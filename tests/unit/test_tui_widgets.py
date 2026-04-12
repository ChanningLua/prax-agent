"""Unit tests for Prax TUI widgets.

Covers:
  - prax/tui/widgets/agent_status.py  (AgentStatus)
  - prax/tui/widgets/log_viewer.py    (LogViewer)
  - prax/tui/widgets/session_list.py  (SessionList, SessionListItem, SessionSelected)

All Textual/Rich imports are mocked so no display is created and no real I/O occurs.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ── Shared Textual mock factory ───────────────────────────────────────────────


def _mock_textual():
    """Return a dict of fake modules suitable for patch.dict(sys.modules, ...)."""

    textual = types.ModuleType("textual")
    textual_app = types.ModuleType("textual.app")
    textual_containers = types.ModuleType("textual.containers")
    textual_binding = types.ModuleType("textual.binding")
    textual_widgets = types.ModuleType("textual.widgets")
    textual_reactive = types.ModuleType("textual.reactive")
    textual_message = types.ModuleType("textual.message")

    class FakeApp:
        CSS = ""
        BINDINGS = []

        def __init__(self, **kwargs):
            pass

        def run(self):
            pass

        def query_one(self, selector, widget_type=None):
            return MagicMock()

        def post_message(self, msg):
            pass

    class FakeComposeResult:
        pass

    class FakeHorizontal:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class FakeBinding:
        def __init__(self, *args, **kwargs):
            pass

    class FakeStatic:
        def __init__(self, **kwargs):
            pass

        def render(self):
            return ""

    class FakeRichLog:
        def __init__(self, **kwargs):
            pass

        def write(self, text):
            pass

        def clear(self):
            pass

    class FakeListView:
        def __init__(self, **kwargs):
            pass

        def append(self, item):
            pass

        def clear(self):
            pass

        def post_message(self, msg):
            pass

    class FakeListItem:
        def __init__(self, **kwargs):
            pass

        def compose(self):
            return iter([])

    class FakeLabel:
        def __init__(self, text, **kwargs):
            self.text = text

    class FakeMessage:
        def __init__(self):
            pass

    class FakeSelected:
        pass

    class FakeReactive:
        def __init__(self, default):
            self._default = default

        def __set_name__(self, owner, name):
            pass

    textual_app.App = FakeApp
    textual_app.ComposeResult = FakeComposeResult
    textual_containers.Horizontal = FakeHorizontal
    textual_binding.Binding = FakeBinding
    textual_widgets.Static = FakeStatic
    textual_widgets.RichLog = FakeRichLog
    textual_widgets.ListView = FakeListView
    textual_widgets.ListItem = FakeListItem
    textual_widgets.Label = FakeLabel
    textual_reactive.reactive = FakeReactive
    textual_message.Message = FakeMessage
    FakeListView.Selected = FakeSelected

    # Rich mocks
    rich = types.ModuleType("rich")
    rich_table = types.ModuleType("rich.table")
    rich_panel = types.ModuleType("rich.panel")
    rich_text = types.ModuleType("rich.text")

    class FakeTable:
        @staticmethod
        def grid(**kwargs):
            return FakeTable()

        def add_column(self, **kwargs):
            pass

        def add_row(self, *args):
            pass

    class FakePanel:
        def __init__(self, *args, **kwargs):
            pass

    class FakeText:
        def __init__(self, *args, **kwargs):
            self._parts: list[tuple[str, str | None]] = []

        def append(self, text, style=None):
            self._parts.append((text, style))

    rich_table.Table = FakeTable
    rich_panel.Panel = FakePanel
    rich_text.Text = FakeText
    rich.table = rich_table
    rich.panel = rich_panel
    rich.text = rich_text

    return {
        "textual": textual,
        "textual.app": textual_app,
        "textual.containers": textual_containers,
        "textual.binding": textual_binding,
        "textual.widgets": textual_widgets,
        "textual.reactive": textual_reactive,
        "textual.message": textual_message,
        "rich": rich,
        "rich.table": rich_table,
        "rich.panel": rich_panel,
        "rich.text": rich_text,
    }


def _clear_tui_modules():
    """Remove prax.tui.* from sys.modules so they are re-imported fresh."""
    for key in list(sys.modules):
        if "prax.tui" in key:
            del sys.modules[key]


# ═══════════════════════════════════════════════════════════════════════════════
# AgentStatus
# ═══════════════════════════════════════════════════════════════════════════════


def _make_agent_status():
    """Import AgentStatus with Textual mocked and return an instance."""
    import importlib

    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        status = AgentStatus()
        # Initialise reactive attrs as plain ints/strs (FakeReactive doesn't
        # implement descriptor storage, so we set them directly)
        status.agent_name = "unknown"
        status.model_name = "unknown"
        status.iteration = 0
        status.total_tokens = 0
        status.tool_calls = 0
        return status


def test_agent_status_update_agent() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        status = AgentStatus()
        status.agent_name = "unknown"
        status.model_name = "unknown"

        status.update_agent("ralph", "claude-3-5")
        assert status.agent_name == "ralph"
        assert status.model_name == "claude-3-5"


def test_agent_status_increment_iteration() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        status = AgentStatus()
        status.iteration = 0

        status.increment_iteration()
        assert status.iteration == 1
        status.increment_iteration()
        assert status.iteration == 2


def test_agent_status_increment_tool_calls() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        status = AgentStatus()
        status.tool_calls = 0

        status.increment_tool_calls()
        status.increment_tool_calls()
        assert status.tool_calls == 2


def test_agent_status_add_tokens() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        status = AgentStatus()
        status.total_tokens = 0

        status.add_tokens(100)
        status.add_tokens(250)
        assert status.total_tokens == 350


def test_agent_status_reset() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        status = AgentStatus()
        status.agent_name = "ralph"
        status.model_name = "claude-3-5"
        status.iteration = 5
        status.total_tokens = 9999
        status.tool_calls = 10

        status.reset()

        assert status.agent_name == "unknown"
        assert status.model_name == "unknown"
        assert status.iteration == 0
        assert status.total_tokens == 0
        assert status.tool_calls == 0


def test_agent_status_render_returns_panel() -> None:
    """render() should return a Panel without raising."""
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.agent_status")
        AgentStatus = mod.AgentStatus
        Panel = mocks["rich.panel"].Panel

        status = AgentStatus()
        status.agent_name = "ralph"
        status.model_name = "model"
        status.iteration = 3
        status.total_tokens = 500
        status.tool_calls = 7

        result = status.render()
        assert isinstance(result, Panel)


# ═══════════════════════════════════════════════════════════════════════════════
# LogViewer
# ═══════════════════════════════════════════════════════════════════════════════


def _make_log_viewer(mocks, event_bus):
    """Import LogViewer with Textual mocked and return an instance."""
    import importlib

    _clear_tui_modules()
    mod = importlib.import_module("prax.tui.widgets.log_viewer")
    LogViewer = mod.LogViewer
    viewer = LogViewer(event_bus=event_bus)
    return viewer, mod


def test_log_viewer_subscribe_registers_four_handlers() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        mock_bus = MagicMock()
        viewer, _ = _make_log_viewer(mocks, mock_bus)

        # on() should have been called 4 times (ToolStart, ToolResult, MessageStart, MessageStop)
        assert mock_bus.on.call_count == 4


def test_log_viewer_on_tool_start_calls_write() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        mock_bus = MagicMock()
        viewer, _ = _make_log_viewer(mocks, mock_bus)
        viewer.write = MagicMock()

        from prax.core.stream_events import ToolStartEvent

        event = ToolStartEvent(tool_name="Bash", tool_id="t1")
        viewer._on_tool_start(event)

        viewer.write.assert_called_once()
        written = viewer.write.call_args[0][0]
        # The written object should have "Bash" in its parts
        assert any("Bash" in part[0] for part in written._parts)


def test_log_viewer_on_tool_result_success_calls_write() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        mock_bus = MagicMock()
        viewer, _ = _make_log_viewer(mocks, mock_bus)
        viewer.write = MagicMock()

        from prax.core.stream_events import ToolResultEvent

        event = ToolResultEvent(
            tool_name="Read", tool_id="t2", content_preview="some output", is_error=False
        )
        viewer._on_tool_result(event)

        viewer.write.assert_called_once()
        written = viewer.write.call_args[0][0]
        assert any("Read" in part[0] for part in written._parts)


def test_log_viewer_on_tool_result_error_uses_red_style() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        mock_bus = MagicMock()
        viewer, _ = _make_log_viewer(mocks, mock_bus)
        viewer.write = MagicMock()

        from prax.core.stream_events import ToolResultEvent

        event = ToolResultEvent(
            tool_name="Bash", tool_id="t3", content_preview="err", is_error=True
        )
        viewer._on_tool_result(event)

        written = viewer.write.call_args[0][0]
        styles = [part[1] for part in written._parts if part[1]]
        assert any("red" in (s or "") for s in styles)


def test_log_viewer_on_message_start_calls_write() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        mock_bus = MagicMock()
        viewer, _ = _make_log_viewer(mocks, mock_bus)
        viewer.write = MagicMock()

        from prax.core.stream_events import MessageStartEvent

        event = MessageStartEvent(iteration=7)
        viewer._on_message_start(event)

        viewer.write.assert_called_once()
        written = viewer.write.call_args[0][0]
        assert any("iteration=7" in part[0] for part in written._parts)


def test_log_viewer_on_message_stop_calls_write() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        mock_bus = MagicMock()
        viewer, _ = _make_log_viewer(mocks, mock_bus)
        viewer.write = MagicMock()

        from prax.core.stream_events import MessageStopEvent

        event = MessageStopEvent(stop_reason="end_turn", usage={})
        viewer._on_message_stop(event)

        viewer.write.assert_called_once()
        written = viewer.write.call_args[0][0]
        assert any("end_turn" in part[0] for part in written._parts)


# ═══════════════════════════════════════════════════════════════════════════════
# SessionList / SessionListItem / SessionSelected
# ═══════════════════════════════════════════════════════════════════════════════


def _make_session_list(mocks, cwd: str):
    """Import SessionList with Textual mocked and return an instance."""
    import importlib

    _clear_tui_modules()
    mod = importlib.import_module("prax.tui.widgets.session_list")
    SessionList = mod.SessionList
    sl = SessionList(cwd=cwd)
    sl.append = MagicMock()  # intercept real Textual append
    return sl, mod


def test_session_list_load_sessions_missing_dir(tmp_path) -> None:
    """When sessions directory does not exist, _load_sessions is a no-op."""
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        sl, _ = _make_session_list(mocks, str(tmp_path))
        sl._load_sessions()
        sl.append.assert_not_called()


def test_session_list_load_sessions_reads_json_files(tmp_path) -> None:
    """Valid JSON session files are loaded and appended as items."""
    mocks = _mock_textual()
    sessions_dir = tmp_path / ".prax" / "sessions"
    sessions_dir.mkdir(parents=True)

    for i in range(3):
        (sessions_dir / f"sess-{i:04d}.json").write_text(
            json.dumps(
                {
                    "session_id": f"sess-{i}",
                    "messages": [{"role": "user"}] * (i + 1),
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )
        )

    with patch.dict(sys.modules, mocks):
        sl, _ = _make_session_list(mocks, str(tmp_path))
        sl._load_sessions()

    assert sl.append.call_count == 3


def test_session_list_load_sessions_limits_to_50(tmp_path) -> None:
    """Even with 60 session files, only the first 50 are appended."""
    mocks = _mock_textual()
    sessions_dir = tmp_path / ".prax" / "sessions"
    sessions_dir.mkdir(parents=True)

    for i in range(60):
        (sessions_dir / f"sess-{i:04d}.json").write_text(
            json.dumps({"session_id": f"s{i}", "messages": [], "created_at": "unknown"})
        )

    with patch.dict(sys.modules, mocks):
        sl, _ = _make_session_list(mocks, str(tmp_path))
        sl._load_sessions()

    assert sl.append.call_count == 50


def test_session_list_load_sessions_skips_invalid_json(tmp_path) -> None:
    """Corrupt JSON files are silently skipped."""
    mocks = _mock_textual()
    sessions_dir = tmp_path / ".prax" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "good.json").write_text(
        json.dumps({"session_id": "good", "messages": [], "created_at": "unknown"})
    )
    (sessions_dir / "bad.json").write_text("not-json{{{{")

    with patch.dict(sys.modules, mocks):
        sl, _ = _make_session_list(mocks, str(tmp_path))
        sl._load_sessions()

    # Only the good file should be loaded
    assert sl.append.call_count == 1


def test_session_list_item_stores_metadata() -> None:
    """SessionListItem stores session_id, message_count, and timestamp."""
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.session_list")
        SessionListItem = mod.SessionListItem
        item = SessionListItem(
            session_id="abc-123", message_count=5, timestamp="2026-01-01 12:00"
        )
        assert item.session_id == "abc-123"
        assert item.message_count == 5
        assert item.timestamp == "2026-01-01 12:00"


def test_session_selected_stores_session_id() -> None:
    """SessionSelected message stores the session_id."""
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        _clear_tui_modules()
        import importlib

        mod = importlib.import_module("prax.tui.widgets.session_list")
        SessionSelected = mod.SessionSelected
        msg = SessionSelected("my-session-id")
        assert msg.session_id == "my-session-id"
