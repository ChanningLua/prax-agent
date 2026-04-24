"""Typed envelope for agent-to-agent result passing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class AgentMessage:
    """Typed envelope for agent-to-agent result passing."""

    sender: str
    content: str
    status: Literal["ok", "error", "timeout"] = "ok"
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None

    def to_text(self) -> str:
        """Downgrade to plain text for backward-compat injection into messages."""
        if self.status != "ok":
            return f"[{self.status.upper()}] {self.content}"
        return self.content

    @classmethod
    def ok(cls, sender: str, content: str, **kw: Any) -> "AgentMessage":
        return cls(sender=sender, content=content, status="ok", **kw)

    @classmethod
    def error(cls, sender: str, content: str, **kw: Any) -> "AgentMessage":
        return cls(sender=sender, content=content, status="error", **kw)
