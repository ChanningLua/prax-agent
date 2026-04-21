"""Runtime middleware chain for Prax orchestration layer.

Implements Deep Agents-style middleware pattern with:
- LoopDetectionMiddleware: prevent infinite tool call loops
- TodoReminderMiddleware: keep todo list visible across context
- RunBoundaryReminderMiddleware: remind the model to treat the current repo state as source of truth
- VerificationGuidanceMiddleware: turn verification results into focused repair/finish reminders
- ContextInjectMiddleware: inject OpenViking context into prompts
- MemoryExtractionMiddleware: extract facts to OpenViking (see memory_middleware.py)
- ModelFallbackMiddleware: Claude → GPT → GLM fallback chain
- PromptCacheMiddleware: Anthropic prompt caching optimization
- QualityGateMiddleware: auto quality gate loop after code modifications
- EvaluatorMiddleware: evaluator-optimizer loop for weak model output quality
- ChangeTracker: single writer of code-change / verification state into RuntimeState.metadata
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

from .todo_store import TodoStore
from ..tools.base import Tool, ToolCall, ToolResult
from ..tools.verify_command import is_verify_command
from .llm_client import LLMResponse

if TYPE_CHECKING:
    from .context import Context

logger = logging.getLogger(__name__)


# 标准优先级常量（数值越小越先执行）
PRIORITY_GUARD = 10    # 安全/循环检测类
PRIORITY_CACHE = 20    # 缓存类
PRIORITY_INJECT = 50   # 上下文注入类
PRIORITY_EXTRACT = 90  # 信息提取类
PRIORITY_EVAL = 95     # 评估/质量门类


# Shared across every middleware that needs to know "did the agent just change code?"
# Keep in sync with the real editing tools in prax/tools/.
CODE_MODIFYING_TOOLS: frozenset[str] = frozenset({
    "Write", "Edit", "MultiEdit",
    "HashlineEdit", "AstGrepReplace", "ApplyPatch",
})

# Key used inside RuntimeState.metadata by ChangeTracker.
CHANGE_TRACKER_KEY = "change_tracker"


def _default_tracker_state() -> dict[str, Any]:
    return {
        "code_gen": 0,            # incremented on each successful code-modifying tool call
        "verified_gen": 0,        # code_gen value at the last passing verification
        "last_verify_ok": False,  # True after a verify attempt passes, False after a failure
        "last_verify_error": None,  # trimmed failure output from the most recent failing verify
    }


def _is_verify_attempt(tool_call: "ToolCall") -> bool:
    """Return True iff the tool call runs a repo-local verification command.

    Uses tools.verify_command.is_verify_command as the single source of truth for
    "what counts as a verification command" so every guard stays in lockstep with
    the VerifyCommandTool allowlist (pytest, python -m pytest, npm/pnpm test,
    cargo test, go test).
    """
    if tool_call.name == "VerifyCommand":
        return True
    if tool_call.name in ("Bash", "SandboxBash"):
        command = str(tool_call.input.get("command", "")).strip()
        return bool(command) and is_verify_command(command)
    return False


def _get_tracker(state: "RuntimeState") -> dict[str, Any]:
    """Read the shared tracker state. Always returns a dict (never None)."""
    tracker = state.metadata.get(CHANGE_TRACKER_KEY)
    if not isinstance(tracker, dict):
        tracker = _default_tracker_state()
        state.metadata[CHANGE_TRACKER_KEY] = tracker
    return tracker


@dataclass
class RuntimeState:
    messages: list[dict]
    context: "Context"
    iteration: int
    tool_loop_counts: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentMiddleware:
    """Base middleware class. Override hooks as needed."""

    priority: int = 100  # 执行优先级，数值越小越先执行

    async def before_model(self, state: RuntimeState) -> None:
        return None

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        return response

    async def before_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        return None

    async def after_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
        result: ToolResult,
    ) -> ToolResult:
        return result


class PermissionMiddleware(AgentMiddleware):
    """Enforce ExecutionPolicy on tool calls."""

    priority: int = 10  # run early

    def __init__(self, policy: Any) -> None:
        from .permissions import ExecutionPolicy
        self.policy: ExecutionPolicy = policy

    async def before_tool(
        self,
        state: "RuntimeState",
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        from ..tools.base import PermissionLevel
        level = tool.permission_level if tool else PermissionLevel.SAFE
        decision = self.policy.authorize_tool(tool_call.name, level)
        if not decision.allowed:
            return ToolResult(content=f"Permission denied: {decision.reason}", is_error=True)
        return None


class LoopDetectionMiddleware(AgentMiddleware):
    """Detect and break repeated identical tool call sequences."""

    READONLY_TOOLS = frozenset({
        "Read", "Glob", "Grep", "HashlineRead", "WebFetch", "WebSearch",
        "Bash", "LS",
    })

    def __init__(self, hard_limit: int = 5):
        self._hard_limit = hard_limit

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        if not response.has_tool_calls:
            state.tool_loop_counts.clear()
            return response

        # Filter out read-only tools from loop detection
        write_calls = [tc for tc in response.tool_calls if tc.name not in self.READONLY_TOOLS]
        if not write_calls:
            return response

        blob = json.dumps(
            [{"name": tc.name, "input": tc.input} for tc in write_calls],
            sort_keys=True,
            ensure_ascii=False,
        )
        call_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
        count = state.tool_loop_counts.get(call_hash, 0) + 1
        state.tool_loop_counts[call_hash] = count

        if count < self._hard_limit:
            return response

        return LLMResponse(
            content=[{
                "type": "text",
                "text": (
                    "[Prax] Repeated tool calls hit the safety limit. "
                    "Summarize progress so far and stop."
                ),
            }],
            stop_reason="safety_stop",
        )


class TodoReminderMiddleware(AgentMiddleware):
    """Re-inject todo list when it scrolls out of context window."""

    def __init__(self, *, cwd: str):
        self._store = TodoStore(cwd)

    async def before_model(self, state: RuntimeState) -> None:
        todos = self._store.load()
        if not todos:
            return

        if any(
            isinstance(message.get("content"), str)
            and "todo list from earlier" in str(message.get("content"))
            for message in state.messages[-3:]
        ):
            return

        if any(
            isinstance(message.get("content"), list)
            and any(
                block.get("type") == "tool_use" and block.get("name") == "TodoWrite"
                for block in message["content"]
            )
            for message in state.messages[-6:]
            if isinstance(message, dict)
        ):
            return

        formatted = "\n".join(f"- [{todo.status}] {todo.content}" for todo in todos)
        state.messages.append({
            "role": "user",
            "name": "todo_reminder",
            "content": (
                "<system_reminder>\n"
                "Your todo list from earlier is no longer visible in the current context window, "
                "but it is still active. Here is the current state:\n\n"
                f"{formatted}\n\n"
                "Continue tracking and updating this todo list as you work. "
                "Call `TodoWrite` whenever the status of any item changes.\n"
                "</system_reminder>"
            ),
        })


class RunBoundaryReminderMiddleware(AgentMiddleware):
    """Inject a one-time reminder for the current task/run boundary."""

    priority: int = 55

    def __init__(self) -> None:
        self._injected = False

    async def before_model(self, state: RuntimeState) -> None:
        if self._injected or state.iteration != 0:
            return
        self._injected = True
        state.messages.append({
            "role": "user",
            "name": "run_boundary",
            "content": (
                "<run_boundary>\n"
                "Treat the current workspace and the latest verification output as the source of truth. "
                "Do not rely on file details from earlier tasks in this session. "
                "For repository-fix work, reproduce or rerun the current verification command, inspect the current file contents, "
                "apply a minimal edit, and rerun verification before finishing.\n"
                "</run_boundary>"
            ),
        })


class ChangeTracker(AgentMiddleware):
    """Single writer of code-change and verification state.

    All guard middlewares should read ``state.metadata[CHANGE_TRACKER_KEY]`` instead
    of keeping their own ``_code_generation`` / ``_verified_generation`` counters.
    Keeping exactly one writer avoids the divergent ``CODE_MODIFYING_TOOLS`` sets
    and the "is this a verify attempt" heuristics that used to drift between
    middlewares.
    """

    priority: int = 5  # run before every guard so they see up-to-date state

    def __init__(self, *, max_failure_output_chars: int = 1600):
        self._max_failure_output_chars = max_failure_output_chars

    def _trim(self, text: str) -> str:
        text = (text or "").strip()
        if len(text) <= self._max_failure_output_chars:
            return text
        return text[: self._max_failure_output_chars].rstrip() + "\n...[truncated]"

    async def after_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
        result: ToolResult,
    ) -> ToolResult:
        tracker = _get_tracker(state)

        if tool_call.name in CODE_MODIFYING_TOOLS and not result.is_error:
            tracker["code_gen"] += 1

        if _is_verify_attempt(tool_call):
            if result.is_error:
                tracker["last_verify_ok"] = False
                tracker["last_verify_error"] = self._trim(result.content)
            else:
                tracker["last_verify_ok"] = True
                tracker["last_verify_error"] = None
                tracker["verified_gen"] = tracker["code_gen"]

        return result


class VerificationGuidanceMiddleware(AgentMiddleware):
    """Inject focused guidance after verification failures or successes.

    Reads shared state from ``ChangeTracker`` via ``state.metadata``; no longer
    maintains its own ``_code_generation`` counter.
    """

    priority: int = 60

    def __init__(self) -> None:
        self._last_injected_failure_key: tuple[int, str | None] | None = None
        self._last_injected_success_verified_gen: int = -1

    async def before_model(self, state: RuntimeState) -> None:
        tracker = _get_tracker(state)
        code_gen = tracker["code_gen"]
        verified_gen = tracker["verified_gen"]
        last_ok = tracker["last_verify_ok"]
        last_error = tracker["last_verify_error"]

        # Success path: code_gen is caught up with a passing verify.
        if last_ok and verified_gen >= code_gen and verified_gen > self._last_injected_success_verified_gen:
            state.messages.append({
                "role": "user",
                "name": "verification_success",
                "content": (
                    "<verification_success>\n"
                    "The latest verification command passed. Summarize the fix and stop unless the user explicitly asked for more work.\n"
                    "</verification_success>"
                ),
            })
            self._last_injected_success_verified_gen = verified_gen
            return

        # Failure path: most recent verify failed and we haven't injected guidance
        # for that (code_gen, error) pair yet.
        if last_error is None:
            return
        failure_key = (code_gen, last_error)
        if failure_key == self._last_injected_failure_key:
            return

        if code_gen > verified_gen:
            guidance = (
                "You have already changed code since the last failed verification. "
                "Rerun VerifyCommand now before doing more exploration or delegation."
            )
        else:
            guidance = (
                "The latest verification command failed. Focus on fixing the failure output below. "
                "Inspect the most relevant source file, apply a minimal edit, then rerun VerifyCommand. "
                "Do not delegate or switch models just to rerun tests."
            )

        state.messages.append({
            "role": "user",
            "name": "verification_feedback",
            "content": (
                "<verification_feedback>\n"
                f"{guidance}\n\n"
                "Failure output:\n"
                f"{last_error}\n"
                "</verification_feedback>"
            ),
        })
        self._last_injected_failure_key = failure_key


_DESIGN_RESTORATION_GUARD_TOOL_NAME = "__design_restoration_guard__"


class DesignRestorationGuardMiddleware(AgentMiddleware):
    """Block completion for design-restoration work until screenshot verification runs.

    Uses the shared ``ChangeTracker`` for code-change bookkeeping; only screenshot
    verification scripts count as verification for this specialized guard, so it
    keeps its own ``_verified_generation`` counter driven by ``_VERIFY_HINTS``.
    """

    priority: int = 62
    _VERIFY_HINTS = (
        "verify-html-rendering.js",
        "compare-screenshots.js",
        "screenshot-prototype.js",
    )

    def __init__(self, max_retries: int = 3):
        self._max_retries = max_retries
        self._retry_count = 0
        self._verified_generation = 0
        self._task_detection: bool | None = None

    def _message_text(self, message: dict[str, Any]) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return str(content)

    def _is_restoration_task(self, state: RuntimeState) -> bool:
        if self._task_detection is not None:
            return self._task_detection

        user_text = "\n".join(
            self._message_text(message)
            for message in state.messages
            if message.get("role") == "user"
        ).lower()

        has_design_source = ("mastergo" in user_text) or ("figma" in user_text) or ("设计稿" in user_text)
        has_restoration_goal = (
            ("还原" in user_text)
            or ("截图对比" in user_text)
            or ("视觉还原" in user_text)
            or ("html 原型" in user_text)
            or ("html prototype" in user_text)
        )
        self._task_detection = has_design_source or has_restoration_goal
        return self._task_detection

    def _is_screenshot_verify(self, tool_call: ToolCall) -> bool:
        if tool_call.name not in ("VerifyCommand", "SandboxBash", "Bash"):
            return False
        command = str(tool_call.input.get("command", "")).strip()
        return any(hint in command for hint in self._VERIFY_HINTS)

    async def after_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
        result: ToolResult,
    ) -> ToolResult:
        if self._is_screenshot_verify(tool_call) and not result.is_error:
            tracker = _get_tracker(state)
            self._verified_generation = tracker["code_gen"]
            self._retry_count = 0
        return result

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        if response.has_tool_calls:
            return response
        if not self._is_restoration_task(state):
            return response
        tracker = _get_tracker(state)
        code_generation = tracker["code_gen"]
        if code_generation == 0 or self._verified_generation >= code_generation:
            self._retry_count = 0
            return response
        if self._retry_count >= self._max_retries:
            return response

        self._retry_count += 1
        feedback = (
            "This task looks like design restoration work (MasterGo/Figma/visual parity), "
            "and code changed since the last screenshot verification. "
            "Run the project's screenshot verification flow before finishing. "
            "Do not claim the page is restored until you have produced comparison screenshots and a quantitative report."
        )
        return LLMResponse(
            content=[
                {"type": "text", "text": response.text},
                {
                    "type": "tool_use",
                    "id": "design-guard-0",
                    "name": _DESIGN_RESTORATION_GUARD_TOOL_NAME,
                    "input": {"feedback": feedback},
                },
            ],
            stop_reason="design_verification_retry",
        )

    async def before_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        if tool_call.name != _DESIGN_RESTORATION_GUARD_TOOL_NAME:
            return None
        feedback = str(tool_call.input.get("feedback", "")).strip()
        return ToolResult(
            content=f"<design_restoration_guard>\n{feedback}\n</design_restoration_guard>",
            is_error=False,
        )


class ContextInjectMiddleware(AgentMiddleware):
    """Inject memory backend context and skills into the conversation.

    Scans .prax/skills/ for skill definitions and injects them
    into the first user message of each session.

    Accepts either a MemoryBackend (preferred) or legacy OpenVikingClient.
    """

    def __init__(self, *, cwd: str, openviking: Any = None, memory_backend: Any = None):
        self._cwd = cwd
        self._openviking = openviking
        self._memory_backend = memory_backend
        self._injected = False

    async def before_model(self, state: RuntimeState) -> None:
        if self._injected:
            return
        self._injected = True

        # MemoryBackend path (preferred)
        if self._memory_backend is not None:
            try:
                task_type = state.metadata.get("task_type", "general")
                memory_text = await self._memory_backend.format_for_prompt(
                    self._cwd, task_type=task_type
                )
                if memory_text:
                    state.messages.insert(0, {
                        "role": "user",
                        "name": "context_inject",
                        "content": f"<system_context>\n{memory_text}\n</system_context>",
                    })
            except Exception as e:
                logger.debug("ContextInjectMiddleware MemoryBackend failed: %s", e)
            return

        # Legacy OpenViking path
        if self._openviking is not None and self._openviking.available:
            try:
                task_type = state.metadata.get("task_type", "general")
                experiences = await self._openviking.get_experiences(task_type)
                exp_text = self._openviking.format_experiences_for_prompt(experiences)
                if exp_text:
                    state.messages.insert(0, {
                        "role": "user",
                        "name": "context_inject",
                        "content": f"<system_context>\n{exp_text}\n</system_context>",
                    })
            except Exception as e:
                logger.debug("ContextInjectMiddleware OpenViking failed: %s", e)


class ModelFallbackMiddleware(AgentMiddleware):
    """Track model errors for fallback routing decisions.

    Works with the model upgrade system in main.py to trigger
    Claude → GPT → GLM fallback chain on failures.

    Also reads `detected_intent` from RuntimeState.metadata (set by
    IntentGateMiddleware) to dynamically adjust the next-round model.
    """

    # intent → preferred model
    _INTENT_MODEL_MAP: dict[str, str] = {
        "debugging": "gpt-4.1",
        "deep_research": "gpt-4.1",
        "architecture": "claude-opus-4-6",
        "chinese_content": "glm-4-flash",
        "quick_tasks": "glm-4-flash",
    }

    def __init__(self, fallback_chain: list[str] | None = None):
        self._fallback_chain = fallback_chain or ["claude-opus-4-6", "gpt-4.1", "glm-4-flash"]
        self._error_count = 0

    async def before_model(self, state: RuntimeState) -> None:
        intent = state.metadata.get("detected_intent")
        if intent:
            preferred = self._INTENT_MODEL_MAP.get(intent)
            if preferred:
                state.metadata["dynamic_model_override"] = preferred
                logger.debug("ModelFallback: intent=%s → model=%s", intent, preferred)

    async def after_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
        result: ToolResult,
    ) -> ToolResult:
        if result.is_error:
            self._error_count += 1
            state.metadata["tool_error_count"] = self._error_count
        return result


class PromptCacheMiddleware(AgentMiddleware):
    """Add Anthropic prompt caching headers for long system prompts.

    Marks the system prompt for caching when using Anthropic models,
    reducing latency and cost for repeated similar requests.
    """

    def __init__(self, min_tokens: int = 1024):
        self._min_tokens = min_tokens

    async def before_model(self, state: RuntimeState) -> None:
        # Mark metadata so LLM client can add cache_control headers
        state.metadata["prompt_cache_enabled"] = True
        state.metadata["prompt_cache_min_tokens"] = self._min_tokens


class HookMiddleware(AgentMiddleware):
    """Middleware that integrates the declarative hook system into the agent loop.

    Executes PreToolUse and PostToolUse hooks from the HookRegistry before and after
    tool calls, supporting declarative workflow automation.

    Also executes lifecycle hooks (PreModel, PostModel) around LLM calls.
    """

    def __init__(self, *, hooks_dir: str | None = None, cwd: str | None = None):
        from .hooks import get_hook_registry, load_hooks_from_directory
        from pathlib import Path

        self._registry = get_hook_registry()
        self._cwd = cwd
        self._hooks_dir = Path(hooks_dir) if hooks_dir else None

        # Load hooks from directory if specified
        if hooks_dir:
            load_hooks_from_directory(Path(hooks_dir))

        # Load hooks from .claude/settings.json (Claude CLI standard format)
        if cwd:
            self._registry.load_from_claude_settings(cwd)

    async def before_model(self, state: RuntimeState) -> None:
        # 热重载：每次迭代检查 hooks 目录文件是否变更
        if self._hooks_dir and self._hooks_dir.exists():
            for config_file in self._hooks_dir.glob("*.json"):
                self._registry.load_from_file(config_file)

        await self._registry.execute_lifecycle_hooks(
            "PreModel",
            {"iteration": state.iteration, "message_count": len(state.messages)},
        )

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        await self._registry.execute_lifecycle_hooks(
            "PostModel",
            {
                "iteration": state.iteration,
                "has_tool_calls": int(response.has_tool_calls),
                "usage": str(response.usage or {}),
            },
        )
        return response

    async def before_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        result = await self._registry.execute_hooks(tool_call, tool, "PreToolUse")
        if result is not None and result.is_error:
            logger.warning(f"PreToolUse hook blocked {tool_call.name}: {result.content}")
        return result

    async def after_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
        result: ToolResult,
    ) -> ToolResult:
        hook_result = await self._registry.execute_hooks(tool_call, tool, "PostToolUse")
        if hook_result is not None and hook_result.is_error:
            logger.warning(f"PostTool hook flagged issue for {tool_call.name}: {hook_result.content}")
            # Combine original result with hook warning
            return ToolResult(
                content=f"{result.content}\n\n[Hook Warning] {hook_result.content}",
                is_error=result.is_error
            )
        return result


class QualityGateMiddleware(AgentMiddleware):
    """在代码修改后自动运行质量检查，将失败结果注入对话形成自愈闭环。

    Also supports an opt-in ``require_verify_before_completion`` flag in
    ``.prax/quality-gates.yaml``. When enabled, a final response with no tool
    calls is rejected if ``state.metadata[CHANGE_TRACKER_KEY]`` shows code was
    modified after the last passing verification — replacing the separate
    ``VerificationGuardMiddleware`` that used to duplicate this state machine.
    """

    def __init__(
        self,
        cwd: str,
        commands: list[str] | None = None,
        *,
        require_verify_before_completion: bool | None = None,
        max_require_verify_retries: int = 3,
    ):
        self.cwd = cwd
        gate_commands, completion_checks, cfg_require_verify = self._load_quality_gates()
        self.commands = commands if commands is not None else gate_commands
        self._completion_checks: list[str] = completion_checks
        self._pending_check = False
        self._require_verify = (
            cfg_require_verify if require_verify_before_completion is None
            else bool(require_verify_before_completion)
        )
        self._max_require_verify_retries = max_require_verify_retries
        self._require_verify_retries = 0

    def _load_quality_gates(self) -> tuple[list[str], list[str], bool]:
        config_path = Path(self.cwd) / ".prax" / "quality-gates.yaml"
        if config_path.exists():
            try:
                data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {} if _yaml else {}
                return (
                    data.get("commands", []),
                    data.get("completion_checks", []),
                    bool(data.get("require_verify_before_completion", False)),
                )
            except Exception as e:
                logger.warning("Failed to load quality-gates.yaml: %s", e)
        return [], [], False

    async def after_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
        result: ToolResult,
    ) -> ToolResult:
        if tool_call.name in CODE_MODIFYING_TOOLS and not result.is_error:
            self._pending_check = True
        return result

    def _build_require_verify_response(
        self, response: LLMResponse, tracker: dict[str, Any]
    ) -> LLMResponse:
        self._require_verify_retries += 1
        message = (
            "Code was modified but no passing verification has run since the last edit "
            f"(code_gen={tracker['code_gen']}, verified_gen={tracker['verified_gen']}). "
            "Run a repository-local verification command (e.g. `VerifyCommand` with "
            "`pytest -q`, `npm test`, `cargo test`, or `go test ./...`) before finishing."
        )
        failure_text = (
            "<completion_check_failure>\n"
            + message
            + "\n</completion_check_failure>"
        )
        return LLMResponse(
            content=[
                {"type": "text", "text": response.text},
                {
                    "type": "tool_use",
                    "id": f"completion-check-verify-{self._require_verify_retries}",
                    "name": "__completion_check__",
                    "input": {"failure": failure_text},
                },
            ],
            stop_reason="completion_check_retry",
        )

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        """Run completion_checks when the model produces a final response (no tool calls)."""
        if response.has_tool_calls:
            return response

        # 1) Require-verify gate (opt-in, reuses shared ChangeTracker state).
        if self._require_verify:
            tracker = _get_tracker(state)
            if tracker["code_gen"] > tracker["verified_gen"]:
                if self._require_verify_retries >= self._max_require_verify_retries:
                    logger.warning(
                        "QualityGate require_verify_before_completion exhausted %d retries; letting completion through",
                        self._max_require_verify_retries,
                    )
                else:
                    return self._build_require_verify_response(response, tracker)
            else:
                self._require_verify_retries = 0

        if not self._completion_checks:
            return response

        failures = []
        for cmd in self._completion_checks:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=self.cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                except asyncio.TimeoutError:
                    proc.kill()
                    failures.append(f"[{cmd}] timed out after 60s")
                    continue
                if proc.returncode != 0:
                    output = (stdout + stderr).decode("utf-8", errors="replace")[:2000]
                    failures.append(f"[{cmd}] failed (exit {proc.returncode}):\n{output}")
            except Exception as e:
                logger.warning("Completion check command error (%s): %s", cmd, e)

        if not failures:
            return response

        failure_text = (
            "<completion_check_failure>\n"
            "以下完成验证失败，任务尚未完成，请继续修复：\n\n"
            + "\n\n".join(failures)
            + "\n</completion_check_failure>"
        )
        # Inject as synthetic tool call so agent loop continues
        _COMPLETION_CHECK_TOOL_ID = "completion-check-0"
        return LLMResponse(
            content=[
                {"type": "text", "text": response.text},
                {
                    "type": "tool_use",
                    "id": _COMPLETION_CHECK_TOOL_ID,
                    "name": "__completion_check__",
                    "input": {"failure": failure_text},
                },
            ],
            stop_reason="completion_check_retry",
        )

    async def before_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        if tool_call.name == "__completion_check__":
            return ToolResult(
                content=tool_call.input.get("failure", ""),
                is_error=False,
            )
        return None

    async def before_model(self, state: RuntimeState) -> None:
        if not self._pending_check or not self.commands:
            return
        self._pending_check = False

        failures = []
        for cmd in self.commands:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=self.cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                except asyncio.TimeoutError:
                    proc.kill()
                    failures.append(f"[{cmd}] timed out after 60s")
                    continue
                if proc.returncode != 0:
                    output = (stdout + stderr).decode("utf-8", errors="replace")[:2000]
                    failures.append(f"[{cmd}] failed (exit {proc.returncode}):\n{output}")
            except Exception as e:
                logger.warning("Quality gate command error (%s): %s", cmd, e)

        if failures:
            state.messages.append({
                "role": "user",
                "name": "quality_gate",
                "content": (
                    "<quality_gate_failure>\n"
                    "以下质量检查失败，请修复后继续：\n\n"
                    + "\n\n".join(failures)
                    + "\n</quality_gate_failure>"
                ),
            })


# Sentinel tool name used by EvaluatorMiddleware to inject feedback into the loop.
_EVALUATOR_TOOL_NAME = "__evaluator_feedback__"


class EvaluatorMiddleware(AgentMiddleware):
    """Evaluator-Optimizer loop: evaluate final responses and retry if criteria unmet.

    When the model produces a text response (no tool calls), runs rule-based
    evaluation criteria against it. If issues are found, injects structured
    feedback and forces another LLM call by returning a synthetic tool_use
    response. The feedback is surfaced to the model as a tool result so it
    understands what to fix.

    Configured via .prax/evaluator.yaml:
        max_retries: 2
        criteria:
          - "响应必须包含具体的文件路径"
          - "代码修改必须引用具体行号"
    """

    _TOOL_ID = "eval-feedback-0"

    def __init__(self, cwd: str, max_retries: int = 2):
        self.cwd = cwd
        self.max_retries = max_retries
        self._criteria: list[str] = self._load_criteria()
        self._retry_count: int = 0

    def _load_criteria(self) -> list[str]:
        config_path = Path(self.cwd) / ".prax" / "evaluator.yaml"
        if not config_path.exists():
            return []
        try:
            data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {} if _yaml else {}
            return [str(c) for c in data.get("criteria", [])]
        except Exception as e:
            logger.warning("Failed to load evaluator.yaml: %s", e)
            return []

    def _evaluate(self, text: str) -> list[str]:
        """Return list of unmet criteria (empty = pass)."""
        issues = []
        for criterion in self._criteria:
            # Simple keyword-presence heuristics per criterion type
            if "文件路径" in criterion and "/" not in text and "\\" not in text:
                issues.append(f"未满足：{criterion}")
            elif "行号" in criterion and not any(
                word in text for word in ["line", "行", "L", ":"]
            ):
                issues.append(f"未满足：{criterion}")
            # Generic: criterion is a substring check instruction — skip if no match keyword
        return issues

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        # Only evaluate pure text responses (final answers, not tool calls)
        if response.has_tool_calls:
            return response
        if not self._criteria:
            return response
        if self._retry_count >= self.max_retries:
            self._retry_count = 0
            return response

        issues = self._evaluate(response.text)
        if not issues:
            self._retry_count = 0
            return response

        self._retry_count += 1
        feedback = (
            "以下评估标准未满足，请修正后重新回答：\n\n"
            + "\n".join(f"- {issue}" for issue in issues)
        )
        logger.info(
            "EvaluatorMiddleware: retry %d/%d, issues=%d",
            self._retry_count, self.max_retries, len(issues),
        )

        # Return a synthetic tool_use response so the agent loop continues.
        # EvaluatorMiddleware.before_tool() will intercept this sentinel call
        # and return the feedback as a clean tool result.
        return LLMResponse(
            content=[
                {"type": "text", "text": response.text},
                {
                    "type": "tool_use",
                    "id": self._TOOL_ID,
                    "name": _EVALUATOR_TOOL_NAME,
                    "input": {"feedback": feedback},
                },
            ],
            stop_reason="evaluator_retry",
        )

    async def before_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        if tool_call.name != _EVALUATOR_TOOL_NAME:
            return None
        feedback = tool_call.input.get("feedback", "")
        return ToolResult(
            content=(
                "<evaluator_feedback>\n"
                + feedback
                + "\n</evaluator_feedback>"
            ),
            is_error=False,
        )


