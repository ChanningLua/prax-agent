"""Comprehensive tests for all middleware classes in prax/core/middleware.py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from prax.core.context import Context
from prax.core.llm_client import LLMResponse
from prax.core.middleware import (
    ChangeTracker,
    ContextInjectMiddleware,
    DesignRestorationGuardMiddleware,
    EvaluatorMiddleware,
    HookMiddleware,
    LoopDetectionMiddleware,
    ModelFallbackMiddleware,
    PermissionMiddleware,
    PromptCacheMiddleware,
    QualityGateMiddleware,
    RunBoundaryReminderMiddleware,
    RuntimeState,
    TodoReminderMiddleware,
    VerificationGuidanceMiddleware,
)
from prax.tools.base import PermissionLevel, Tool, ToolCall, ToolResult


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_context():
    ctx = MagicMock(spec=Context)
    ctx.cwd = "/test/cwd"
    return ctx


@pytest.fixture
def runtime_state(mock_context):
    return RuntimeState(
        messages=[],
        context=mock_context,
        iteration=1,
        tool_loop_counts={},
        metadata={},
    )


@pytest.fixture
def mock_tool():
    tool = MagicMock(spec=Tool)
    tool.name = "TestTool"
    tool.permission_level = PermissionLevel.SAFE
    tool.required_permission = MagicMock(return_value=PermissionLevel.SAFE)
    return tool


# ── PermissionMiddleware ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_middleware_blocks_dangerous_tool(runtime_state):
    from prax.core.permissions import AuthDecision, ExecutionPolicy

    policy = MagicMock(spec=ExecutionPolicy)
    policy.authorize_tool = MagicMock(
        return_value=AuthDecision(allowed=False, reason="Dangerous tool")
    )

    middleware = PermissionMiddleware(policy)
    tool = MagicMock(spec=Tool)
    tool.permission_level = PermissionLevel.DANGEROUS
    tool.required_permission = MagicMock(return_value=PermissionLevel.DANGEROUS)

    tool_call = ToolCall(name="Bash", input={"command": "rm -rf /"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is not None
    assert result.is_error
    assert "Permission denied" in result.content


@pytest.mark.asyncio
async def test_permission_middleware_allows_safe_tool(runtime_state):
    from prax.core.permissions import AuthDecision, ExecutionPolicy

    policy = MagicMock(spec=ExecutionPolicy)
    policy.authorize_tool = MagicMock(return_value=AuthDecision(allowed=True))

    middleware = PermissionMiddleware(policy)
    tool = MagicMock(spec=Tool)
    tool.permission_level = PermissionLevel.SAFE
    tool.required_permission = MagicMock(return_value=PermissionLevel.SAFE)

    tool_call = ToolCall(name="Read", input={"file_path": "/test/file.txt"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None


@pytest.mark.asyncio
async def test_permission_middleware_uses_tool_permission_level(runtime_state):
    from prax.core.permissions import AuthDecision, ExecutionPolicy

    policy = MagicMock(spec=ExecutionPolicy)
    policy.authorize_tool = MagicMock(return_value=AuthDecision(allowed=True))

    middleware = PermissionMiddleware(policy)
    tool = MagicMock(spec=Tool)
    tool.permission_level = PermissionLevel.REVIEW
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    tool_call = ToolCall(name="Write", input={"file_path": "/test/file.txt"})
    await middleware.before_tool(runtime_state, tool_call, tool)

    policy.authorize_tool.assert_called_once_with("Write", PermissionLevel.REVIEW)


@pytest.mark.asyncio
async def test_permission_middleware_defaults_to_safe_when_tool_none(runtime_state):
    from prax.core.permissions import AuthDecision, ExecutionPolicy

    policy = MagicMock(spec=ExecutionPolicy)
    policy.authorize_tool = MagicMock(return_value=AuthDecision(allowed=True))

    middleware = PermissionMiddleware(policy)
    tool_call = ToolCall(name="UnknownTool", input={})
    result = await middleware.before_tool(runtime_state, tool_call, None)

    assert result is None
    policy.authorize_tool.assert_called_once_with("UnknownTool", PermissionLevel.SAFE)


# ── LoopDetectionMiddleware ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_detection_clears_counts_when_no_tool_calls(runtime_state):
    middleware = LoopDetectionMiddleware(hard_limit=5)
    runtime_state.tool_loop_counts = {"abc123": 3}

    response = LLMResponse(content=[{"type": "text", "text": "Done"}])
    result = await middleware.after_model(runtime_state, response)

    assert runtime_state.tool_loop_counts == {}
    assert result == response


@pytest.mark.asyncio
async def test_loop_detection_filters_readonly_tools(runtime_state):
    middleware = LoopDetectionMiddleware(hard_limit=5)

    response = LLMResponse(
        content=[
            {"type": "tool_use", "id": "1", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "2", "name": "Grep", "input": {}},
        ]
    )
    result = await middleware.after_model(runtime_state, response)

    assert runtime_state.tool_loop_counts == {}
    assert result == response


@pytest.mark.asyncio
async def test_loop_detection_increments_hash_count(runtime_state):
    middleware = LoopDetectionMiddleware(hard_limit=5)

    response = LLMResponse(
        content=[
            {"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "/test"}},
        ]
    )
    await middleware.after_model(runtime_state, response)

    assert len(runtime_state.tool_loop_counts) == 1
    assert list(runtime_state.tool_loop_counts.values())[0] == 1


@pytest.mark.asyncio
async def test_loop_detection_returns_safety_stop_at_hard_limit(runtime_state):
    middleware = LoopDetectionMiddleware(hard_limit=3)

    response = LLMResponse(
        content=[
            {"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "/test"}},
        ]
    )

    # Call 3 times to hit limit
    for _ in range(2):
        await middleware.after_model(runtime_state, response)

    result = await middleware.after_model(runtime_state, response)

    assert result.stop_reason == "safety_stop"
    assert "Repeated tool calls" in result.text


@pytest.mark.asyncio
async def test_loop_detection_returns_normal_below_limit(runtime_state):
    middleware = LoopDetectionMiddleware(hard_limit=5)

    response = LLMResponse(
        content=[
            {"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "/test"}},
        ]
    )

    result = await middleware.after_model(runtime_state, response)

    assert result == response
    assert result.stop_reason != "safety_stop"


@pytest.mark.asyncio
async def test_loop_detection_different_tool_combos_different_hashes(runtime_state):
    middleware = LoopDetectionMiddleware(hard_limit=5)

    response1 = LLMResponse(
        content=[
            {"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "/test1"}},
        ]
    )
    response2 = LLMResponse(
        content=[
            {"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "/test2"}},
        ]
    )

    await middleware.after_model(runtime_state, response1)
    await middleware.after_model(runtime_state, response2)

    assert len(runtime_state.tool_loop_counts) == 2


# ── TodoReminderMiddleware ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_todo_reminder_injects_when_todos_exist(runtime_state, tmp_path):
    from prax.core.todo_store import TodoItem

    runtime_state.context.cwd = str(tmp_path)
    todos_dir = tmp_path / ".prax"
    todos_dir.mkdir()
    todos_file = todos_dir / "todos.json"
    todos_file.write_text('[{"content": "Fix bug", "activeForm": "Fixing bug", "status": "pending"}]')

    middleware = TodoReminderMiddleware(cwd=str(tmp_path))
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1
    assert "todo list from earlier" in runtime_state.messages[0]["content"]
    assert "Fix bug" in runtime_state.messages[0]["content"]


@pytest.mark.asyncio
async def test_todo_reminder_skips_when_no_todos(runtime_state, tmp_path):
    runtime_state.context.cwd = str(tmp_path)
    middleware = TodoReminderMiddleware(cwd=str(tmp_path))
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 0


@pytest.mark.asyncio
async def test_todo_reminder_skips_when_recently_visible(runtime_state, tmp_path):
    runtime_state.context.cwd = str(tmp_path)
    todos_dir = tmp_path / ".prax"
    todos_dir.mkdir()
    todos_file = todos_dir / "todos.json"
    todos_file.write_text('[{"content": "Fix bug", "activeForm": "Fixing bug", "status": "pending"}]')

    runtime_state.messages = [
        {"role": "user", "content": "Your todo list from earlier is here"}
    ]

    middleware = TodoReminderMiddleware(cwd=str(tmp_path))
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1  # No new message added


@pytest.mark.asyncio
async def test_todo_reminder_skips_when_todowrite_in_recent_messages(runtime_state, tmp_path):
    runtime_state.context.cwd = str(tmp_path)
    todos_dir = tmp_path / ".prax"
    todos_dir.mkdir()
    todos_file = todos_dir / "todos.json"
    todos_file.write_text('[{"content": "Fix bug", "activeForm": "Fixing bug", "status": "pending"}]')

    runtime_state.messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "name": "TodoWrite", "id": "1", "input": {}}]}
    ]

    middleware = TodoReminderMiddleware(cwd=str(tmp_path))
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1  # No new message added


# ── ContextInjectMiddleware ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_inject_injects_memory_backend_on_first_call(runtime_state, tmp_path):
    memory_backend = AsyncMock()
    memory_backend.format_for_prompt = AsyncMock(return_value="Memory context here")

    middleware = ContextInjectMiddleware(cwd=str(tmp_path), memory_backend=memory_backend)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1
    assert "Memory context here" in runtime_state.messages[0]["content"]
    assert runtime_state.messages[0]["name"] == "context_inject"


@pytest.mark.asyncio
async def test_context_inject_skips_on_subsequent_calls(runtime_state, tmp_path):
    memory_backend = AsyncMock()
    memory_backend.format_for_prompt = AsyncMock(return_value="Memory context here")

    middleware = ContextInjectMiddleware(cwd=str(tmp_path), memory_backend=memory_backend)
    await middleware.before_model(runtime_state)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1  # Only injected once


@pytest.mark.asyncio
async def test_context_inject_falls_back_to_openviking(runtime_state, tmp_path):
    openviking = AsyncMock()
    openviking.available = True
    openviking.get_experiences = AsyncMock(return_value=[])
    openviking.format_experiences_for_prompt = MagicMock(return_value="OpenViking context")

    middleware = ContextInjectMiddleware(cwd=str(tmp_path), openviking=openviking)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1
    assert "OpenViking context" in runtime_state.messages[0]["content"]


@pytest.mark.asyncio
async def test_context_inject_handles_memory_backend_exception(runtime_state, tmp_path):
    memory_backend = AsyncMock()
    memory_backend.format_for_prompt = AsyncMock(side_effect=Exception("Backend error"))

    middleware = ContextInjectMiddleware(cwd=str(tmp_path), memory_backend=memory_backend)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 0  # No injection on error


@pytest.mark.asyncio
async def test_context_inject_injects_nothing_when_both_backends_none(runtime_state, tmp_path):
    middleware = ContextInjectMiddleware(cwd=str(tmp_path), openviking=None, memory_backend=None)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 0


# ── ModelFallbackMiddleware ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_fallback_sets_dynamic_override_from_intent(runtime_state):
    middleware = ModelFallbackMiddleware()
    runtime_state.metadata["detected_intent"] = "debugging"

    await middleware.before_model(runtime_state)

    assert runtime_state.metadata["dynamic_model_override"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_model_fallback_tracks_tool_error_count(runtime_state, mock_tool):
    middleware = ModelFallbackMiddleware()
    tool_call = ToolCall(name="TestTool", input={})
    result = ToolResult(content="Error", is_error=True)

    await middleware.after_tool(runtime_state, tool_call, mock_tool, result)

    assert runtime_state.metadata["tool_error_count"] == 1


@pytest.mark.asyncio
async def test_model_fallback_noop_when_no_intent(runtime_state):
    middleware = ModelFallbackMiddleware()
    await middleware.before_model(runtime_state)

    assert "dynamic_model_override" not in runtime_state.metadata


# ── PromptCacheMiddleware ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_cache_sets_metadata_flags(runtime_state):
    middleware = PromptCacheMiddleware(min_tokens=2048)
    await middleware.before_model(runtime_state)

    assert runtime_state.metadata["prompt_cache_enabled"] is True
    assert runtime_state.metadata["prompt_cache_min_tokens"] == 2048


# ── HookMiddleware ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hook_middleware_executes_pretooluse_hooks(runtime_state, mock_tool, tmp_path):
    with patch("prax.core.hooks.get_hook_registry") as mock_get_registry, \
         patch("prax.core.hooks.load_hooks_from_directory"):
        mock_registry = MagicMock()
        mock_registry.execute_hooks = AsyncMock(return_value=None)
        mock_registry.load_from_claude_settings = MagicMock()
        mock_registry.load_from_file = MagicMock()
        mock_get_registry.return_value = mock_registry

        middleware = HookMiddleware(cwd=str(tmp_path))
        tool_call = ToolCall(name="Write", input={})
        result = await middleware.before_tool(runtime_state, tool_call, mock_tool)

        mock_registry.execute_hooks.assert_called_once_with(tool_call, mock_tool, "PreToolUse")
        assert result is None


@pytest.mark.asyncio
async def test_hook_middleware_combines_posttooluse_warning(runtime_state, mock_tool, tmp_path):
    with patch("prax.core.hooks.get_hook_registry") as mock_get_registry, \
         patch("prax.core.hooks.load_hooks_from_directory"):
        mock_registry = MagicMock()
        hook_result = ToolResult(content="Hook warning", is_error=True)
        mock_registry.execute_hooks = AsyncMock(return_value=hook_result)
        mock_registry.load_from_claude_settings = MagicMock()
        mock_registry.load_from_file = MagicMock()
        mock_get_registry.return_value = mock_registry

        middleware = HookMiddleware(cwd=str(tmp_path))
        tool_call = ToolCall(name="Write", input={})
        original_result = ToolResult(content="Original result", is_error=False)

        result = await middleware.after_tool(runtime_state, tool_call, mock_tool, original_result)

        assert "Original result" in result.content
        assert "Hook warning" in result.content


@pytest.mark.asyncio
async def test_hook_middleware_executes_lifecycle_hooks_premodel(runtime_state, tmp_path):
    with patch("prax.core.hooks.get_hook_registry") as mock_get_registry, \
         patch("prax.core.hooks.load_hooks_from_directory"):
        mock_registry = MagicMock()
        mock_registry.execute_lifecycle_hooks = AsyncMock()
        mock_registry.execute_hooks = AsyncMock(return_value=None)
        mock_registry.load_from_claude_settings = MagicMock()
        mock_registry.load_from_file = MagicMock()
        mock_get_registry.return_value = mock_registry

        middleware = HookMiddleware(cwd=str(tmp_path))
        await middleware.before_model(runtime_state)

        mock_registry.execute_lifecycle_hooks.assert_called_once()
        call_args = mock_registry.execute_lifecycle_hooks.call_args
        assert call_args[0][0] == "PreModel"


@pytest.mark.asyncio
async def test_hook_middleware_executes_lifecycle_hooks_postmodel(runtime_state, tmp_path):
    with patch("prax.core.hooks.get_hook_registry") as mock_get_registry, \
         patch("prax.core.hooks.load_hooks_from_directory"):
        mock_registry = MagicMock()
        mock_registry.execute_lifecycle_hooks = AsyncMock()
        mock_registry.execute_hooks = AsyncMock(return_value=None)
        mock_registry.load_from_claude_settings = MagicMock()
        mock_registry.load_from_file = MagicMock()
        mock_get_registry.return_value = mock_registry

        middleware = HookMiddleware(cwd=str(tmp_path))
        response = LLMResponse(content=[{"type": "text", "text": "Done"}])
        await middleware.after_model(runtime_state, response)

        mock_registry.execute_lifecycle_hooks.assert_called_once()
        call_args = mock_registry.execute_lifecycle_hooks.call_args
        assert call_args[0][0] == "PostModel"


# ── QualityGateMiddleware ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quality_gate_sets_pending_after_code_modifying_tool(runtime_state, mock_tool, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=["echo test"])
    tool_call = ToolCall(name="Write", input={})
    result = ToolResult(content="Success", is_error=False)

    await middleware.after_tool(runtime_state, tool_call, mock_tool, result)

    assert middleware._pending_check is True


@pytest.mark.asyncio
async def test_quality_gate_runs_commands_on_before_model_when_pending(runtime_state, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=["exit 0"])
    middleware._pending_check = True

    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        await middleware.before_model(runtime_state)

        mock_subprocess.assert_called_once()
        assert middleware._pending_check is False


@pytest.mark.asyncio
async def test_quality_gate_injects_failure_message_on_failure(runtime_state, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=["exit 1"])
    middleware._pending_check = True

    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error output"))
        mock_proc.returncode = 1
        mock_subprocess.return_value = mock_proc

        await middleware.before_model(runtime_state)

        assert len(runtime_state.messages) == 1
        assert "quality_gate_failure" in runtime_state.messages[0]["content"]


@pytest.mark.asyncio
async def test_quality_gate_noop_when_no_commands(runtime_state, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=[])
    middleware._pending_check = True

    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 0


@pytest.mark.asyncio
async def test_quality_gate_noop_when_not_pending(runtime_state, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=["echo test"])
    middleware._pending_check = False

    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 0


@pytest.mark.asyncio
async def test_quality_gate_handles_completion_checks_failures(runtime_state, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=[])
    middleware._completion_checks = ["exit 1"]

    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Check failed"))
        mock_proc.returncode = 1
        mock_subprocess.return_value = mock_proc

        response = LLMResponse(content=[{"type": "text", "text": "Done"}])
        result = await middleware.after_model(runtime_state, response)

        assert result.has_tool_calls
        assert result.stop_reason == "completion_check_retry"


@pytest.mark.asyncio
async def test_quality_gate_before_tool_intercepts_completion_check_sentinel(runtime_state, tmp_path):
    middleware = QualityGateMiddleware(cwd=str(tmp_path), commands=[])
    tool_call = ToolCall(name="__completion_check__", input={"failure": "Test failure"})

    result = await middleware.before_tool(runtime_state, tool_call, None)

    assert result is not None
    assert result.content == "Test failure"
    assert result.is_error is False


# ── VerificationGuidanceMiddleware ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_boundary_reminder_injects_once(runtime_state):
    runtime_state.iteration = 0
    middleware = RunBoundaryReminderMiddleware()

    await middleware.before_model(runtime_state)
    assert len(runtime_state.messages) == 1
    assert runtime_state.messages[0]["name"] == "run_boundary"
    assert "source of truth" in runtime_state.messages[0]["content"]

    await middleware.before_model(runtime_state)
    assert len(runtime_state.messages) == 1


@pytest.mark.asyncio
async def test_run_boundary_reminder_skips_nonzero_iteration(runtime_state):
    runtime_state.iteration = 2
    middleware = RunBoundaryReminderMiddleware()

    await middleware.before_model(runtime_state)

    assert runtime_state.messages == []


@pytest.mark.asyncio
async def test_verification_guidance_injects_failure_feedback(runtime_state):
    tracker = ChangeTracker()
    middleware = VerificationGuidanceMiddleware()
    tool_call = ToolCall(name="VerifyCommand", input={"command": "pytest -q"})
    result = ToolResult(content="Verification failed.\n\n1 failed", is_error=True)

    await tracker.after_tool(runtime_state, tool_call, None, result)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1
    assert "verification_feedback" in runtime_state.messages[0]["name"]
    assert "Focus on fixing" in runtime_state.messages[0]["content"]
    assert "1 failed" in runtime_state.messages[0]["content"]


@pytest.mark.asyncio
async def test_verification_guidance_injects_rerun_hint_after_code_change(runtime_state):
    tracker = ChangeTracker()
    middleware = VerificationGuidanceMiddleware()
    failed_verify = ToolCall(name="VerifyCommand", input={"command": "pytest -q"})
    await tracker.after_tool(
        runtime_state,
        failed_verify,
        None,
        ToolResult(content="Verification failed.\n\n1 failed", is_error=True),
    )
    await tracker.after_tool(
        runtime_state,
        ToolCall(name="HashlineEdit", input={}),
        None,
        ToolResult(content="edited", is_error=False),
    )
    await middleware.before_model(runtime_state)

    assert "Rerun VerifyCommand now" in runtime_state.messages[0]["content"]


@pytest.mark.asyncio
async def test_verification_guidance_injects_success_feedback(runtime_state):
    tracker = ChangeTracker()
    middleware = VerificationGuidanceMiddleware()
    tool_call = ToolCall(name="VerifyCommand", input={"command": "pytest -q"})
    result = ToolResult(content="Verification passed.\n\n2 passed", is_error=False)

    await tracker.after_tool(runtime_state, tool_call, None, result)
    await middleware.before_model(runtime_state)

    assert len(runtime_state.messages) == 1
    assert "verification_success" in runtime_state.messages[0]["name"]
    assert "Summarize the fix and stop" in runtime_state.messages[0]["content"]


@pytest.mark.asyncio
async def test_verification_guidance_treats_safe_sandbox_verify_as_verification(runtime_state):
    tracker = ChangeTracker()
    middleware = VerificationGuidanceMiddleware()
    tool_call = ToolCall(name="SandboxBash", input={"command": "pytest -q"})
    result = ToolResult(content="Verification failed.\n\n1 failed", is_error=True)

    await tracker.after_tool(runtime_state, tool_call, None, result)
    await middleware.before_model(runtime_state)

    assert "verification_feedback" in runtime_state.messages[0]["name"]


@pytest.mark.asyncio
async def test_verification_guidance_idempotent_before_model(runtime_state):
    """Consecutive before_model calls without new verify results must not inject duplicates."""
    tracker = ChangeTracker()
    middleware = VerificationGuidanceMiddleware()
    tool_call = ToolCall(name="VerifyCommand", input={"command": "pytest -q"})
    result = ToolResult(content="Verification failed.\n\n1 failed", is_error=True)

    await tracker.after_tool(runtime_state, tool_call, None, result)

    # First before_model — should inject guidance
    await middleware.before_model(runtime_state)
    assert len(runtime_state.messages) == 1
    assert "verification_feedback" in runtime_state.messages[0]["name"]

    # Second before_model without new verify result — dedupe key blocks re-injection
    await middleware.before_model(runtime_state)
    assert len(runtime_state.messages) == 1  # No new message added


# ── EvaluatorMiddleware ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_design_restoration_guard_blocks_completion_without_verification(runtime_state):
    runtime_state.messages = [
        {"role": "user", "content": "根据 MasterGo 设计稿还原这个 HTML 页面并交付最终结果"}
    ]
    tracker = ChangeTracker()
    middleware = DesignRestorationGuardMiddleware()

    await tracker.after_tool(
        runtime_state,
        ToolCall(name="HashlineEdit", input={}),
        None,
        ToolResult(content="edited", is_error=False),
    )
    response = LLMResponse(content=[{"type": "text", "text": "已完成"}])

    result = await middleware.after_model(runtime_state, response)

    assert result.has_tool_calls
    assert result.stop_reason == "design_verification_retry"


@pytest.mark.asyncio
async def test_design_restoration_guard_allows_completion_after_verification(runtime_state):
    runtime_state.messages = [
        {"role": "user", "content": "请按 MasterGo 设计稿还原页面"}
    ]
    tracker = ChangeTracker()
    middleware = DesignRestorationGuardMiddleware()

    edit_call = ToolCall(name="HashlineEdit", input={})
    await tracker.after_tool(runtime_state, edit_call, None, ToolResult(content="edited", is_error=False))

    verify_call = ToolCall(
        name="SandboxBash",
        input={"command": "node scripts/d2c/verify-html-rendering.js ref.html impl.html"},
    )
    verify_result = ToolResult(content="verified", is_error=False)
    await tracker.after_tool(runtime_state, verify_call, None, verify_result)
    await middleware.after_tool(runtime_state, verify_call, None, verify_result)

    response = LLMResponse(content=[{"type": "text", "text": "已完成"}])

    result = await middleware.after_model(runtime_state, response)

    assert result == response


@pytest.mark.asyncio
async def test_design_restoration_guard_skips_non_restoration_tasks(runtime_state):
    runtime_state.messages = [
        {"role": "user", "content": "请修复 pytest 失败"}
    ]
    tracker = ChangeTracker()
    middleware = DesignRestorationGuardMiddleware()

    await tracker.after_tool(
        runtime_state,
        ToolCall(name="HashlineEdit", input={}),
        None,
        ToolResult(content="edited", is_error=False),
    )
    response = LLMResponse(content=[{"type": "text", "text": "已完成"}])

    result = await middleware.after_model(runtime_state, response)

    assert result == response


@pytest.mark.asyncio
async def test_design_restoration_guard_before_tool_returns_feedback(runtime_state):
    middleware = DesignRestorationGuardMiddleware()
    tool_call = ToolCall(
        name="__design_restoration_guard__",
        input={"feedback": "Run screenshot verification first."},
    )

    result = await middleware.before_tool(runtime_state, tool_call, None)

    assert result is not None
    assert "Run screenshot verification first." in result.content
    assert result.is_error is False


# ── EvaluatorMiddleware ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluator_loads_criteria_from_yaml(tmp_path):
    evaluator_dir = tmp_path / ".prax"
    evaluator_dir.mkdir()
    evaluator_file = evaluator_dir / "evaluator.yaml"
    evaluator_file.write_text("criteria:\n  - 响应必须包含具体的文件路径\n")

    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=2)

    assert len(middleware._criteria) == 1
    assert "文件路径" in middleware._criteria[0]


@pytest.mark.asyncio
async def test_evaluator_passes_when_criteria_met(runtime_state, tmp_path):
    evaluator_dir = tmp_path / ".prax"
    evaluator_dir.mkdir()
    evaluator_file = evaluator_dir / "evaluator.yaml"
    evaluator_file.write_text("criteria:\n  - 响应必须包含具体的文件路径\n")

    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=2)
    response = LLMResponse(content=[{"type": "text", "text": "File at /test/path.txt"}])

    result = await middleware.after_model(runtime_state, response)

    assert result == response


@pytest.mark.asyncio
async def test_evaluator_returns_synthetic_tool_use_when_criteria_unmet(runtime_state, tmp_path):
    evaluator_dir = tmp_path / ".prax"
    evaluator_dir.mkdir()
    evaluator_file = evaluator_dir / "evaluator.yaml"
    evaluator_file.write_text("criteria:\n  - 响应必须包含具体的文件路径\n")

    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=2)
    response = LLMResponse(content=[{"type": "text", "text": "No file path here"}])

    result = await middleware.after_model(runtime_state, response)

    assert result.has_tool_calls
    assert result.stop_reason == "evaluator_retry"
    assert middleware._retry_count == 1


@pytest.mark.asyncio
async def test_evaluator_respects_max_retries(runtime_state, tmp_path):
    evaluator_dir = tmp_path / ".prax"
    evaluator_dir.mkdir()
    evaluator_file = evaluator_dir / "evaluator.yaml"
    evaluator_file.write_text("criteria:\n  - 响应必须包含具体的文件路径\n")

    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=1)
    response = LLMResponse(content=[{"type": "text", "text": "No file path"}])

    # First retry
    result1 = await middleware.after_model(runtime_state, response)
    assert result1.has_tool_calls

    # Second call should pass through (max retries reached)
    result2 = await middleware.after_model(runtime_state, response)
    assert result2 == response
    assert middleware._retry_count == 0  # Reset after max


@pytest.mark.asyncio
async def test_evaluator_before_tool_intercepts_feedback_sentinel(runtime_state, tmp_path):
    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=2)
    tool_call = ToolCall(name="__evaluator_feedback__", input={"feedback": "Fix this"})

    result = await middleware.before_tool(runtime_state, tool_call, None)

    assert result is not None
    assert "Fix this" in result.content
    assert result.is_error is False


@pytest.mark.asyncio
async def test_evaluator_noop_when_no_criteria(runtime_state, tmp_path):
    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=2)
    response = LLMResponse(content=[{"type": "text", "text": "Any text"}])

    result = await middleware.after_model(runtime_state, response)

    assert result == response


@pytest.mark.asyncio
async def test_evaluator_noop_when_response_has_tool_calls(runtime_state, tmp_path):
    evaluator_dir = tmp_path / ".prax"
    evaluator_dir.mkdir()
    evaluator_file = evaluator_dir / "evaluator.yaml"
    evaluator_file.write_text("criteria:\n  - 响应必须包含具体的文件路径\n")

    middleware = EvaluatorMiddleware(cwd=str(tmp_path), max_retries=2)
    response = LLMResponse(
        content=[
            {"type": "text", "text": "No path"},
            {"type": "tool_use", "id": "1", "name": "Read", "input": {}},
        ]
    )

    result = await middleware.after_model(runtime_state, response)

    assert result == response
