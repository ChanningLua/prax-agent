"""Tests for Batch 2 stream events system."""
import pytest

from prax.core.agent_loop import run_agent_loop
from prax.core.context import Context
from prax.core.llm_client import LLMClient, LLMResponse, ModelConfig
from prax.core.stream_events import (
    MessageStartEvent,
    MessageStopEvent,
    ToolMatchEvent,
    ToolStartEvent,
    ToolResultEvent,
    MessageDeltaEvent,
)
from prax.tools.base import Tool, ToolResult, PermissionLevel


class MockTool(Tool):
    """Mock tool for testing."""
    name = "MockTool"
    description = "Mock tool for testing"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}
    permission_level = PermissionLevel.SAFE

    async def execute(self, params):
        return ToolResult(content="mock result", is_error=False)


class MockLLMClient(LLMClient):
    """Mock LLM client for testing."""

    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0

    async def complete(self, messages, tools, model_config, system_prompt, **kwargs):
        response = self.responses[self.call_count]
        self.call_count += 1
        return response


class TestStreamEvents:
    """Tests for stream events system."""

    @pytest.mark.asyncio
    async def test_complete_event_sequence(self):
        """Test that all events are emitted in correct order."""
        events = []

        def capture_event(event):
            events.append(event)

        # Mock LLM responses: first with tool call, then final text
        responses = [
            LLMResponse(
                content=[
                    {"type": "tool_use", "id": "tool1", "name": "MockTool", "input": {}}
                ],
                stop_reason="tool_use",
                usage={"input_tokens": 10, "output_tokens": 5}
            ),
            LLMResponse(
                content=[{"type": "text", "text": "Final answer"}],
                stop_reason="end_turn",
                usage={"input_tokens": 15, "output_tokens": 10}
            )
        ]

        context = Context(cwd="/tmp")
        llm_client = MockLLMClient(responses)
        model_config = ModelConfig(
            provider="mock",
            model="mock-model",
            base_url="http://mock",
            api_key="mock-key",
            api_format="openai"
        )
        tools = [MockTool()]

        await run_agent_loop(
            "test task",
            context=context,
            llm_client=llm_client,
            model_config=model_config,
            tools=tools,
            session_id="test-session",
            on_event=capture_event
        )

        # Verify event sequence
        assert len(events) >= 6
        assert isinstance(events[0], MessageStartEvent)
        assert events[0].session_id == "test-session"
        assert events[0].iteration == 0

        # Find tool events
        tool_match_events = [e for e in events if isinstance(e, ToolMatchEvent)]
        tool_start_events = [e for e in events if isinstance(e, ToolStartEvent)]
        tool_result_events = [e for e in events if isinstance(e, ToolResultEvent)]

        assert len(tool_match_events) == 1
        assert tool_match_events[0].tool_name == "MockTool"

        assert len(tool_start_events) == 1
        assert tool_start_events[0].tool_name == "MockTool"

        assert len(tool_result_events) == 1
        assert tool_result_events[0].tool_name == "MockTool"
        assert not tool_result_events[0].is_error

        # Find message delta and stop events
        delta_events = [e for e in events if isinstance(e, MessageDeltaEvent)]
        stop_events = [e for e in events if isinstance(e, MessageStopEvent)]

        assert len(delta_events) == 1
        assert delta_events[0].text == "Final answer"

        assert len(stop_events) == 1
        assert stop_events[0].session_id == "test-session"
        assert stop_events[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_budget_reached_event(self):
        """Test that budget limit triggers correct stop event."""
        events = []

        def capture_event(event):
            events.append(event)

        # First response uses tokens, second would exceed budget
        responses = [
            LLMResponse(
                content=[
                    {"type": "tool_use", "id": "tool1", "name": "MockTool", "input": {}}
                ],
                stop_reason="tool_use",
                usage={"input_tokens": 8, "output_tokens": 3}  # Total: 11 tokens
            ),
            LLMResponse(
                content=[{"type": "text", "text": "Response"}],
                stop_reason="end_turn",
                usage={"input_tokens": 5, "output_tokens": 5}
            )
        ]

        context = Context(cwd="/tmp")
        llm_client = MockLLMClient(responses)
        model_config = ModelConfig(
            provider="mock",
            model="mock-model",
            base_url="http://mock",
            api_key="mock-key",
            api_format="openai"
        )

        result = await run_agent_loop(
            "test task",
            context=context,
            llm_client=llm_client,
            model_config=model_config,
            tools=[MockTool()],
            max_budget_tokens=10,  # Budget exceeded after first response
            session_id="test-session",
            on_event=capture_event
        )

        # Should stop after first iteration due to budget
        assert "Budget limit reached" in result

        # Find stop event
        stop_events = [e for e in events if isinstance(e, MessageStopEvent)]
        assert len(stop_events) == 1
        assert stop_events[0].stop_reason == "max_budget_reached"

    @pytest.mark.asyncio
    async def test_backward_compatibility(self):
        """Test that old callbacks still work."""
        tool_calls = []
        tool_results = []
        texts = []
        reports = []

        def on_tool_call(tc):
            tool_calls.append(tc)

        def on_tool_result(tc, result):
            tool_results.append((tc, result))

        def on_text(text):
            texts.append(text)

        def on_complete(report):
            reports.append(report)

        responses = [
            LLMResponse(
                content=[{"type": "text", "text": "Final"}],
                stop_reason="end_turn",
                usage={}
            )
        ]

        context = Context(cwd="/tmp")
        llm_client = MockLLMClient(responses)
        model_config = ModelConfig(
            provider="mock",
            model="mock-model",
            base_url="http://mock",
            api_key="mock-key",
            api_format="openai",
            supports_streaming=False
        )

        await run_agent_loop(
            "test task",
            context=context,
            llm_client=llm_client,
            model_config=model_config,
            tools=[],
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_text=on_text,
            on_complete=on_complete
        )

        # Old callbacks should still work
        assert len(texts) == 1
        assert texts[0] == "Final"
        assert len(reports) == 1
        assert reports[0].stop_reason == "end_turn"
