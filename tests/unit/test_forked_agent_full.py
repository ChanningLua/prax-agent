"""
Comprehensive unit tests for ForkedAgent covering uncovered execution paths:
- _run_inner() tool call loop, max_iterations, extra_context, LLM error
- _execute_tool() tool-not-in-allowed, tool-not-in-map, exception
- run() timeout path
- run_memory_extraction() standalone function
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.core.forked_agent import ForkedAgent, run_memory_extraction
from prax.tools.base import Tool, ToolCall, ToolResult


# ── helpers ────────────────────────────────────────────────────────────────────


class _SimpleTool(Tool):
    name = "SimpleTool"
    description = "A simple tool"
    input_schema: dict = {"type": "object", "properties": {}}

    async def execute(self, params: dict) -> ToolResult:
        return ToolResult(content="simple result", is_error=False)


class _BrokenTool(Tool):
    name = "BrokenTool"
    description = "A tool that throws"
    input_schema: dict = {"type": "object", "properties": {}}

    async def execute(self, params: dict) -> ToolResult:
        raise RuntimeError("deliberate failure")


def _tc(name: str, id: str = "tc1") -> ToolCall:
    return ToolCall(id=id, name=name, input={})


def _make_response(text: str = "", tool_calls: list | None = None):
    r = MagicMock()
    r.text = text
    calls = tool_calls or []
    r.has_tool_calls = bool(calls)
    r.tool_calls = [_tc(c) if isinstance(c, str) else c for c in calls]
    r.content = [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                 for tc in r.tool_calls] if calls else [{"type": "text", "text": text}]
    return r


# ── _execute_tool paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_not_in_allowed_set():
    """Tool name not in allowed set → Permission denied error."""
    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=["SimpleTool"],
        llm_client=MagicMock(),
        model_config=MagicMock(),
        tools=[_SimpleTool()],
    )
    result = await agent._execute_tool(_tc("NotAllowed"))
    assert result.is_error is True
    assert "Permission denied" in result.content
    assert "NotAllowed" in result.content


@pytest.mark.asyncio
async def test_execute_tool_in_allowed_but_not_in_tool_map():
    """Tool is in allowed list but was not provided in tools list → not-available error."""
    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=["GhostTool"],   # allowed but no tool provided
        llm_client=MagicMock(),
        model_config=MagicMock(),
        tools=[],                      # empty
    )
    result = await agent._execute_tool(_tc("GhostTool"))
    assert result.is_error is True
    assert "not available" in result.content
    assert "GhostTool" in result.content


@pytest.mark.asyncio
async def test_execute_tool_exception_returned_as_error():
    """Tool.execute() raises → ToolResult with is_error=True."""
    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=["BrokenTool"],
        llm_client=MagicMock(),
        model_config=MagicMock(),
        tools=[_BrokenTool()],
    )
    result = await agent._execute_tool(_tc("BrokenTool"))
    assert result.is_error is True
    assert "deliberate failure" in result.content


@pytest.mark.asyncio
async def test_execute_tool_success():
    """Whitelisted tool executes normally."""
    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=["SimpleTool"],
        llm_client=MagicMock(),
        model_config=MagicMock(),
        tools=[_SimpleTool()],
    )
    result = await agent._execute_tool(_tc("SimpleTool"))
    assert result.is_error is False
    assert result.content == "simple result"


# ── _run_inner paths ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_inner_no_tool_calls_returns_text():
    """When LLM returns no tool calls, _run_inner returns the text immediately."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_make_response(text="Final answer"))

    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=[],
        llm_client=mock_client,
        model_config=MagicMock(),
        tools=[],
    )
    result = await agent._run_inner("Do something")
    assert result == "Final answer"
    mock_client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_inner_tool_call_loop_two_iterations():
    """LLM calls a tool on iter-1, then returns text on iter-2."""
    tc = _tc("SimpleTool")
    call_seq = [
        _make_response(tool_calls=[tc]),
        _make_response(text="Done after tool"),
    ]
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=call_seq)

    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=["SimpleTool"],
        llm_client=mock_client,
        model_config=MagicMock(),
        tools=[_SimpleTool()],
        max_iterations=5,
    )
    result = await agent._run_inner("Do something")
    assert result == "Done after tool"
    assert mock_client.complete.await_count == 2


@pytest.mark.asyncio
async def test_run_inner_max_iterations_sentinel():
    """If tool calls persist beyond max_iterations, returns sentinel string."""
    tc = _tc("SimpleTool")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_make_response(tool_calls=[tc]))

    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=["SimpleTool"],
        llm_client=mock_client,
        model_config=MagicMock(),
        tools=[_SimpleTool()],
        max_iterations=2,
    )
    result = await agent._run_inner("Infinite loop task")
    assert "Max iterations" in result
    assert mock_client.complete.await_count == 2


