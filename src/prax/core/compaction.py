"""Message compaction — three-tier compression for long conversations.

Tier 1 — Micro compaction (every iteration, no API call):
  Clear old tool result content, keep recent results intact.

Tier 2 — Session Memory compaction (preferred, no API call):
  Use SessionMemory summary as the conversation summary.
  Preserve recent messages after lastSummarizedMessageId.

Tier 3 — Standard compaction (fallback, requires API call):
  Call LLM to generate a full conversation summary.
  All messages replaced with summary + recent messages.

Auto-trigger threshold: effective_context_window - 13_000 buffer tokens.
Circuit breaker: stop after 3 consecutive compaction failures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SUMMARY_PREFIX = "[Summary] "

# Number of recent messages to keep in Tier 2 / Tier 3 compaction
_KEEP_RECENT_MESSAGES = 5
# Buffer before triggering auto-compaction
_CONTEXT_BUFFER_TOKENS = 13_000

# Tool types whose old results can be cleared in Micro compaction
_CLEARED_PLACEHOLDER = "[Old tool result content cleared]"


@dataclass
class CompactionConfig:
    max_messages: int = 100
    keep_recent: int = 20
    # For Micro compaction: keep this many recent tool results intact
    micro_keep_recent_tool_results: int = 3
    # Context window size (tokens) — used for auto-trigger
    context_window_tokens: int = 200_000


# ── Micro compaction ─────────────────────────────────────────────────────────

def micro_compact(
    messages: list[dict[str, Any]],
    keep_recent: int = 3,
) -> list[dict[str, Any]]:
    """Clear old tool result contents, keeping the most recent `keep_recent` intact.

    Only clears large string results from tool_result blocks — tools whose
    output is large but ephemeral (Read, Bash, Grep, Glob, etc.).
    Leaves structural messages (system, user text, assistant text) unchanged.
    """
    clearable_indices: list[tuple[int, int]] = []  # (msg_idx, block_idx)

    for msg_idx, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block_idx, block in enumerate(content):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and _is_clearable_result(block)
            ):
                clearable_indices.append((msg_idx, block_idx))

    to_clear = clearable_indices[:-keep_recent] if keep_recent > 0 else clearable_indices

    if not to_clear:
        return messages

    result = list(messages)
    for msg_idx, block_idx in to_clear:
        msg = dict(result[msg_idx])
        content = list(msg["content"])
        block = dict(content[block_idx])
        if block.get("content") != _CLEARED_PLACEHOLDER:
            block["content"] = _CLEARED_PLACEHOLDER
            content[block_idx] = block
            msg["content"] = content
            result[msg_idx] = msg

    return result


def _is_clearable_result(block: dict[str, Any]) -> bool:
    content = block.get("content", "")
    if content == _CLEARED_PLACEHOLDER:
        return False
    return isinstance(content, str) and len(content) > 200


# ── Session Memory compaction ────────────────────────────────────────────────

def session_memory_compact(
    messages: list[dict[str, Any]],
    session_memory_summary: str,
    last_summarized_id: str | None = None,
    keep_recent: int = _KEEP_RECENT_MESSAGES,
) -> list[dict[str, Any]]:
    """Replace old messages with session memory summary.

    If `last_summarized_id` is provided, messages after that ID are kept.
    Otherwise, keep the most recent `keep_recent` messages.
    """
    recent_messages: list[dict[str, Any]] = []

    if last_summarized_id and last_summarized_id != "none":
        found = False
        for msg in messages:
            if found:
                recent_messages.append(msg)
            if msg.get("id") == last_summarized_id:
                found = True
        if not found:
            recent_messages = messages[-keep_recent:]
    else:
        recent_messages = messages[-keep_recent:]

    if not recent_messages and messages:
        recent_messages = messages[-1:]

    summary_message: dict[str, Any] = {
        "role": "user",
        "content": f"{SUMMARY_PREFIX}{session_memory_summary}",
    }

    return [summary_message] + recent_messages


# ── Standard compaction ──────────────────────────────────────────────────────

async def standard_compact(
    messages: list[dict[str, Any]],
    llm_client: Any,
    model_config: Any,
    keep_recent: int = _KEEP_RECENT_MESSAGES,
) -> list[dict[str, Any]]:
    """Generate a conversation summary via LLM and replace old messages."""
    if len(messages) <= keep_recent:
        return messages

    messages_to_summarize = messages[:-keep_recent]
    recent_messages = messages[-keep_recent:]

    conversation_parts: list[str] = []
    for msg in messages_to_summarize:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            conversation_parts.append(f"{role.upper()}: {content[:1000]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        conversation_parts.append(f"{role.upper()}: {block.get('text', '')[:500]}")
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "?")
                        conversation_parts.append(f"{role.upper()} [tool: {name}]")
                    elif block.get("type") == "tool_result":
                        conversation_parts.append(
                            f"{role.upper()} [tool_result: {str(block.get('content', ''))[:200]}]"
                        )

    conversation_text = "\n".join(conversation_parts)

    prompt = f"""Summarize this conversation history concisely for context compaction.
Focus on: decisions made, files modified, errors encountered, current task state.
Be specific about file paths, function names, and important values.

CONVERSATION:
{conversation_text}

SUMMARY:"""

    try:
        response = await llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            model_config=model_config,
            system_prompt="You are a concise technical summarizer.",
        )
        summary_text = response.text.strip()
    except Exception as exc:
        logger.error("Standard compaction LLM call failed: %s", exc)
        summary_text = f"[Compaction failed: {exc}] Conversation truncated."

    summary_message: dict[str, Any] = {
        "role": "user",
        "content": f"{SUMMARY_PREFIX}{summary_text}",
    }

    return [summary_message] + list(recent_messages)


# ── Auto-trigger check ───────────────────────────────────────────────────────

def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate: ~4 chars per token."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    for val in block.values():
                        if isinstance(val, str):
                            total_chars += len(val)
    return total_chars // 4


def should_compact(
    messages: list[dict[str, Any]],
    context_window_tokens: int = 200_000,
    buffer_tokens: int = _CONTEXT_BUFFER_TOKENS,
) -> bool:
    """Return True if the message list is approaching the context window limit."""
    estimated = estimate_tokens(messages)
    threshold = context_window_tokens - buffer_tokens
    return estimated >= threshold


# ── Legacy compat ────────────────────────────────────────────────────────────

def compact_messages(
    messages: list[dict[str, Any]],
    config: CompactionConfig,
) -> list[dict[str, Any]]:
    """Compat wrapper: micro-compact then trim to keep_recent if over limit."""
    messages = micro_compact(messages, keep_recent=config.micro_keep_recent_tool_results)
    if len(messages) <= config.max_messages:
        return messages
    return messages[-config.keep_recent:]
