"""Tests for prax/core/compaction.py."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prax.core.compaction import (
    SUMMARY_PREFIX,
    CompactionConfig,
    _CLEARED_PLACEHOLDER,
    compact_messages,
    estimate_tokens,
    micro_compact,
    session_memory_compact,
    should_compact,
    standard_compact,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_tool_result_msg(content: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": content}
        ],
    }


def make_text_msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


# ── micro_compact ────────────────────────────────────────────────────────────


def test_micro_compact_clears_large_tool_results_keeps_recent():
    # 4 large tool results; keep 2 recent
    messages = [make_tool_result_msg("x" * 300) for _ in range(4)]
    result = micro_compact(messages, keep_recent=2)

    cleared_count = sum(
        1
        for msg in result
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("content") == _CLEARED_PLACEHOLDER
    )
    intact_count = sum(
        1
        for msg in result
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("content") == "x" * 300
    )

    assert cleared_count == 2
    assert intact_count == 2


def test_micro_compact_doesnt_clear_short_results():
    messages = [make_tool_result_msg("short") for _ in range(5)]
    result = micro_compact(messages, keep_recent=2)

    cleared_count = sum(
        1
        for msg in result
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("content") == _CLEARED_PLACEHOLDER
    )
    assert cleared_count == 0


def test_micro_compact_doesnt_clear_already_cleared():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": _CLEARED_PLACEHOLDER}
            ],
        }
    ]
    result = micro_compact(messages, keep_recent=0)

    assert result[0]["content"][0]["content"] == _CLEARED_PLACEHOLDER


def test_micro_compact_doesnt_modify_non_user_messages():
    messages = [
        {"role": "assistant", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 500}]},
    ]
    result = micro_compact(messages, keep_recent=0)

    # Assistant message should not be touched
    assert result[0]["content"][0]["content"] == "x" * 500


def test_micro_compact_handles_string_content():
    messages = [make_text_msg("user", "Just text " * 100)]
    result = micro_compact(messages, keep_recent=2)

    # String content messages are not touched
    assert result[0]["content"] == "Just text " * 100


def test_micro_compact_empty_messages():
    result = micro_compact([])
    assert result == []


def test_micro_compact_returns_same_when_nothing_to_clear():
    messages = [make_tool_result_msg("short result")]
    result = micro_compact(messages, keep_recent=3)

    assert result == messages


# ── session_memory_compact ───────────────────────────────────────────────────


def test_session_memory_compact_uses_last_summarized_id():
    messages = [
        {"id": "msg1", "role": "user", "content": "Hello"},
        {"id": "msg2", "role": "assistant", "content": "Hi"},
        {"id": "msg3", "role": "user", "content": "What now?"},
    ]

    result = session_memory_compact(
        messages,
        session_memory_summary="Previous discussion",
        last_summarized_id="msg2",
    )

    assert result[0]["content"] == f"{SUMMARY_PREFIX}Previous discussion"
    assert len(result) == 2  # summary + msg3
    assert result[1]["content"] == "What now?"


def test_session_memory_compact_falls_back_to_keep_recent_when_id_not_found():
    messages = [
        {"id": "msg1", "role": "user", "content": "Hello"},
        {"id": "msg2", "role": "assistant", "content": "Hi"},
        {"id": "msg3", "role": "user", "content": "What now?"},
    ]

    result = session_memory_compact(
        messages,
        session_memory_summary="Summary",
        last_summarized_id="nonexistent-id",
        keep_recent=2,
    )

    assert result[0]["content"] == f"{SUMMARY_PREFIX}Summary"
    assert len(result) == 3  # summary + last 2 messages


def test_session_memory_compact_falls_back_when_no_id():
    messages = [
        {"id": "msg1", "role": "user", "content": "Hello"},
        {"id": "msg2", "role": "assistant", "content": "Hi"},
    ]

    result = session_memory_compact(
        messages,
        session_memory_summary="Summary",
        last_summarized_id=None,
        keep_recent=1,
    )

    assert result[0]["content"] == f"{SUMMARY_PREFIX}Summary"
    assert len(result) == 2  # summary + last 1


def test_session_memory_compact_ensures_at_least_1_recent_message():
    messages = [{"id": "msg1", "role": "user", "content": "Hello"}]

    # last_summarized_id matches the only message; no messages after it
    result = session_memory_compact(
        messages,
        session_memory_summary="Summary",
        last_summarized_id="msg1",
        keep_recent=5,
    )

    # Should have at least 1 recent message (fallback to last 1)
    assert len(result) >= 2  # summary + at least 1 message


def test_session_memory_compact_summary_message_format():
    messages = [{"id": "msg1", "role": "user", "content": "Hello"}]

    result = session_memory_compact(
        messages,
        session_memory_summary="Summary text",
        last_summarized_id=None,
        keep_recent=1,
    )

    assert result[0]["role"] == "user"
    assert result[0]["content"].startswith(SUMMARY_PREFIX)
    assert "Summary text" in result[0]["content"]


# ── standard_compact ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_standard_compact_returns_messages_if_under_keep_recent_limit():
    messages = [make_text_msg("user", "Hello")]

    mock_client = AsyncMock()
    mock_config = MagicMock()

    result = await standard_compact(messages, mock_client, mock_config, keep_recent=5)

    assert result == messages
    mock_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_standard_compact_calls_llm_to_summarize():
    messages = [make_text_msg("user", f"Message {i}") for i in range(10)]

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Conversation summary"
    mock_client.complete = AsyncMock(return_value=mock_response)

    mock_config = MagicMock()

    result = await standard_compact(messages, mock_client, mock_config, keep_recent=3)

    mock_client.complete.assert_called_once()
    assert result[0]["content"] == f"{SUMMARY_PREFIX}Conversation summary"
    assert len(result) == 4  # summary + 3 recent


@pytest.mark.asyncio
async def test_standard_compact_handles_llm_failure():
    messages = [make_text_msg("user", f"Message {i}") for i in range(10)]

    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(side_effect=Exception("LLM error"))

    mock_config = MagicMock()

    result = await standard_compact(messages, mock_client, mock_config, keep_recent=3)

    assert len(result) == 4  # summary + 3 recent
    assert "Compaction failed" in result[0]["content"]


@pytest.mark.asyncio
async def test_standard_compact_formats_string_content():
    messages = [
        make_text_msg("user", f"Text message {i}") for i in range(10)
    ]

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Summary"
    mock_client.complete = AsyncMock(return_value=mock_response)

    mock_config = MagicMock()
    result = await standard_compact(messages, mock_client, mock_config, keep_recent=2)

    # Verify the LLM was called with the conversation text
    call_args = mock_client.complete.call_args
    prompt_content = call_args[1]["messages"][0]["content"]
    assert "USER" in prompt_content


@pytest.mark.asyncio
async def test_standard_compact_formats_list_content_text():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": f"Response {i}"}]
        }
        for i in range(10)
    ]

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Summary"
    mock_client.complete = AsyncMock(return_value=mock_response)

    mock_config = MagicMock()
    result = await standard_compact(messages, mock_client, mock_config, keep_recent=2)

    assert len(result) == 3  # summary + 2 recent


@pytest.mark.asyncio
async def test_standard_compact_formats_tool_use_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"t{i}", "name": "Write", "input": {}}]
        }
        for i in range(10)
    ]

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Summary"
    mock_client.complete = AsyncMock(return_value=mock_response)

    mock_config = MagicMock()
    result = await standard_compact(messages, mock_client, mock_config, keep_recent=2)

    call_args = mock_client.complete.call_args
    prompt_content = call_args[1]["messages"][0]["content"]
    assert "tool: Write" in prompt_content


@pytest.mark.asyncio
async def test_standard_compact_formats_tool_result_blocks():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": f"Result {i}"}
            ]
        }
        for i in range(10)
    ]

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Summary"
    mock_client.complete = AsyncMock(return_value=mock_response)

    mock_config = MagicMock()
    result = await standard_compact(messages, mock_client, mock_config, keep_recent=2)

    call_args = mock_client.complete.call_args
    prompt_content = call_args[1]["messages"][0]["content"]
    assert "tool_result" in prompt_content


# ── estimate_tokens ──────────────────────────────────────────────────────────


def test_estimate_tokens_string_content():
    messages = [make_text_msg("user", "A" * 400)]
    tokens = estimate_tokens(messages)

    assert tokens == 100  # 400 chars / 4


def test_estimate_tokens_list_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "B" * 400}
            ]
        }
    ]
    tokens = estimate_tokens(messages)

    # Counts all string values in blocks: "text" key (4 chars) + "B"*400 + "type" (4 chars)
    # The exact value depends on how many string values exist in the dict
    assert tokens > 0


def test_estimate_tokens_empty_messages():
    tokens = estimate_tokens([])
    assert tokens == 0


# ── should_compact ───────────────────────────────────────────────────────────


def test_should_compact_under_threshold_returns_false():
    messages = [make_text_msg("user", "Short message")]
    result = should_compact(messages, context_window_tokens=10000, buffer_tokens=5000)

    assert result is False


def test_should_compact_over_threshold_returns_true():
    # Create enough tokens to exceed threshold
    # threshold = 10000 - 5000 = 5000 tokens = 20000 chars
    messages = [make_text_msg("user", "A" * 80_000)]
    result = should_compact(messages, context_window_tokens=10_000, buffer_tokens=5_000)

    assert result is True


# ── compact_messages ─────────────────────────────────────────────────────────


def test_compact_messages_under_max_returns_micro_compacted():
    messages = [
        make_tool_result_msg("x" * 500),
        make_tool_result_msg("y" * 500),
        make_tool_result_msg("z" * 500),
    ]
    config = CompactionConfig(max_messages=100, keep_recent=20, micro_keep_recent_tool_results=1)

    result = compact_messages(messages, config)

    # Total messages: 3, which is < max_messages=100
    assert len(result) == 3
    # Micro compaction applied: keep last 1, clear first 2
    cleared = sum(
        1
        for msg in result
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("content") == _CLEARED_PLACEHOLDER
    )
    assert cleared == 2


def test_compact_messages_over_max_trims_to_keep_recent():
    messages = [make_text_msg("user", f"Message {i}") for i in range(20)]
    config = CompactionConfig(max_messages=10, keep_recent=5)

    result = compact_messages(messages, config)

    assert len(result) == 5


def test_compact_messages_exactly_at_max_returns_micro_compacted():
    messages = [make_text_msg("user", f"Message {i}") for i in range(10)]
    config = CompactionConfig(max_messages=10, keep_recent=5)

    result = compact_messages(messages, config)

    # 10 <= 10, so just returns micro-compacted (no trimming)
    assert len(result) == 10
