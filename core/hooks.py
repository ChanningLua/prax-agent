"""Declarative hook system for workflow automation.

Provides PreTool/PostTool hooks with pattern matching and file-based loading.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from ..tools.base import Tool, ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class HookMatcher:
    """Matcher for conditional hook execution."""
    tool_name: str | None = None  # Match specific tool name
    tool_pattern: str | None = None  # Regex pattern for tool names
    file_pattern: str | None = None  # Glob pattern for file paths
    event_type: str | None = None  # "PreTool", "PostTool", "PreModel", "PostModel", "OnComplete", "OnError"

    # Valid lifecycle event types (not tool-related)
    _LIFECYCLE_EVENTS: frozenset[str] = frozenset({
        "PreModel", "PostModel", "OnComplete", "OnError",
        "SessionStart", "Stop", "PreCompact",
        "PreToolUse", "PostToolUse",
    })

    def matches(self, tool_call: ToolCall, tool: Tool | None, event: str) -> bool:
        """Check if this matcher matches the given tool call and event."""
        # Check event type
        if self.event_type and self.event_type != event:
            return False

        # Check tool name (exact match)
        if self.tool_name and tool_call.name != self.tool_name:
            return False

        # Check tool pattern (regex)
        if self.tool_pattern:
            if not re.match(self.tool_pattern, tool_call.name):
                return False

        # Check file pattern (for file-related tools)
        if self.file_pattern:
            file_path = self._extract_file_path(tool_call)
            if not file_path:
                return False
            if not Path(file_path).match(self.file_pattern):
                return False

        return True

    def _extract_file_path(self, tool_call: ToolCall) -> str | None:
        """Extract file path from tool call parameters."""
        params = tool_call.input
        # Common parameter names for file paths
        for key in ["file_path", "path", "filepath"]:
            if key in params:
                value = params[key]
                if isinstance(value, str):
                    return value
        return None

    def matches_lifecycle(self, event: str, context: dict) -> bool:
        """Check if this matcher matches a lifecycle event (non-tool events)."""
        if self.event_type != event:
            return False
        return True


@dataclass
class Hook:
    """A single hook definition."""
    name: str
    matcher: HookMatcher
    handler: Callable[[ToolCall, Tool | None], Awaitable[ToolResult | None]]
    enabled: bool = True
    priority: int = 0  # Higher priority runs first
    lifecycle_handler: Callable[[dict], Awaitable[None]] | None = None


@dataclass
class HookConfig:
    """Configuration for a hook loaded from file."""
    name: str
    matcher: dict[str, Any]
    command: str | None = None
    timeout: int = 30000  # milliseconds
    enabled: bool = True
    priority: int = 0


class HookRegistry:
    """Registry for managing hooks."""

    def __init__(self):
        self._hooks: list[Hook] = []
        self._file_mtimes: dict[str, float] = {}  # config_path → last mtime

    def register(self, hook: Hook) -> None:
        """Register a hook."""
        self._hooks.append(hook)
        # Sort by priority (descending)
        self._hooks.sort(key=lambda h: h.priority, reverse=True)

    def unregister(self, name: str) -> None:
        """Unregister a hook by name."""
        self._hooks = [h for h in self._hooks if h.name != name]

    def get_matching_hooks(
        self,
        tool_call: ToolCall,
        tool: Tool | None,
        event: str
    ) -> list[Hook]:
        """Get all hooks that match the given tool call and event."""
        return [
            hook for hook in self._hooks
            if hook.enabled and hook.matcher.matches(tool_call, tool, event)
        ]

    async def execute_hooks(
        self,
        tool_call: ToolCall,
        tool: Tool | None,
        event: str
    ) -> ToolResult | None:
        """Execute all matching hooks for the given tool call and event.

        Returns:
            ToolResult if any hook returns a result (short-circuits execution)
            None if all hooks pass through
        """
        matching_hooks = self.get_matching_hooks(tool_call, tool, event)

        for hook in matching_hooks:
            try:
                result = await hook.handler(tool_call, tool)
                if result is not None:
                    logger.info(f"Hook '{hook.name}' returned result, short-circuiting")
                    return result
            except Exception as e:
                logger.error(f"Hook '{hook.name}' failed: {e}")
                # Continue to next hook on error

        return None

    async def execute_lifecycle_hooks(self, event: str, context: dict) -> None:
        """Execute all lifecycle hooks matching the given event.

        Lifecycle hooks (PreModel, PostModel, OnComplete, OnError) receive
        a context dict with event-specific metadata.
        """
        for hook in self._hooks:
            if not hook.enabled:
                continue
            if not hook.matcher.matches_lifecycle(event, context):
                continue
            try:
                if hook.lifecycle_handler is not None:
                    await hook.lifecycle_handler(context)
                elif hook.matcher.event_type == event:
                    # Fall back to command handler for config-loaded hooks
                    await self._run_lifecycle_command_hook(hook, event, context)
            except Exception as e:
                logger.error(f"Lifecycle hook '{hook.name}' failed: {e}")

    async def _run_lifecycle_command_hook(
        self, hook: Hook, event: str, context: dict
    ) -> None:
        """Run a command-based hook for lifecycle events, injecting env vars."""
        import asyncio
        import os

        # Build env with context variables
        env = os.environ.copy()
        for k, v in context.items():
            env[f"PRAX_{k.upper()}"] = str(v)
        env["PRAX_EVENT"] = event

        # Find the command from the config (stored in hook's handler closure or name)
        # Config-loaded lifecycle hooks store their command via a special handler
        if not hasattr(hook, "_command"):
            return
        command: str = hook._command  # type: ignore[attr-defined]
        timeout: int = getattr(hook, "_timeout", 30000)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                await asyncio.wait_for(process.communicate(), timeout=timeout / 1000.0)
            except asyncio.TimeoutError:
                process.kill()
                logger.warning(f"Lifecycle hook '{hook.name}' timed out after {timeout}ms")
        except Exception as e:
            logger.error(f"Lifecycle hook '{hook.name}' command error: {e}")

    def load_from_file(self, config_path: Path) -> None:
        """Load hooks from a JSON configuration file.

        Skips reload if the file's mtime hasn't changed since last load.
        """
        if not config_path.exists():
            logger.debug(f"Hook config file not found: {config_path}")
            return

        path_str = str(config_path)
        try:
            current_mtime = config_path.stat().st_mtime
        except OSError:
            return

        if self._file_mtimes.get(path_str) == current_mtime:
            return  # 文件未变更，跳过

        # 移除旧的同文件 hooks，重新加载
        self._hooks = [h for h in self._hooks if getattr(h, "_source_file", None) != path_str]

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            hooks_data = data.get("hooks", [])
            for hook_data in hooks_data:
                hook = self._register_from_config(hook_data)
                if hook is not None:
                    hook._source_file = path_str  # type: ignore[attr-defined]

            self._file_mtimes[path_str] = current_mtime
            logger.info(f"Loaded {len(hooks_data)} hooks from {config_path}")

        except Exception as e:
            logger.error(f"Failed to load hooks from {config_path}: {e}")

    def _register_from_config(self, config: dict[str, Any]) -> Hook | None:
        """Register a hook from configuration data. Returns the Hook or None."""
        name = config.get("name", "unnamed")
        matcher_data = config.get("matcher", {})
        command = config.get("command")
        timeout = config.get("timeout", 30000)
        enabled = config.get("enabled", True)
        priority = config.get("priority", 0)

        # Create matcher
        matcher = HookMatcher(
            tool_name=matcher_data.get("tool_name"),
            tool_pattern=matcher_data.get("tool_pattern"),
            file_pattern=matcher_data.get("file_pattern"),
            event_type=matcher_data.get("event_type")
        )

        # Create handler
        if command:
            handler = self._create_command_handler(command, timeout)
        else:
            logger.warning(f"Hook '{name}' has no command, skipping")
            return None

        # Register hook
        hook = Hook(
            name=name,
            matcher=matcher,
            handler=handler,
            enabled=enabled,
            priority=priority
        )
        # Attach command metadata for lifecycle hook execution
        hook._command = command  # type: ignore[attr-defined]
        hook._timeout = timeout  # type: ignore[attr-defined]
        self.register(hook)
        return hook

    def _create_command_handler(
        self,
        command: str,
        timeout: int
    ) -> Callable[[ToolCall, Tool | None], Awaitable[ToolResult | None]]:
        """Create a handler that executes a shell command."""
        async def handler(tool_call: ToolCall, tool: Tool | None) -> ToolResult | None:
            import asyncio
            import subprocess

            try:
                # Execute command with timeout
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout / 1000.0
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    return ToolResult(
                        content=f"Hook command timed out after {timeout}ms: {command}",
                        is_error=True
                    )

                # Check exit code
                if process.returncode != 0:
                    error_msg = stderr.decode('utf-8', errors='replace')
                    return ToolResult(
                        content=f"Hook command failed (exit {process.returncode}): {command}\n{error_msg}",
                        is_error=True
                    )

                # Success - return None to continue normal execution
                return None

            except Exception as e:
                return ToolResult(
                    content=f"Hook command error: {e}",
                    is_error=True
                )

        return handler

    def load_from_claude_settings(self, cwd: str) -> None:
        """Load hooks from .claude/settings.json (Claude CLI standard format).

        Expected format:
        {
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "bash ..."}]}],
                "PreToolUse": [{"matcher": "Edit|Write|Bash", "hooks": [{"type": "command", "command": "bash ..."}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "bash ..."}]}]
            }
        }
        """
        settings_path = Path(cwd) / ".claude" / "settings.json"
        if not settings_path.exists():
            return

        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            hooks_data = data.get("hooks", {})
            if not isinstance(hooks_data, dict):
                return

            for event_type, event_hooks in hooks_data.items():
                if not isinstance(event_hooks, list):
                    continue
                for entry in event_hooks:
                    if not isinstance(entry, dict):
                        continue
                    tool_pattern = entry.get("matcher")
                    inner_hooks = entry.get("hooks", [])
                    for i, hook_def in enumerate(inner_hooks):
                        if hook_def.get("type") != "command":
                            continue
                        command = hook_def.get("command")
                        if not command:
                            continue
                        raw_timeout = hook_def.get("timeout", 30)
                        # settings.json timeout is in seconds (Claude CLI convention);
                        # prax internally uses milliseconds.
                        timeout = raw_timeout * 1000 if raw_timeout <= 600 else raw_timeout
                        hook_name = f"claude_settings_{event_type}_{i}"

                        # Lifecycle events vs tool events
                        if event_type in ("SessionStart", "Stop", "PreCompact", "OnComplete", "OnError"):
                            matcher = HookMatcher(event_type=event_type)
                            handler = self._create_command_handler(command, timeout)
                            hook = Hook(name=hook_name, matcher=matcher, handler=handler)
                            hook._command = command  # type: ignore[attr-defined]
                            hook._timeout = timeout  # type: ignore[attr-defined]
                            self.register(hook)
                        else:
                            # PreToolUse / PostToolUse with optional tool pattern
                            matcher = HookMatcher(
                                event_type=event_type,
                                tool_pattern=tool_pattern,
                            )
                            handler = self._create_command_handler(command, timeout)
                            hook = Hook(name=hook_name, matcher=matcher, handler=handler)
                            hook._command = command  # type: ignore[attr-defined]
                            hook._timeout = timeout  # type: ignore[attr-defined]
                            self.register(hook)

            logger.info("Loaded hooks from %s", settings_path)
        except Exception as e:
            logger.error("Failed to load hooks from %s: %s", settings_path, e)


# Global hook registry
_global_registry: HookRegistry | None = None


def get_hook_registry() -> HookRegistry:
    """Get the global hook registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = HookRegistry()
    return _global_registry


def load_hooks_from_directory(hooks_dir: Path) -> None:
    """Load all hook configuration files from a directory.

    Looks for .json files in the hooks directory.
    """
    if not hooks_dir.exists() or not hooks_dir.is_dir():
        logger.debug(f"Hooks directory not found: {hooks_dir}")
        return

    registry = get_hook_registry()

    for config_file in hooks_dir.glob("*.json"):
        registry.load_from_file(config_file)
