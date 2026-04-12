"""Real-time event log viewer widget."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.widgets import RichLog
from rich.text import Text

from ...core.event_bus import EventBus
from ...core.stream_events import (
    ToolStartEvent,
    ToolResultEvent,
    MessageStartEvent,
    MessageDeltaEvent,
    MessageStopEvent,
)


class LogViewer(RichLog):
    """Center pane: real-time event log stream."""

    def __init__(self, event_bus: EventBus, **kwargs: Any) -> None:
        super().__init__(**kwargs, wrap=True, highlight=True, markup=True)
        self.event_bus = event_bus
        self._subscribe()

    def _subscribe(self) -> None:
        """Subscribe to all relevant event types."""
        self.event_bus.on(ToolStartEvent, self._on_tool_start)
        self.event_bus.on(ToolResultEvent, self._on_tool_result)
        self.event_bus.on(MessageStartEvent, self._on_message_start)
        self.event_bus.on(MessageStopEvent, self._on_message_stop)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _on_tool_start(self, event: ToolStartEvent) -> None:
        ts = self._timestamp()
        text = Text()
        text.append(f"[{ts}] ", style="dim")
        text.append("🔧 ", style="bold cyan")
        text.append(f"{event.tool_name}", style="bold cyan")
        text.append(f" ({event.tool_id})", style="dim")
        self.write(text)

    def _on_tool_result(self, event: ToolResultEvent) -> None:
        ts = self._timestamp()
        status = "✗" if event.is_error else "✓"
        style = "red" if event.is_error else "green"
        text = Text()
        text.append(f"[{ts}] ", style="dim")
        text.append(f"{status} ", style=f"bold {style}")
        text.append(f"{event.tool_name}", style=style)
        if event.content_preview:
            text.append(f"  {event.content_preview[:80]}", style="dim")
        self.write(text)

    def _on_message_start(self, event: MessageStartEvent) -> None:
        ts = self._timestamp()
        text = Text()
        text.append(f"[{ts}] ", style="dim")
        text.append("💬 ", style="bold yellow")
        text.append(f"iteration={event.iteration}", style="yellow")
        self.write(text)

    def _on_message_stop(self, event: MessageStopEvent) -> None:
        ts = self._timestamp()
        text = Text()
        text.append(f"[{ts}] ", style="dim")
        text.append("✓ ", style="bold green")
        text.append(f"stop_reason={event.stop_reason}", style="green")
        self.write(text)
