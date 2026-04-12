"""Unit tests for EventBus (prax/core/event_bus.py).

No external deps — EventBus is pure stdlib. Tests cover:
  on / off / clear / merge / emit / emit_sync / from_callbacks
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prax.core.event_bus import EventBus
from prax.core.stream_events import (
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    ToolMatchEvent,
    ToolResultEvent,
    ToolStartEvent,
)


# ── on / registration ─────────────────────────────────────────────────────────


def test_on_registers_handler() -> None:
    bus = EventBus()
    handler = MagicMock()
    bus.on(ToolStartEvent, handler)
    assert handler in bus._handlers[ToolStartEvent]


def test_on_returns_self_for_chaining() -> None:
    bus = EventBus()
    result = bus.on(ToolStartEvent, lambda e: None)
    assert result is bus


def test_decorator_form_registers_handler() -> None:
    bus = EventBus()

    @bus(ToolStartEvent)
    def handle(e):
        pass

    assert handle in bus._handlers[ToolStartEvent]


# ── off / removal ─────────────────────────────────────────────────────────────


def test_off_removes_handler() -> None:
    bus = EventBus()
    handler = MagicMock()
    bus.on(ToolStartEvent, handler)
    bus.off(ToolStartEvent, handler)
    assert handler not in bus._handlers.get(ToolStartEvent, [])


def test_off_silently_ignores_unknown_handler() -> None:
    bus = EventBus()
    # Should not raise
    bus.off(ToolStartEvent, lambda e: None)


# ── clear ─────────────────────────────────────────────────────────────────────


def test_clear_all_removes_all_handlers() -> None:
    bus = EventBus()
    bus.on(ToolStartEvent, MagicMock())
    bus.on(MessageStartEvent, MagicMock())
    bus.clear()
    assert len(bus._handlers) == 0


def test_clear_scoped_removes_only_that_type() -> None:
    bus = EventBus()
    h1 = MagicMock()
    h2 = MagicMock()
    bus.on(ToolStartEvent, h1)
    bus.on(MessageStartEvent, h2)
    bus.clear(ToolStartEvent)
    assert h1 not in bus._handlers.get(ToolStartEvent, [])
    assert h2 in bus._handlers[MessageStartEvent]


# ── merge ─────────────────────────────────────────────────────────────────────


def test_merge_copies_handlers_from_other_bus() -> None:
    bus_a = EventBus()
    bus_b = EventBus()
    handler = MagicMock()
    bus_b.on(ToolStartEvent, handler)

    bus_a.merge(bus_b)

    assert handler in bus_a._handlers[ToolStartEvent]


# ── emit (async) ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_calls_sync_handler() -> None:
    bus = EventBus()
    handler = MagicMock()
    bus.on(ToolStartEvent, handler)

    event = ToolStartEvent(tool_name="Bash", tool_id="t1")
    await bus.emit(event)

    handler.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_emit_awaits_async_handler() -> None:
    bus = EventBus()
    async_handler = AsyncMock()
    bus.on(ToolStartEvent, async_handler)

    event = ToolStartEvent(tool_name="Read", tool_id="t2")
    await bus.emit(event)

    async_handler.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_emit_ignores_unregistered_event_type() -> None:
    bus = EventBus()
    # No handler for ToolStartEvent — should not raise
    await bus.emit(ToolStartEvent(tool_name="x", tool_id="y"))


@pytest.mark.asyncio
async def test_emit_catches_handler_exception_without_crashing() -> None:
    bus = EventBus()
    bad_handler = MagicMock(side_effect=RuntimeError("boom"))
    good_handler = MagicMock()
    bus.on(ToolStartEvent, bad_handler)
    bus.on(ToolStartEvent, good_handler)

    event = ToolStartEvent(tool_name="Bash", tool_id="t1")
    # Should not raise
    await bus.emit(event)
    # Good handler still called
    good_handler.assert_called_once_with(event)


# ── emit_sync ─────────────────────────────────────────────────────────────────


def test_emit_sync_calls_sync_handler() -> None:
    bus = EventBus()
    handler = MagicMock()
    bus.on(MessageStartEvent, handler)

    event = MessageStartEvent(iteration=1)
    bus.emit_sync(event)

    handler.assert_called_once_with(event)


def test_emit_sync_skips_async_handler_without_raising() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def async_handler(e):
        calls.append("async")

    bus.on(MessageStartEvent, async_handler)

    event = MessageStartEvent(iteration=2)
    # Should not raise; async handler is skipped
    bus.emit_sync(event)
    assert calls == []


# ── from_callbacks ────────────────────────────────────────────────────────────


def test_from_callbacks_on_text_receives_text() -> None:
    received: list[str] = []
    bus = EventBus.from_callbacks(on_text=lambda t: received.append(t))

    import asyncio

    asyncio.run(bus.emit(MessageDeltaEvent(text="hello world")))
    assert received == ["hello world"]


def test_from_callbacks_on_complete_called_on_stop_event() -> None:
    received = []
    bus = EventBus.from_callbacks(on_complete=lambda e: received.append(e))

    import asyncio

    stop = MessageStopEvent(stop_reason="end_turn")
    asyncio.run(bus.emit(stop))
    assert len(received) == 1
    assert received[0] is stop


def test_from_callbacks_on_event_called_for_all_types() -> None:
    """on_event should be invoked for every event type it is subscribed to."""
    calls: list[str] = []

    def on_event(e):
        calls.append(type(e).__name__)

    bus = EventBus.from_callbacks(on_event=on_event)

    import asyncio

    asyncio.run(bus.emit(MessageStartEvent(iteration=1)))
    asyncio.run(bus.emit(ToolStartEvent(tool_name="Bash", tool_id="t")))
    asyncio.run(bus.emit(MessageStopEvent(stop_reason="end_turn")))

    assert "MessageStartEvent" in calls
    assert "ToolStartEvent" in calls
    assert "MessageStopEvent" in calls
