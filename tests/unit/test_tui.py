"""Unit tests for the Prax TUI (prax/tui/__init__.py and prax/tui/app.py).

All Textual imports are mocked so no display is created.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ── Mock Textual before any prax.tui import ──────────────────────────────────


def _mock_textual():
    """Install fake textual modules into sys.modules."""
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

    class FakeListItem:
        def __init__(self, **kwargs):
            pass

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

    # ListView.Selected nested class
    FakeListView.Selected = FakeSelected

    # Also add rich mocks used by agent_status
    rich = types.ModuleType("rich")
    rich_table = types.ModuleType("rich.table")
    rich_panel = types.ModuleType("rich.panel")
    rich_text = types.ModuleType("rich.text")

    class FakeTable:
        @staticmethod
        def grid(**kwargs):
            t = FakeTable()
            return t

        def add_column(self, **kwargs):
            pass

        def add_row(self, *args):
            pass

    class FakePanel:
        def __init__(self, *args, **kwargs):
            pass

    class FakeText:
        def __init__(self, *args, **kwargs):
            pass

        def append(self, text, style=None):
            pass

    rich_table.Table = FakeTable
    rich_panel.Panel = FakePanel
    rich_text.Text = FakeText
    rich.table = rich_table
    rich.panel = rich_panel
    rich.text = rich_text

    mods = {
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
    return mods


# ── launch_tui ────────────────────────────────────────────────────────────────


def test_launch_tui_creates_and_runs_app() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        # Clear cached prax.tui modules
        for key in list(sys.modules):
            if "prax.tui" in key:
                del sys.modules[key]

        import importlib
        tui_mod = importlib.import_module("prax.tui")

        mock_bus = MagicMock()
        mock_app_instance = MagicMock()

        with patch("prax.tui.PraxTUI", return_value=mock_app_instance) as MockTUI:
            tui_mod.launch_tui(
                cwd="/some/cwd",
                event_bus=mock_bus,
                agent_name="test-agent",
                model_name="gpt-4",
            )
            MockTUI.assert_called_once_with(
                cwd="/some/cwd",
                event_bus=mock_bus,
                agent_name="test-agent",
                model_name="gpt-4",
            )
            mock_app_instance.run.assert_called_once()


def test_launch_tui_creates_eventbus_when_none_given() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        for key in list(sys.modules):
            if "prax.tui" in key:
                del sys.modules[key]

        import importlib
        tui_mod = importlib.import_module("prax.tui")

        mock_app_instance = MagicMock()
        with patch("prax.tui.PraxTUI", return_value=mock_app_instance):
            # Should not raise even though event_bus=None
            tui_mod.launch_tui(cwd="/cwd")
        mock_app_instance.run.assert_called_once()


# ── PraxTUI init ──────────────────────────────────────────────────────────────


def test_prax_tui_stores_constructor_args() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        for key in list(sys.modules):
            if "prax.tui" in key:
                del sys.modules[key]

        import importlib
        app_mod = importlib.import_module("prax.tui.app")
        PraxTUI = app_mod.PraxTUI

        mock_bus = MagicMock()
        app = PraxTUI(cwd="/workspace", event_bus=mock_bus, agent_name="myagent", model_name="mymodel")
        assert app.cwd == "/workspace"
        assert app.event_bus is mock_bus
        assert app.agent_name == "myagent"
        assert app.model_name == "mymodel"


def test_prax_tui_on_tool_start_increments_tool_calls() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        for key in list(sys.modules):
            if "prax.tui" in key:
                del sys.modules[key]

        import importlib
        app_mod = importlib.import_module("prax.tui.app")
        PraxTUI = app_mod.PraxTUI

        mock_bus = MagicMock()
        app = PraxTUI(cwd="/cwd", event_bus=mock_bus)
        mock_status = MagicMock()
        app.query_one = MagicMock(return_value=mock_status)

        from prax.core.stream_events import ToolStartEvent
        event = ToolStartEvent(tool_name="Bash", tool_id="t1")
        app._on_tool_start(event)
        mock_status.increment_tool_calls.assert_called_once()


def test_prax_tui_on_message_start_increments_iteration() -> None:
    mocks = _mock_textual()
    with patch.dict(sys.modules, mocks):
        for key in list(sys.modules):
            if "prax.tui" in key:
                del sys.modules[key]

        import importlib
        app_mod = importlib.import_module("prax.tui.app")
        PraxTUI = app_mod.PraxTUI

        mock_bus = MagicMock()
        app = PraxTUI(cwd="/cwd", event_bus=mock_bus)
        mock_status = MagicMock()
        app.query_one = MagicMock(return_value=mock_status)

        from prax.core.stream_events import MessageStartEvent
        event = MessageStartEvent(iteration=3)
        app._on_message_start(event)
        mock_status.increment_iteration.assert_called_once()
