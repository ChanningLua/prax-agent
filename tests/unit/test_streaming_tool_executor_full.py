from __future__ import annotations

import pytest

from prax.core.context import Context
from prax.core.middleware import RuntimeState
from prax.core.streaming_tool_executor import StreamingToolExecutor
from prax.tools.base import Tool, ToolCall, ToolResult


class _ValidatedTool(Tool):
    name = "ValidatedTool"
    description = "requires a command"
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
        "additionalProperties": False,
    }

    async def execute(self, params):
        return ToolResult(content=str(params["command"]))


@pytest.mark.asyncio
async def test_streaming_tool_executor_returns_validation_error(tmp_path):
    state = RuntimeState(
        messages=[],
        context=Context(cwd=str(tmp_path)),
        iteration=0,
        tool_loop_counts={},
        metadata={},
    )
    executor = StreamingToolExecutor(
        tools=[_ValidatedTool()],
        middlewares=[],
        state=state,
    )

    await executor.submit(ToolCall(name="ValidatedTool", input={}))
    results = await executor.drain()

    assert len(results) == 1
    assert results[0].result.is_error
    assert "Invalid input for ValidatedTool" in results[0].result.content


@pytest.mark.asyncio
async def test_unknown_tool_name_does_not_expose_tool_list(tmp_path):
    """Reject unknown tool calls without leaking registered tool names (AC-1 defense)."""
    state = RuntimeState(
        messages=[],
        context=Context(cwd=str(tmp_path)),
        iteration=0,
        tool_loop_counts={},
        metadata={},
    )
    executor = StreamingToolExecutor(
        tools=[_ValidatedTool()],
        middlewares=[],
        state=state,
    )

    await executor.submit(ToolCall(name="FakeToolXYZ", input={}))
    results = await executor.drain()

    assert len(results) == 1
    assert results[0].result.is_error
    assert "unknown tool 'FakeToolXYZ'" in results[0].result.content
    # Must NOT expose available tool names
    assert "ValidatedTool" not in results[0].result.content
    assert "Available tools" not in results[0].result.content
