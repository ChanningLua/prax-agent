"""Tests for prax.core.hooks."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from prax.core.hooks import Hook, HookMatcher, HookRegistry
from prax.tools.base import ToolCall, ToolResult


def _tc(name: str = "Bash", input: dict | None = None) -> ToolCall:
    return ToolCall(id="tc-1", name=name, input=input or {})


class TestHookMatcher:
    def test_matches_exact_tool_name(self):
        m = HookMatcher(tool_name="Bash")
        assert m.matches(_tc("Bash"), None, "PreTool") is True
        assert m.matches(_tc("Write"), None, "PreTool") is False

    def test_matches_tool_pattern_regex(self):
        m = HookMatcher(tool_pattern="Bash|Write")
        assert m.matches(_tc("Bash"), None, "PreTool") is True
        assert m.matches(_tc("Write"), None, "PreTool") is True
        assert m.matches(_tc("Read"), None, "PreTool") is False

    def test_matches_event_type_filter(self):
        m = HookMatcher(event_type="PreTool")
        assert m.matches(_tc(), None, "PreTool") is True
        assert m.matches(_tc(), None, "PostTool") is False

    def test_matches_combined_tool_and_event(self):
        m = HookMatcher(tool_name="Bash", event_type="PreTool")
        assert m.matches(_tc("Bash"), None, "PreTool") is True
        assert m.matches(_tc("Bash"), None, "PostTool") is False
        assert m.matches(_tc("Write"), None, "PreTool") is False

    def test_matches_no_filters_matches_all(self):
        m = HookMatcher()
        assert m.matches(_tc("Bash"), None, "PreTool") is True
        assert m.matches(_tc("Write"), None, "PostTool") is True


class TestHookMatcherLifecycle:
    def test_matches_lifecycle_event(self):
        m = HookMatcher(event_type="OnComplete")
        assert m.matches_lifecycle("OnComplete", {}) is True
        assert m.matches_lifecycle("OnError", {}) is False

    def test_non_matching_lifecycle(self):
        m = HookMatcher(event_type="PreModel")
        assert m.matches_lifecycle("PostModel", {}) is False


class TestHookRegistry:
    def test_register_and_priority_sort(self):
        registry = HookRegistry()
        handler = AsyncMock(return_value=None)

        h1 = Hook(name="low", matcher=HookMatcher(), handler=handler, priority=1)
        h2 = Hook(name="high", matcher=HookMatcher(), handler=handler, priority=10)
        h3 = Hook(name="mid", matcher=HookMatcher(), handler=handler, priority=5)

        registry.register(h1)
        registry.register(h2)
        registry.register(h3)

        names = [h.name for h in registry._hooks]
        assert names == ["high", "mid", "low"]

    def test_get_matching_hooks_returns_subset(self):
        registry = HookRegistry()
        handler = AsyncMock(return_value=None)

        h_bash = Hook(name="bash-hook", matcher=HookMatcher(tool_name="Bash"), handler=handler)
        h_write = Hook(name="write-hook", matcher=HookMatcher(tool_name="Write"), handler=handler)

        registry.register(h_bash)
        registry.register(h_write)

        matches = registry.get_matching_hooks(_tc("Bash"), None, "PreTool")
        assert len(matches) == 1
        assert matches[0].name == "bash-hook"

    def test_disabled_hooks_excluded(self):
        registry = HookRegistry()
        handler = AsyncMock(return_value=None)

        h = Hook(name="disabled", matcher=HookMatcher(), handler=handler, enabled=False)
        registry.register(h)

        matches = registry.get_matching_hooks(_tc(), None, "PreTool")
        assert len(matches) == 0

    def test_unregister(self):
        registry = HookRegistry()
        handler = AsyncMock(return_value=None)

        h = Hook(name="to-remove", matcher=HookMatcher(), handler=handler)
        registry.register(h)
        assert len(registry._hooks) == 1

        registry.unregister("to-remove")
        assert len(registry._hooks) == 0
