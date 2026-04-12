"""Structured stream events for agent loop execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .trace import SpanEndEvent, SpanStartEvent


@dataclass(frozen=True)
class MessageStartEvent:
    """Event emitted at the start of each agent iteration."""
    type: str = "message_start"
    session_id: str | None = None
    iteration: int = 0


@dataclass(frozen=True)
class ToolMatchEvent:
    """Event emitted when a tool call is matched from LLM response."""
    type: str = "tool_match"
    tool_name: str = ""
    tool_id: str = ""
    tool_input: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolStartEvent:
    """Event emitted when a tool execution starts."""
    type: str = "tool_start"
    tool_name: str = ""
    tool_id: str = ""


@dataclass(frozen=True)
class ToolResultEvent:
    """Event emitted when a tool execution completes."""
    type: str = "tool_result"
    tool_name: str = ""
    tool_id: str = ""
    is_error: bool = False
    content_preview: str = ""  # First 200 characters


@dataclass(frozen=True)
class MessageDeltaEvent:
    """Event emitted for text chunks from LLM."""
    type: str = "message_delta"
    text: str = ""


@dataclass(frozen=True)
class MessageStopEvent:
    """Event emitted when agent loop completes."""
    type: str = "message_stop"
    session_id: str | None = None
    stop_reason: str = ""
    iterations: int = 0
    usage: dict[str, int] | None = None
    had_tool_errors: bool = False
    verification_passed: bool = False


@dataclass(frozen=True)
class AgentResultEvent:
    """Emitted when a sub-agent completes and returns AgentMessage."""
    type: str = "agent_result"
    sender: str = ""
    status: str = "ok"
    content_preview: str = ""   # first 200 chars


# Union type alias for all stream events
StreamEvent = (
    MessageStartEvent
    | ToolMatchEvent
    | ToolStartEvent
    | ToolResultEvent
    | MessageDeltaEvent
    | MessageStopEvent
    | AgentResultEvent
    | SpanStartEvent
    | SpanEndEvent
)

__all__ = [
    "MessageStartEvent",
    "ToolMatchEvent",
    "ToolStartEvent",
    "ToolResultEvent",
    "MessageDeltaEvent",
    "MessageStopEvent",
    "AgentResultEvent",
    "SpanStartEvent",
    "SpanEndEvent",
    "StreamEvent",
]
