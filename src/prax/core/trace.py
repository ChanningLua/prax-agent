"""Structured observability — TraceContext and SpanEvent types.

Provides hierarchical span tracking for agent loop execution.
Each top-level run_agent_loop call creates a root TraceContext;
child spans are created via TraceContext.child().
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceContext:
    """Carries trace/span identity and timing for one unit of work."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    started_at: float  # time.monotonic()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, metadata: dict[str, Any] | None = None) -> "TraceContext":
        """Create a new root TraceContext (no parent)."""
        return cls(
            trace_id=str(uuid.uuid4()),
            span_id=str(uuid.uuid4()),
            parent_span_id=None,
            started_at=time.monotonic(),
            metadata=metadata or {},
        )

    def child(self, name: str) -> "TraceContext":
        """Create a child span with the same trace_id."""
        ctx = TraceContext(
            trace_id=self.trace_id,
            span_id=str(uuid.uuid4()),
            parent_span_id=self.span_id,
            started_at=time.monotonic(),
            metadata={"span_name": name},
        )
        return ctx

    def elapsed_ms(self) -> float:
        """Return milliseconds elapsed since this span started."""
        return (time.monotonic() - self.started_at) * 1000.0


@dataclass(frozen=True)
class SpanStartEvent:
    """Emitted when a named span begins."""

    trace_id: str
    span_id: str
    span_name: str  # e.g. "agent_loop", "tool_call:Write", "llm_complete"
    parent_span_id: str | None = None
    type: str = "span_start"


@dataclass(frozen=True)
class SpanEndEvent:
    """Emitted when a named span ends."""

    trace_id: str
    span_id: str
    span_name: str
    duration_ms: float
    status: str  # "ok" | "error" | "timeout"
    metadata: dict[str, Any] = field(default_factory=dict)
    type: str = "span_end"
