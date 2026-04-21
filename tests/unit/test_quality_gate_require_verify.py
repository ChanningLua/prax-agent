"""Tests for QualityGateMiddleware.require_verify_before_completion gate.

This gate replaces the removed VerificationGuardMiddleware; it reads shared
state from ChangeTracker and blocks no-tool completion while code_gen has
advanced past verified_gen.
"""

import pytest
from unittest.mock import MagicMock

from prax.core.context import Context
from prax.core.llm_client import LLMResponse
from prax.core.middleware import (
    ChangeTracker,
    QualityGateMiddleware,
    RuntimeState,
)
from prax.tools.base import ToolCall, ToolResult


@pytest.fixture
def state():
    ctx = MagicMock(spec=Context)
    ctx.cwd = "/test/cwd"
    return RuntimeState(messages=[], context=ctx, iteration=0, tool_loop_counts={}, metadata={})


def _final_response(text: str = "done") -> LLMResponse:
    return LLMResponse(content=[{"type": "text", "text": text}])


@pytest.mark.asyncio
async def test_require_verify_blocks_when_code_changed_without_verification(state, tmp_path):
    tracker = ChangeTracker()
    gate = QualityGateMiddleware(
        cwd=str(tmp_path),
        commands=[],
        require_verify_before_completion=True,
    )

    await tracker.after_tool(
        state,
        ToolCall(name="Write", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )

    result = await gate.after_model(state, _final_response())

    assert result.has_tool_calls
    assert result.stop_reason == "completion_check_retry"
    tool_call = result.tool_calls[0]
    assert tool_call.name == "__completion_check__"
    assert "no passing verification" in tool_call.input["failure"]


@pytest.mark.asyncio
async def test_require_verify_allows_completion_when_verified(state, tmp_path):
    tracker = ChangeTracker()
    gate = QualityGateMiddleware(
        cwd=str(tmp_path),
        commands=[],
        require_verify_before_completion=True,
    )

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

    response = _final_response()
    result = await gate.after_model(state, response)

    assert result is response


@pytest.mark.asyncio
async def test_require_verify_off_by_default(state, tmp_path):
    """Without explicit opt-in, the gate is silent — preserves 0.3.0 behavior."""
    gate = QualityGateMiddleware(cwd=str(tmp_path), commands=[])

    # Even with dirty state, gate must not interfere.
    tracker = ChangeTracker()
    await tracker.after_tool(
        state,
        ToolCall(name="Write", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )

    response = _final_response()
    result = await gate.after_model(state, response)

    assert result is response


@pytest.mark.asyncio
async def test_require_verify_reentry_after_edit_reblocks(state, tmp_path):
    """Verified → edit → unverified again should re-block."""
    tracker = ChangeTracker()
    gate = QualityGateMiddleware(
        cwd=str(tmp_path),
        commands=[],
        require_verify_before_completion=True,
    )

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
        ToolResult(content="passed", is_error=False),
    )

    # First completion attempt passes.
    first = await gate.after_model(state, _final_response())
    assert not first.has_tool_calls

    # Another edit dirties the state.
    await tracker.after_tool(
        state,
        ToolCall(name="HashlineEdit", input={}),
        None,
        ToolResult(content="edited", is_error=False),
    )

    second = await gate.after_model(state, _final_response())
    assert second.has_tool_calls
    assert second.stop_reason == "completion_check_retry"


@pytest.mark.asyncio
async def test_require_verify_passes_through_if_no_code_changes(state, tmp_path):
    gate = QualityGateMiddleware(
        cwd=str(tmp_path),
        commands=[],
        require_verify_before_completion=True,
    )

    response = _final_response()
    result = await gate.after_model(state, response)

    assert result is response


@pytest.mark.asyncio
async def test_require_verify_max_retries_lets_completion_through(state, tmp_path, caplog):
    tracker = ChangeTracker()
    gate = QualityGateMiddleware(
        cwd=str(tmp_path),
        commands=[],
        require_verify_before_completion=True,
        max_require_verify_retries=2,
    )

    await tracker.after_tool(
        state,
        ToolCall(name="Write", input={}),
        None,
        ToolResult(content="ok", is_error=False),
    )

    # First two calls block.
    r1 = await gate.after_model(state, _final_response())
    assert r1.has_tool_calls
    r2 = await gate.after_model(state, _final_response())
    assert r2.has_tool_calls

    # Third call: retries exhausted → passes through + logs warning.
    caplog.clear()
    response = _final_response()
    r3 = await gate.after_model(state, response)
    assert r3 is response
    assert any("exhausted" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_require_verify_flag_can_be_read_from_yaml(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "quality-gates.yaml").write_text(
        "require_verify_before_completion: true\n"
    )
    gate = QualityGateMiddleware(cwd=str(tmp_path))
    assert gate._require_verify is True
