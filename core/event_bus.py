"""EventBus — lightweight observer for agent loop stream events.

Replaces the six on_xxx callback parameters in run_agent_loop with a
single bus object.  Callers register typed handlers:

    bus = EventBus()
    bus.on(ToolResultEvent, lambda e: print(e.tool_name, e.is_error))
    bus.on(MessageStopEvent, my_completion_handler)

The bus is synchronous-dispatch: handlers are called in registration order
immediately when emit() is called (still inside the async loop, but the
handlers themselves may be plain callables or async coroutines).

Design notes:
- Handlers that raise are caught and logged; they never crash the agent loop.
- A single handler can subscribe to multiple event types by calling on() twice.
- emit() accepts any StreamEvent subtype; unregistered types are silently ignored.
- No external deps — stdlib only.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Type

from .stream_events import StreamEvent

logger = logging.getLogger(__name__)

# Handler type: sync or async callable that receives one StreamEvent
Handler = Callable[[Any], Any]


class EventBus:
    """Observer hub for StreamEvents.

    Usage::

        bus = EventBus()

        @bus.on(ToolResultEvent)
        def handle_tool(event: ToolResultEvent) -> None:
            print(event.tool_name)

        # In the agent loop:
        await bus.emit(ToolResultEvent(tool_name="read", ...))
    """

    def __init__(self) -> None:
        # Maps event *class* → list of handlers
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    # ── Registration ──────────────────────────────────────────────────────

    def on(self, event_type: Type[StreamEvent], handler: Handler) -> "EventBus":
        """Register *handler* for *event_type*. Returns self for chaining."""
        self._handlers[event_type].append(handler)
        return self

    def __call__(self, event_type: Type[StreamEvent]) -> Callable[[Handler], Handler]:
        """Decorator form: @bus.on(SomeEvent) def handle(e): ..."""
        def decorator(fn: Handler) -> Handler:
            self.on(event_type, fn)
            return fn
        return decorator

    def off(self, event_type: Type[StreamEvent], handler: Handler) -> None:
        """Unregister a previously registered handler."""
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def clear(self, event_type: Type[StreamEvent] | None = None) -> None:
        """Remove all handlers, optionally scoped to one event type."""
        if event_type is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event_type, None)

    def merge(self, other: "EventBus") -> None:
        """Copy all handlers from *other* into this bus."""
        for event_type, handlers in other._handlers.items():
            for h in handlers:
                self.on(event_type, h)

    # ── Emission ──────────────────────────────────────────────────────────

    async def emit(self, event: StreamEvent) -> None:
        """Dispatch *event* to all registered handlers for its type.

        Sync handlers are called directly; async handlers are awaited.
        Exceptions in handlers are caught and logged so they never abort
        the agent loop.
        """
        handlers = self._handlers.get(type(event), [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "EventBus handler %r raised for event %s",
                    handler,
                    type(event).__name__,
                )

    def emit_sync(self, event: StreamEvent) -> None:
        """Fire-and-forget sync dispatch (skips async handlers).

        Only use this from non-async contexts where you cannot await.
        Async handlers registered for this event type will be skipped with
        a warning.
        """
        handlers = self._handlers.get(type(event), [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    logger.warning(
                        "EventBus.emit_sync: async handler %r skipped for %s",
                        handler,
                        type(event).__name__,
                    )
                    # Don't await — close the coroutine to avoid ResourceWarning
                    result.close()
            except Exception:
                logger.exception(
                    "EventBus handler %r raised for event %s",
                    handler,
                    type(event).__name__,
                )

    # ── Convenience: wire legacy callbacks onto the bus ───────────────────

    @classmethod
    def from_callbacks(
        cls,
        *,
        on_text: Callable[[str], None] | None = None,
        on_tool_call: Callable[[Any], None] | None = None,
        on_tool_result: Callable[[Any, Any], None] | None = None,
        on_complete: Callable[[Any], None] | None = None,
        on_event: Callable[[StreamEvent], None] | None = None,
    ) -> "EventBus":
        """Build an EventBus that wraps legacy on_xxx callbacks.

        This makes it trivial to keep backwards-compat while callers migrate
        to the bus API.  Each non-None callback is wrapped in a thin adapter
        and registered on the returned bus.
        """
        from .stream_events import (
            MessageDeltaEvent,
            MessageStopEvent,
            ToolMatchEvent,
            ToolResultEvent,
        )

        bus = cls()

        if on_text is not None:
            bus.on(MessageDeltaEvent, lambda e: on_text(e.text))

        if on_tool_call is not None:
            # on_tool_call(tool_call) — we carry the raw ToolCall via metadata
            # ToolMatchEvent carries tool_name/tool_id/tool_input; reconstruct
            # a minimal stand-in if callers need the full ToolCall object they
            # should switch to ToolMatchEvent directly.
            bus.on(ToolMatchEvent, lambda e: on_tool_call(e))

        if on_tool_result is not None:
            # Legacy signature: on_tool_result(tool_call, result)
            # ToolResultEvent doesn't carry both; wrap with a closure that
            # captures the last ToolMatchEvent for pairing.
            _last_match: list[Any] = [None]

            def _capture_match(e: Any) -> None:
                _last_match[0] = e

            def _fire_result(e: Any) -> None:
                on_tool_result(_last_match[0], e)

            bus.on(ToolMatchEvent, _capture_match)
            bus.on(ToolResultEvent, _fire_result)

        if on_complete is not None:
            bus.on(MessageStopEvent, on_complete)

        if on_event is not None:
            # Wire to every event type
            from .stream_events import (
                MessageStartEvent,
                ToolStartEvent,
            )
            for et in (
                MessageStartEvent,
                ToolMatchEvent,
                ToolStartEvent,
                ToolResultEvent,
                MessageDeltaEvent,
                MessageStopEvent,
            ):
                bus.on(et, on_event)

        return bus
