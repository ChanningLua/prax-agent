"""SandboxBash tool — execute shell commands via the sandbox layer.

Provides the same interface as TmuxBashTool but routes execution through
the configured SandboxProvider (local or Docker), enabling isolation
without changing the agent's tool-calling interface.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .base import PermissionLevel, Tool, ToolResult
from ..core.sandbox import get_sandbox_provider
from .verify_command import VerifyCommandTool, is_verify_command

DESCRIPTION = """\
Execute a shell command in an isolated sandbox environment.

The sandbox backend is selected automatically:
  - Docker container (if Docker daemon is available)
  - Local host execution (fallback for trusted dev environments)

Use this tool for one-shot commands that need isolation.
For persistent interactive sessions (TUI apps), use InteractiveBash instead.

Examples:
  {"command": "python3 --version"}
  {"command": "pip install requests && python3 -c 'import requests; print(requests.__version__)'"}
  {"command": "ls -la /workspace", "timeout": 10}
"""


class SandboxBashTool(Tool):
    """Execute commands via the configured sandbox provider."""

    name = "SandboxBash"
    description = DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute in the sandbox",
            },
            "sandbox_id": {
                "type": "string",
                "description": "Reuse an existing sandbox by ID (optional). If omitted, a new sandbox is acquired.",
            },
            "timeout": {
                "type": "integer",
                "description": "Command timeout in seconds (default: 60)",
                "default": 60,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DANGEROUS

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd
        self._provider = get_sandbox_provider(cwd=cwd)
        # Default persistent sandbox per tool instance
        self._default_sid: str | None = None

    def required_permission(self, params: dict[str, Any]) -> PermissionLevel:
        command = str(params.get("command", "")).strip()
        if command and is_verify_command(command):
            return PermissionLevel.REVIEW
        return self.permission_level

    def _get_or_create_sandbox(self, sandbox_id: str | None):
        if sandbox_id:
            sb = self._provider.get(sandbox_id)
            if sb is None:
                # Acquire with the requested ID
                self._provider.acquire(sandbox_id)
                sb = self._provider.get(sandbox_id)
            return sb

        # Use the tool's default sandbox (created once, reused)
        if self._default_sid is None:
            self._default_sid = self._provider.acquire()
        sb = self._provider.get(self._default_sid)
        if sb is None:
            # Sandbox was released externally — recreate
            self._default_sid = self._provider.acquire()
            sb = self._provider.get(self._default_sid)
        return sb

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        command = params.get("command", "").strip()
        if not command:
            return ToolResult(content="Error: 'command' is required", is_error=True)

        sandbox_id = params.get("sandbox_id")
        timeout = int(params.get("timeout", 60))

        if is_verify_command(command):
            return await VerifyCommandTool(cwd=self._cwd or ".").execute(
                {"command": command, "timeout": timeout}
            )

        try:
            sandbox = self._get_or_create_sandbox(sandbox_id)
            if sandbox is None:
                return ToolResult(
                    content="Error: failed to acquire sandbox",
                    is_error=True,
                )

            # Run blocking sandbox call in thread pool
            loop = asyncio.get_event_loop()
            sr = await loop.run_in_executor(
                None, lambda: sandbox.execute_command_v2(command, timeout=timeout)
            )
            is_error = sr.exit_code != 0 or sr.timed_out
            content = sr.output
            if sr.exit_code != 0 and not sr.timed_out:
                content += f"\nExit code: {sr.exit_code}"
            return ToolResult(content=content, is_error=is_error)

        except Exception as exc:
            return ToolResult(content=f"SandboxBash error: {exc}", is_error=True)

    def release(self) -> None:
        """Release the default sandbox (call on cleanup)."""
        if self._default_sid is not None:
            self._provider.release(self._default_sid)
            self._default_sid = None
