"""Tests for ChangeTracker middleware — single writer of code-change / verify state."""

import pytest
from unittest.mock import MagicMock

from prax.core.context import Context
from prax.core.middleware import (
    CHANGE_TRACKER_KEY,
    CODE_MODIFYING_TOOLS,
    ChangeTracker,
    RuntimeState,
    _is_verify_attempt,
)
from prax.tools.base import ToolCall, ToolResult


@pytest.fixture
def state():
    ctx = MagicMock(spec=Context)
    ctx.cwd = "/test/cwd"
    return RuntimeState(messages=[], context=ctx, iteration=0, tool_loop_counts={}, metadata={})


@pytest.mark.asyncio
async def test_tracker_initializes_default_state_on_first_read(state):
    tracker = ChangeTracker()
    await tracker.after_tool(
        state,
        ToolCall(name="Read", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )
    snap = state.metadata[CHANGE_TRACKER_KEY]
    assert snap == {
        "code_gen": 0,
        "verified_gen": 0,
        "last_verify_ok": False,
        "last_verify_error": None,
    }


@pytest.mark.asyncio
async def test_bash_readonly_does_not_bump_code_gen(state):
    """Bash must NOT be treated as a code-modifying tool — this was the 0.3.1 regression."""
    tracker = ChangeTracker()
    for cmd in ("ls", "git status", "cat README.md"):
        await tracker.after_tool(
            state,
            ToolCall(name="Bash", input={"command": cmd}),
            None,
            ToolResult(content="ok", is_error=False),
        )
    assert state.metadata[CHANGE_TRACKER_KEY]["code_gen"] == 0


@pytest.mark.asyncio
async def test_write_edit_apply_patch_all_bump_code_gen(state):
    tracker = ChangeTracker()
    for name in ("Write", "Edit", "MultiEdit", "HashlineEdit", "AstGrepReplace", "ApplyPatch"):
        await tracker.after_tool(
            state,
            ToolCall(name=name, input={}),
            None,
            ToolResult(content="ok", is_error=False),
        )
    assert state.metadata[CHANGE_TRACKER_KEY]["code_gen"] == 6


@pytest.mark.asyncio
async def test_failed_code_edit_does_not_bump(state):
    tracker = ChangeTracker()
    await tracker.after_tool(
        state,
        ToolCall(name="Write", input={}),
        None,
        ToolResult(content="permission denied", is_error=True),
    )
    assert state.metadata[CHANGE_TRACKER_KEY]["code_gen"] == 0


@pytest.mark.asyncio
async def test_verify_command_tool_marks_success(state):
    tracker = ChangeTracker()
    await tracker.after_tool(
        state,
        ToolCall(name="Write", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )
    await tracker.after_tool(
        state,
        ToolCall(name="VerifyCommand", input={"command": "pytest -q"}),
        None,
        ToolResult(content="Verification passed.", is_error=False),
    )
    snap = state.metadata[CHANGE_TRACKER_KEY]
    assert snap["code_gen"] == 1
    assert snap["verified_gen"] == 1
    assert snap["last_verify_ok"] is True
    assert snap["last_verify_error"] is None


@pytest.mark.asyncio
async def test_verify_failure_records_error_and_keeps_verified_gen(state):
    tracker = ChangeTracker()
    await tracker.after_tool(
        state,
        ToolCall(name="Edit", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )
    await tracker.after_tool(
        state,
        ToolCall(name="VerifyCommand", input={"command": "pytest -q"}),
        None,
        ToolResult(content="Verification failed.\n\n1 failed", is_error=True),
    )
    snap = state.metadata[CHANGE_TRACKER_KEY]
    assert snap["code_gen"] == 1
    assert snap["verified_gen"] == 0
    assert snap["last_verify_ok"] is False
    assert "1 failed" in snap["last_verify_error"]


@pytest.mark.asyncio
async def test_python_dash_m_pytest_counts_as_verify(state):
    """The VerifyCommandTool falls back to `python -m pytest` when pytest is not on PATH.
    The tracker must recognize the equivalent Bash form."""
    tracker = ChangeTracker()
    await tracker.after_tool(
        state,
        ToolCall(name="Edit", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )
    await tracker.after_tool(
        state,
        ToolCall(name="Bash", input={"command": "python -m pytest -q"}),
        None,
        ToolResult(content="passed", is_error=False),
    )
    snap = state.metadata[CHANGE_TRACKER_KEY]
    assert snap["last_verify_ok"] is True
    assert snap["verified_gen"] == 1


@pytest.mark.asyncio
async def test_arbitrary_bash_is_not_verify_attempt():
    assert _is_verify_attempt(ToolCall(name="Bash", input={"command": "ls"})) is False
    assert _is_verify_attempt(ToolCall(name="Bash", input={"command": "git status"})) is False


@pytest.mark.asyncio
async def test_verify_attempt_recognizes_npm_and_cargo_variants():
    assert _is_verify_attempt(ToolCall(name="Bash", input={"command": "npm test"})) is True
    assert _is_verify_attempt(ToolCall(name="SandboxBash", input={"command": "cargo test"})) is True
    assert _is_verify_attempt(ToolCall(name="Bash", input={"command": "go test ./..."})) is True


@pytest.mark.asyncio
async def test_long_error_output_is_trimmed(state):
    tracker = ChangeTracker(max_failure_output_chars=50)
    huge = "X" * 500
    await tracker.after_tool(
        state,
        ToolCall(name="VerifyCommand", input={"command": "pytest -q"}),
        None,
        ToolResult(content=huge, is_error=True),
    )
    trimmed = state.metadata[CHANGE_TRACKER_KEY]["last_verify_error"]
    assert trimmed.endswith("[truncated]")
    assert len(trimmed) <= 50 + len("\n...[truncated]")


def test_code_modifying_tools_frozenset_is_shared():
    assert "Write" in CODE_MODIFYING_TOOLS
    assert "ApplyPatch" in CODE_MODIFYING_TOOLS
    assert "HashlineEdit" in CODE_MODIFYING_TOOLS
    # Bash is NOT a code-modifying tool from the tracker's perspective.
    assert "Bash" not in CODE_MODIFYING_TOOLS
