"""Main Textual TUI application."""
from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.binding import Binding

from ..core.event_bus import EventBus
from ..core.stream_events import ToolStartEvent, MessageStartEvent
from .widgets.log_viewer import LogViewer
from .widgets.session_list import SessionList
from .widgets.agent_status import AgentStatus


class PraxTUI(App):
    """Prax TUI control plane."""

    CSS = """
    Screen {
        layout: horizontal;
    }

    #session_list {
        width: 25%;
        border: solid cyan;
    }

    #log_viewer {
        width: 50%;
        border: solid yellow;
    }

    #agent_status {
        width: 25%;
        border: solid cyan;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "clear_log", "Clear Log"),
    ]

    def __init__(
        self,
        cwd: str,
        event_bus: EventBus,
        agent_name: str = "unknown",
        model_name: str = "unknown",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.cwd = cwd
        self.event_bus = event_bus
        self.agent_name = agent_name
        self.model_name = model_name

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        with Horizontal():
            yield SessionList(self.cwd, id="session_list")
            yield LogViewer(self.event_bus, id="log_viewer")
            yield AgentStatus(id="agent_status")

    def on_mount(self) -> None:
        """Initialize after mounting."""
        self.title = "Prax TUI Control Plane"
        self.sub_title = f"CWD: {self.cwd}"

        status = self.query_one("#agent_status", AgentStatus)
        status.update_agent(self.agent_name, self.model_name)

        # Subscribe to events for status updates
        self.event_bus.on(ToolStartEvent, self._on_tool_start)
        self.event_bus.on(MessageStartEvent, self._on_message_start)

    def _on_tool_start(self, event: ToolStartEvent) -> None:
        status = self.query_one("#agent_status", AgentStatus)
        status.increment_tool_calls()

    def _on_message_start(self, event: MessageStartEvent) -> None:
        status = self.query_one("#agent_status", AgentStatus)
        status.increment_iteration()

    def action_refresh(self) -> None:
        """Refresh session list."""
        session_list = self.query_one("#session_list", SessionList)
        session_list.clear()
        session_list._load_sessions()

    def action_clear_log(self) -> None:
        """Clear log viewer."""
        log_viewer = self.query_one("#log_viewer", LogViewer)
        log_viewer.clear()
