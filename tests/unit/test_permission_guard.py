"""Tests for prax/core/permission_guard.py."""

import pytest
from unittest.mock import MagicMock, Mock

from prax.core.context import Context
from prax.core.middleware import RuntimeState
from prax.core.permission_guard import PermissionGuardMiddleware
from prax.core.permissions import PermissionMode
from prax.tools.write import WriteTool
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


# ── WORKSPACE_WRITE mode ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workspace_write_blocks_dangerous_tool(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.WORKSPACE_WRITE)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.DANGEROUS)

    tool_call = ToolCall(name="Bash", input={"command": "rm -rf /"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is not None
    assert result.is_error
    assert "Permission denied" in result.content
    assert "dangerous" in result.content.lower()


@pytest.mark.asyncio
async def test_workspace_write_allows_safe_tool(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.WORKSPACE_WRITE)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.SAFE)

    tool_call = ToolCall(name="Read", input={"file_path": "/test/file.txt"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None


@pytest.mark.asyncio
async def test_workspace_write_allows_review_tool(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.WORKSPACE_WRITE)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    tool_call = ToolCall(name="Write", input={"file_path": "/test/file.txt"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None


# ── READ_ONLY mode ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_only_blocks_review_tool(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.READ_ONLY)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    tool_call = ToolCall(name="Write", input={"file_path": "/test/file.txt"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is not None
    assert result.is_error
    assert "Permission denied" in result.content


@pytest.mark.asyncio
async def test_read_only_blocks_dangerous_tool(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.READ_ONLY)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.DANGEROUS)

    tool_call = ToolCall(name="Bash", input={"command": "ls"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is not None
    assert result.is_error


@pytest.mark.asyncio
async def test_read_only_allows_safe_tool(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.READ_ONLY)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.SAFE)

    tool_call = ToolCall(name="Read", input={"file_path": "/test/file.txt"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None


# ── DANGER_FULL_ACCESS mode ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_danger_full_access_allows_everything(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.DANGER_FULL_ACCESS)

    for level in [PermissionLevel.SAFE, PermissionLevel.REVIEW, PermissionLevel.DANGEROUS]:
        tool = MagicMock(spec=Tool)
        tool.required_permission = MagicMock(return_value=level)

        tool_call = ToolCall(name="TestTool", input={})
        result = await middleware.before_tool(runtime_state, tool_call, tool)

        assert result is None


# ── Risk score checks ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_score_blocks_high_risk_calls(runtime_state):
    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        risk_threshold=10
    )

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    # TmuxBash with destructive command should have high risk score
    tool_call = ToolCall(name="TmuxBash", input={"command": "rm -rf /important"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is not None
    assert result.is_error
    assert "High-risk operation blocked" in result.content


@pytest.mark.asyncio
async def test_risk_score_check_skipped_for_danger_full_access(runtime_state):
    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        risk_threshold=1  # Very low threshold
    )

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.DANGEROUS)

    # Even with high-risk command, should pass in DANGER_FULL_ACCESS mode
    tool_call = ToolCall(name="Bash", input={"command": "rm -rf /"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None


@pytest.mark.asyncio
async def test_risk_score_allows_low_risk_calls(runtime_state):
    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        risk_threshold=15
    )

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.SAFE)

    # Read operation should have low risk score
    tool_call = ToolCall(name="Read", input={"file_path": "/test/file.txt"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None


@pytest.mark.asyncio
async def test_workspace_write_blocks_write_outside_workspace(tmp_path):
    context = MagicMock(spec=Context)
    context.cwd = str(tmp_path / "workspace")
    runtime_state = RuntimeState(
        messages=[],
        context=context,
        iteration=1,
        tool_loop_counts={},
        metadata={},
    )
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.WORKSPACE_WRITE)
    tool = WriteTool()

    tool_call = ToolCall(
        name="Write",
        input={"file_path": str(tmp_path / "outside.txt"), "content": "hello"},
    )
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is not None
    assert result.is_error
    assert "outside the allowed workspace" in result.content


# ── Denied count tracking ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_denied_count_increments(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.READ_ONLY)

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    assert middleware.denied_count == 0

    tool_call = ToolCall(name="Write", input={})
    await middleware.before_tool(runtime_state, tool_call, tool)

    assert middleware.denied_count == 1

    await middleware.before_tool(runtime_state, tool_call, tool)

    assert middleware.denied_count == 2


# ── Callback invocation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_permission_denied_callback_called(runtime_state):
    callback = MagicMock()
    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.READ_ONLY,
        on_permission_denied=callback
    )

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.DANGEROUS)

    tool_call = ToolCall(name="Bash", input={"command": "ls"})
    await middleware.before_tool(runtime_state, tool_call, tool)

    callback.assert_called_once_with(tool_call, PermissionLevel.DANGEROUS, PermissionMode.READ_ONLY)


# ── Governance config override ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_governance_config_overrides_permission_mode(runtime_state):
    governance = MagicMock()
    governance.permission_mode = "read-only"
    governance.risk_threshold = 20

    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.WORKSPACE_WRITE,  # Should be overridden
        risk_threshold=10,  # Should be overridden
        governance=governance
    )

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    tool_call = ToolCall(name="Write", input={})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    # Should be blocked because governance set mode to READ_ONLY
    assert result is not None
    assert result.is_error


@pytest.mark.asyncio
async def test_governance_config_overrides_risk_threshold(runtime_state):
    governance = MagicMock()
    governance.permission_mode = "workspace-write"
    governance.risk_threshold = 100  # Very high threshold

    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        risk_threshold=1,  # Very low, should be overridden
        governance=governance
    )

    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.REVIEW)

    # High-risk command that would normally be blocked
    tool_call = ToolCall(name="Bash", input={"command": "rm -rf /test"})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    # Should pass because governance set high threshold
    assert result is None


# ── Unknown tool handling ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_tool_passes_through(runtime_state):
    middleware = PermissionGuardMiddleware(permission_mode=PermissionMode.READ_ONLY)

    tool_call = ToolCall(name="UnknownTool", input={})
    result = await middleware.before_tool(runtime_state, tool_call, None)

    # Should return None to let agent_loop handle the unknown tool error
    assert result is None


# ── Invalid governance config ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_permission_mode_in_governance_falls_back(runtime_state):
    governance = MagicMock()
    governance.permission_mode = "invalid-mode"
    governance.risk_threshold = 15

    # Should not raise, should fall back to default
    middleware = PermissionGuardMiddleware(
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        governance=governance
    )

    # Verify it still works with fallback mode
    tool = MagicMock(spec=Tool)
    tool.required_permission = MagicMock(return_value=PermissionLevel.SAFE)

    tool_call = ToolCall(name="Read", input={})
    result = await middleware.before_tool(runtime_state, tool_call, tool)

    assert result is None
