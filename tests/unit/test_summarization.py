"""Unit tests for prax.core.summarization — LLM-driven history compression."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from prax.core.summarization import SummarizationMiddleware, _format_messages_for_summary
from prax.core.middleware import RuntimeState
from prax.core.llm_client import LLMResponse


def _mock_state(message_count: int) -> RuntimeState:
    """Create a RuntimeState with N messages."""
    messages = [
        {"role": "user", "content": f"message {i}"}
        for i in range(message_count)
    ]
    return RuntimeState(
        messages=messages,
        context=MagicMock(),
        iteration=0,
    )


@pytest.mark.asyncio
async def test_before_model_triggers_compress_when_exceeds_max_messages():
    """before_model triggers _compress when message count > max_messages."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "summary text"}],
        stop_reason="end_turn",
    ))
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
        max_messages=10,
        keep_recent=3,
    )

    state = _mock_state(11)
    await middleware.before_model(state)

    # Should have called LLM to summarize
    mock_client.complete.assert_awaited_once()
    # Should have replaced messages: 1 summary + 3 recent = 4 total
    assert len(state.messages) == 4
    assert "[Summary of earlier conversation]" in state.messages[0]["content"]
    assert "summary text" in state.messages[0]["content"]


@pytest.mark.asyncio
async def test_before_model_triggers_compress_when_exceeds_token_estimate():
    """before_model triggers _compress when estimated tokens > 40000."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "compressed"}],
        stop_reason="end_turn",
    ))
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
        max_messages=100,
        keep_recent=2,
    )

    # Create 5 messages with total chars > 160000 (40000 * 4)
    state = RuntimeState(
        messages=[
            {"role": "user", "content": "x" * 40001},
            {"role": "assistant", "content": "y" * 40001},
            {"role": "user", "content": "z" * 40001},
            {"role": "assistant", "content": "w" * 40001},
            {"role": "user", "content": "v" * 1000},
        ],
        context=MagicMock(),
        iteration=0,
    )

    await middleware.before_model(state)

    # Should have triggered compression despite message count < max_messages
    mock_client.complete.assert_awaited_once()
    assert len(state.messages) == 3  # 1 summary + 2 recent


@pytest.mark.asyncio
async def test_before_model_no_trigger_when_below_thresholds():
    """before_model is a no-op when below both thresholds."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock()
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
        max_messages=50,
        keep_recent=5,
    )

    state = _mock_state(10)
    await middleware.before_model(state)

    # Should NOT have called LLM
    mock_client.complete.assert_not_awaited()
    # Messages unchanged
    assert len(state.messages) == 10


@pytest.mark.asyncio
async def test_compress_replaces_old_messages_with_summary_and_keeps_recent():
    """_compress replaces old messages with summary + keeps keep_recent recent."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "summary of old messages"}],
        stop_reason="end_turn",
    ))
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
        max_messages=10,
        keep_recent=3,
    )

    state = _mock_state(8)
    original_recent = state.messages[-3:]

    await middleware._compress(state)

    # Should have 1 summary + 3 recent = 4 total
    assert len(state.messages) == 4
    assert state.messages[0]["role"] == "user"
    assert "[Summary of earlier conversation]" in state.messages[0]["content"]
    assert "summary of old messages" in state.messages[0]["content"]
    # Recent messages preserved
    assert state.messages[1:] == original_recent


@pytest.mark.asyncio
async def test_call_llm_summarize_returns_llm_text():
    """_call_llm_summarize returns the LLM response text."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "  concise summary  "}],
        stop_reason="end_turn",
    ))
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
    )

    messages = [{"role": "user", "content": "test"}]
    result = await middleware._call_llm_summarize(messages)

    assert result == "concise summary"
    mock_client.complete.assert_awaited_once()
    call_args = mock_client.complete.call_args
    assert call_args.kwargs["system_prompt"] == "You are a concise technical summarizer."
    assert call_args.kwargs["max_tokens"] == 1024
    assert call_args.kwargs["temperature"] == 0.3


@pytest.mark.asyncio
async def test_call_llm_summarize_returns_unavailable_on_empty_text():
    """_call_llm_summarize returns [Summary unavailable] when LLM returns empty text."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "   "}],
        stop_reason="end_turn",
    ))
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
    )

    messages = [{"role": "user", "content": "test"}]
    result = await middleware._call_llm_summarize(messages)

    assert result == "[Summary unavailable]"


@pytest.mark.asyncio
async def test_call_llm_summarize_returns_failed_message_on_exception():
    """_call_llm_summarize returns [Summary generation failed: ...] on exception."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=RuntimeError("API timeout"))
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
    )

    messages = [{"role": "user", "content": "test"}]
    result = await middleware._call_llm_summarize(messages)

    assert result.startswith("[Summary generation failed:")
    assert "API timeout" in result


def test_estimate_tokens_counts_chars_divided_by_4():
    """_estimate_tokens counts total chars // 4 across all message content."""
    mock_client = MagicMock()
    mock_cfg = MagicMock()

    middleware = SummarizationMiddleware(
        llm_client=mock_client,
        model_config=mock_cfg,
    )

    messages = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 200},
        {"role": "user", "content": [
            {"type": "text", "text": "c" * 50},
            {"type": "tool_use", "name": "bash", "input": {"cmd": "d" * 50}},
        ]},
    ]

    tokens = middleware._estimate_tokens(messages)

    # 100 + 200 + 50 + len('{"cmd": "' + 'd'*50 + '"}') = 100 + 200 + 50 + 60 = 410
    # 410 // 4 = 102
    assert tokens == 102


def test_format_messages_for_summary_handles_text_content():
    """_format_messages_for_summary converts text messages to readable format."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    result = _format_messages_for_summary(messages)

    assert "USER: hello" in result
    assert "ASSISTANT: world" in result


def test_format_messages_for_summary_handles_structured_content():
    """_format_messages_for_summary handles tool_use and tool_result blocks."""
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "run bash"},
            {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
        ]},
        {"role": "assistant", "content": [
            {"type": "tool_result", "content": "file1.txt\nfile2.txt"},
        ]},
    ]

    result = _format_messages_for_summary(messages)

    assert "USER:" in result
    assert "run bash" in result
    assert "[Tool call: bash]" in result
    assert "ASSISTANT:" in result
    assert "[Tool result: file1.txt" in result
