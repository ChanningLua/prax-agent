"""Unit tests for IntentGateMiddleware (prax/core/intent_gate.py)."""
from __future__ import annotations

import pytest

from prax.core.intent_gate import (
    IntentGateMiddleware,
    _extract_pre_tool_text,
    _has_intent,
)
from prax.core.llm_client import LLMResponse
from prax.core.middleware import RuntimeState


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state(messages=None, metadata=None):
    from unittest.mock import MagicMock
    ctx = MagicMock()
    return RuntimeState(
        messages=messages or [],
        context=ctx,
        iteration=1,
        metadata=metadata or {},
    )


def _response_with_tool(pre_text: str = "") -> LLMResponse:
    """Build an LLMResponse that has a tool call, optionally preceded by text."""
    content = []
    if pre_text:
        content.append({"type": "text", "text": pre_text})
    content.append({"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {}})
    return LLMResponse(content=content, stop_reason="tool_use")


def _response_no_tool() -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": "Just a plain answer."}],
        stop_reason="end_turn",
    )


# ── _extract_pre_tool_text ────────────────────────────────────────────────────

class TestExtractPreToolText:
    def test_returns_empty_when_no_content(self):
        assert _extract_pre_tool_text([]) == ""

    def test_returns_text_before_tool(self):
        content = [
            {"type": "text", "text": "I will do something."},
            {"type": "tool_use", "name": "T"},
            {"type": "text", "text": "After tool"},
        ]
        result = _extract_pre_tool_text(content)
        assert "I will do something" in result
        assert "After tool" not in result

    def test_ignores_non_dict_blocks(self):
        content = ["not a dict", {"type": "tool_use", "name": "T"}]
        result = _extract_pre_tool_text(content)
        assert result == ""

    def test_returns_empty_when_tool_is_first(self):
        content = [{"type": "tool_use", "name": "T"}, {"type": "text", "text": "after"}]
        assert _extract_pre_tool_text(content) == ""


# ── _has_intent ───────────────────────────────────────────────────────────────

class TestHasIntent:
    def test_long_text_always_passes(self):
        assert _has_intent("A" * 25, min_length=20) is True

    def test_short_text_without_keywords_fails(self):
        assert _has_intent("ok", min_length=20) is False

    def test_empty_text_fails(self):
        assert _has_intent("", min_length=20) is False

    def test_keyword_match_passes_even_short(self):
        assert _has_intent("i will fix", min_length=20) is True

    def test_chinese_keyword_passes(self):
        assert _has_intent("我将修改代码", min_length=20) is True


# ── IntentGateMiddleware ──────────────────────────────────────────────────────

class TestIntentGateMiddlewareNonStrict:
    def setup_method(self):
        self.mw = IntentGateMiddleware(strict=False)

    @pytest.mark.asyncio
    async def test_response_without_tools_passes_through(self):
        state = _make_state()
        resp = _response_no_tool()
        result = await self.mw.after_model(state, resp)
        assert result is resp
        assert self.mw.violations == 0

    @pytest.mark.asyncio
    async def test_tool_with_good_intent_no_violation(self):
        state = _make_state()
        resp = _response_with_tool("I will update the todo list with the new items.")
        await self.mw.after_model(state, resp)
        assert self.mw.violations == 0

    @pytest.mark.asyncio
    async def test_tool_without_intent_increments_violation(self):
        state = _make_state()
        resp = _response_with_tool("")  # no pre-text
        await self.mw.after_model(state, resp)
        assert self.mw.violations == 1

    @pytest.mark.asyncio
    async def test_no_violation_metadata_in_non_strict_mode(self):
        state = _make_state()
        resp = _response_with_tool("")
        await self.mw.after_model(state, resp)
        # In non-strict mode, intent_gate_violation must NOT be set
        assert not state.metadata.get("intent_gate_violation", False)

    @pytest.mark.asyncio
    async def test_before_model_noop_in_non_strict(self):
        state = _make_state(metadata={"intent_gate_violation": True})
        await self.mw.before_model(state)
        # No message should be injected
        assert state.messages == []


class TestIntentGateMiddlewareStrict:
    def setup_method(self):
        self.mw = IntentGateMiddleware(strict=True)

    @pytest.mark.asyncio
    async def test_violation_sets_metadata_in_strict_mode(self):
        state = _make_state()
        resp = _response_with_tool("")
        await self.mw.after_model(state, resp)
        assert state.metadata.get("intent_gate_violation") is True

    @pytest.mark.asyncio
    async def test_before_model_injects_reminder_after_violation(self):
        state = _make_state(metadata={"intent_gate_violation": True})
        await self.mw.before_model(state)
        assert len(state.messages) == 1
        assert "intent" in state.messages[0]["content"].lower() or \
               "意图" in state.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_before_model_clears_violation_flag(self):
        state = _make_state(metadata={"intent_gate_violation": True})
        await self.mw.before_model(state)
        assert "intent_gate_violation" not in state.metadata
