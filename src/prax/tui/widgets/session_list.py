"""Session list widget for browsing historical sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual.widgets import ListView, ListItem, Label
from textual.reactive import reactive
from textual.message import Message


class SessionListItem(ListItem):
    """Single session item with metadata."""

    def __init__(self, session_id: str, message_count: int, timestamp: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.session_id = session_id
        self.message_count = message_count
        self.timestamp = timestamp

    def compose(self):
        yield Label(f"[bold]{self.session_id[:12]}...[/bold]")
        yield Label(f"  {self.message_count} msgs • {self.timestamp}")


class SessionList(ListView):
    """Left pane: historical sessions from .prax/sessions/."""

    current_session: reactive[str | None] = reactive(None)

    def __init__(self, cwd: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cwd = cwd
        self.sessions_dir = Path(cwd) / ".prax" / "sessions"

    def on_mount(self) -> None:
        """Load sessions when widget mounts."""
        self._load_sessions()

    def _load_sessions(self) -> None:
        """Load all sessions from disk."""
        if not self.sessions_dir.exists():
            return

        sessions = []
        for session_file in sorted(self.sessions_dir.glob("*.json"), reverse=True):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    session_id = data.get("session_id", session_file.stem)
                    messages = data.get("messages", [])
                    timestamp = data.get("created_at", "unknown")

                    # Parse timestamp for display
                    if timestamp != "unknown":
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                            timestamp = dt.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            pass

                    sessions.append({
                        "id": session_id,
                        "count": len(messages),
                        "timestamp": timestamp,
                    })
            except Exception:
                continue

        # Add items to list
        for session in sessions[:50]:  # Limit to 50 most recent
            item = SessionListItem(
                session["id"],
                session["count"],
                session["timestamp"],
            )
            self.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle session selection."""
        if isinstance(event.item, SessionListItem):
            self.current_session = event.item.session_id
            # Post custom message for app to handle
            self.post_message(SessionSelected(event.item.session_id))


class SessionSelected(Message):
    """Custom message when a session is selected."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id