@pytest.mark.asyncio
async def test_run_inner_extra_context_appended_to_system():
    """extra_context is appended to the system prompt passed to LLM."""
    captured: dict = {}
    async def _complete(**kwargs):
        captured.update(kwargs)
        return _make_response(text="ok")

    mock_client = MagicMock()
    mock_client.complete = _complete

    agent = ForkedAgent(
        parent_system_prompt="base prompt",
        allowed_tools=[],
        llm_client=mock_client,
        model_config=MagicMock(),
        tools=[],
    )
    await agent._run_inner("task", extra_context="--- EXTRA ---")
    assert "--- EXTRA ---" in captured["system_prompt"]
    assert "base prompt" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_run_inner_llm_exception_returns_error_string():
    """LLM call exception → _run_inner returns error string (no re-raise)."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=[],
        llm_client=mock_client,
        model_config=MagicMock(),
        tools=[],
    )
    result = await agent._run_inner("task")
    assert "ForkedAgent error" in result
    assert "LLM unavailable" in result


# ── run() paths ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_success_returns_text():
    """run() acquires semaphore, calls _run_inner, returns text."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_make_response(text="result text"))

    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=[],
        llm_client=mock_client,
        model_config=MagicMock(),
        tools=[],
    )
    result = await agent.run("Do task")
    assert result == "result text"


@pytest.mark.asyncio
async def test_run_timeout_returns_timeout_message():
    """asyncio.TimeoutError is caught and a timeout message is returned."""

    async def slow_inner(task, extra_context=None):
        await asyncio.sleep(9999)
        return "unreachable"

    agent = ForkedAgent(
        parent_system_prompt="sys",
        allowed_tools=[],
        llm_client=MagicMock(),
        model_config=MagicMock(),
        tools=[],
    )
    agent._run_inner = slow_inner

    # Patch wait_for to always time out
    with patch("prax.core.forked_agent.asyncio.wait_for",
               side_effect=asyncio.TimeoutError()):
        result = await agent.run("task")

    assert "Timed out" in result or "timed out" in result.lower()


# ── run_memory_extraction ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_memory_extraction_creates_forked_agent_and_runs():
    """run_memory_extraction creates a ForkedAgent and calls run()."""
    mock_client = MagicMock()
    # The ForkedAgent.run call happens inside run_memory_extraction
    run_mock = AsyncMock(return_value="memory extracted")

    with patch("prax.core.forked_agent.ForkedAgent") as MockFA:
        instance = MagicMock()
        instance.run = run_mock
        MockFA.return_value = instance

        await run_memory_extraction(
            messages=[{"role": "user", "content": "hello"}],
            parent_system_prompt="base sys",
            llm_client=mock_client,
            model_config=MagicMock(),
            tools=[],
            memory_file_path="/path/MEMORY.md",
        )

    # ForkedAgent constructed with correct allowed tools
    call_kwargs = MockFA.call_args[1]
    assert set(call_kwargs["allowed_tools"]) == {"HashlineRead", "Write"}
    assert call_kwargs["max_iterations"] == 3
    run_mock.assert_awaited_once()
    # Task string should reference the memory file path
    task_arg = run_mock.call_args[0][0]
    assert "/path/MEMORY.md" in task_arg


@pytest.mark.asyncio
async def test_run_memory_extraction_handles_run_exception_silently():
    """If ForkedAgent.run() raises, run_memory_extraction swallows the error."""
    with patch("prax.core.forked_agent.ForkedAgent") as MockFA:
        instance = MagicMock()
        instance.run = AsyncMock(side_effect=RuntimeError("boom"))
        MockFA.return_value = instance

        # Should not raise
        await run_memory_extraction(
            messages=[],
            parent_system_prompt="sys",
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[],
            memory_file_path="/m.md",
        )


@pytest.mark.asyncio
async def test_run_memory_extraction_uses_last_20_messages():
    """Only the last 20 messages are included in the extraction task."""
    messages = [
        {"role": "user", "content": f"msg_{i}"}
        for i in range(30)
    ]
    task_captured: list[str] = []

    async def _fake_run(task, extra_context=None):
        task_captured.append(task)
        return "ok"

    with patch("prax.core.forked_agent.ForkedAgent") as MockFA:
        instance = MagicMock()
        instance.run = _fake_run
        MockFA.return_value = instance

        await run_memory_extraction(
            messages=messages,
            parent_system_prompt="sys",
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[],
            memory_file_path="/m.md",
        )

    # Only last 20 messages: msg_10..msg_29 should appear; msg_0..msg_9 should not
    assert "msg_29" in task_captured[0]
    assert "msg_0" not in task_captured[0]
