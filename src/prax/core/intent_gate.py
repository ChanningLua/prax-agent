"""IntentGateMiddleware — enforce intent declaration before tool calls.

Encourages agents to verbalize their plan before invoking tools:
- After model response, checks whether meaningful text precedes the first tool call
- In strict mode, re-injects a reminder on the next turn if no intent was declared
- In non-strict mode, only logs a warning and tracks violation count

Usage:
    middlewares = [IntentGateMiddleware(strict=False)]   # default — monitor only
    middlewares = [IntentGateMiddleware(strict=True)]    # enforce via reminder injection
"""

from __future__ import annotations

import logging
from typing import Any

from .middleware import AgentMiddleware, LLMResponse, RuntimeState, PRIORITY_INJECT

logger = logging.getLogger(__name__)

# Minimum character count for text-before-tool to count as "intent declared"
_MIN_INTENT_LENGTH = 20

# Keyword patterns that signal an intent statement
_INTENT_KEYWORDS = (
    # English
    "i will", "i'll", "i plan", "i'm going to", "my plan", "my approach",
    "let me", "first i", "i need to", "i'll start", "i detect", "i found",
    "the approach", "to accomplish", "to complete", "to fix", "to implement",
    "step 1", "step 2",
    # Chinese
    "我将", "我会", "我的计划", "我检测到", "我发现", "我打算",
    "首先", "第一步", "方案", "思路", "计划",
)

_GATE_VIOLATION_KEY = "intent_gate_violation"
_REMINDER_MESSAGE = (
    "<system_reminder>\n"
    "Please briefly state your intent or plan BEFORE calling any tools. "
    "Describe what you are about to do and why, then proceed with tool calls.\n"
    "请在调用工具前先口述意图或计划，说明你接下来要做什么以及原因。\n"
    "</system_reminder>"
)


class IntentGateMiddleware(AgentMiddleware):
    """Enforce intent declaration before tool invocations.

    Args:
        strict: If True, inject a reminder message when intent is missing.
                If False (default), only log a warning and increment counter.
        min_length: Minimum character count of pre-tool text to accept as intent.
    """

    def __init__(self, *, strict: bool = False, min_length: int = _MIN_INTENT_LENGTH):
        self._strict = strict
        self._min_length = min_length
        self._violations = 0

    @property
    def violations(self) -> int:
        return self._violations

    async def before_model(self, state: RuntimeState) -> None:
        """Inject a reminder if the previous turn violated the intent gate."""
        if not self._strict:
            return
        if state.metadata.pop(_GATE_VIOLATION_KEY, False):
            state.messages.append({
                "role": "user",
                "content": _REMINDER_MESSAGE,
            })

    async def after_model(
        self, state: RuntimeState, response: LLMResponse
    ) -> LLMResponse:
        """Check whether model response includes intent text before first tool call.

        同时提取意图标签写入 state.metadata["detected_intent"]，
        供 ModelFallbackMiddleware 动态调整下一轮模型选择。
        """
        if not response.has_tool_calls:
            return response

        # Collect text blocks that appear before the first tool_use block
        text_before_tool = _extract_pre_tool_text(response.content)

        # 提取意图标签并写入 metadata
        detected_intent = _classify_intent(text_before_tool)
        if detected_intent:
            state.metadata["detected_intent"] = detected_intent

        if _has_intent(text_before_tool, self._min_length):
            return response

        # No meaningful intent declared
        self._violations += 1
        logger.debug(
            "IntentGate: missing intent before tool call (violation #%d, strict=%s)",
            self._violations,
            self._strict,
        )

        if self._strict:
            state.metadata[_GATE_VIOLATION_KEY] = True

        return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_pre_tool_text(content: list[Any]) -> str:
    """Return concatenated text from blocks that appear before the first tool_use."""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            break
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return " ".join(parts)


def _has_intent(text: str, min_length: int) -> bool:
    """Return True if text satisfies the intent declaration criteria."""
    stripped = text.strip()
    if len(stripped) >= min_length:
        return True
    text_lower = stripped.lower()
    return any(kw in text_lower for kw in _INTENT_KEYWORDS)


_INTENT_KEYWORD_MAP: dict[str, list[str]] = {
    "debugging": ["debug", "fix bug", "error", "exception", "traceback", "not working", "修复", "报错"],
    "architecture": ["architect", "design", "structure", "refactor", "重构", "架构"],
    "deep_research": ["research", "investigate", "analyze", "survey", "compare", "分析", "调研"],
    "chinese_content": ["中文", "中国", "汉字", "普通话"],
    "quick_tasks": ["quick", "simple", "brief", "summarize", "简单", "总结"],
}


def _classify_intent(text: str) -> str | None:
    """从响应文本中提取意图标签，返回 None 表示无法识别。"""
    text_lower = text.lower()
    for intent, keywords in _INTENT_KEYWORD_MAP.items():
        if any(kw in text_lower for kw in keywords):
            return intent
    return None
