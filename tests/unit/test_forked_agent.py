"""Tests for prax.core.forked_agent.ForkedAgent."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from prax.core.forked_agent import ForkedAgent
from prax.tools.base import Tool, ToolCall, ToolResult


class _DummyTool(Tool):
    name = "DummyTool"
    description = "A dummy tool for testing"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, params):
        return ToolResult(content="dummy result", is_error=False)


class _ForbiddenTool(Tool):
    name = "ForbiddenTool"
    description = "A tool not in the whitelist"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, params):
        return ToolResult(content="should not run", is_error=False)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestForkedAgentConstruction:
    def test_allowed_tools_filtering(self):
        agent = ForkedAgent(
            parent_system_prompt="test",
            allowed_tools=["DummyTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[_DummyTool(), _ForbiddenTool()],
        )
        assert "DummyTool" in agent._tool_map
        assert "ForbiddenTool" not in agent._tool_map


class TestForkedAgentExecuteTool:
    def test_rejects_non_whitelisted_tool(self):
        agent = ForkedAgent(
            parent_system_prompt="test",
            allowed_tools=["DummyTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[_DummyTool()],
        )
        tc = ToolCall(id="1", name="ForbiddenTool", input={})
        result = _run(agent._execute_tool(tc))
        assert result.is_error is True
        assert "Permission denied" in result.content

    def test_executes_whitelisted_tool(self):
        agent = ForkedAgent(
            parent_system_prompt="test",
            allowed_tools=["DummyTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[_DummyTool()],
        )
        tc = ToolCall(id="1", name="DummyTool", input={})
        result = _run(agent._execute_tool(tc))
        assert result.is_error is False
        assert result.content == "dummy result"


class TestForkedAgentRun:
    def test_single_turn_text_response(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_response.text = "Hello from forked agent"
        mock_client.complete = AsyncMock(return_value=mock_response)

        agent = ForkedAgent(
            parent_system_prompt="test prompt",
            allowed_tools=[],
            llm_client=mock_client,
            model_config=MagicMock(),
            tools=[],
        )
        result = _run(agent.run("What is 2+2?"))
        assert result == "Hello from forked agent"
        mock_client.complete.assert_awaited_once()
