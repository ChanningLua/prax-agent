"""Tests for the declarative hook system (pure unit tests, no real I/O)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from prax.tools.base import ToolCall, ToolResult
from prax.core.hooks import (
    HookMatcher,
    Hook,
    HookRegistry,
    get_hook_registry,
    load_hooks_from_directory,
)
import prax.core.hooks as hooks_module


# ── HookMatcher.matches ───────────────────────────────────

def _tc(name="Read", input=None):
    return ToolCall(name=name, input=input or {})


def test_matcher_no_constraints_matches_anything():
    m = HookMatcher()
    assert m.matches(_tc("Read"), None, "PreTool") is True


def test_matcher_tool_name_exact_match():
    m = HookMatcher(tool_name="Read")
    assert m.matches(_tc("Read"), None, "PreTool") is True


def test_matcher_tool_name_no_match():
    m = HookMatcher(tool_name="Write")
    assert m.matches(_tc("Read"), None, "PreTool") is False


def test_matcher_tool_pattern_regex_match():
    m = HookMatcher(tool_pattern=r"Read|Write")
    assert m.matches(_tc("Read"), None, "PreTool") is True
    assert m.matches(_tc("Write"), None, "PreTool") is True


def test_matcher_tool_pattern_regex_no_match():
    m = HookMatcher(tool_pattern=r"Bash")
    assert m.matches(_tc("Read"), None, "PreTool") is False


def test_matcher_event_type_match():
    m = HookMatcher(event_type="PreTool")
    assert m.matches(_tc("Read"), None, "PreTool") is True


def test_matcher_event_type_no_match():
    m = HookMatcher(event_type="PostTool")
    assert m.matches(_tc("Read"), None, "PreTool") is False


def test_matcher_file_pattern_match():
    m = HookMatcher(file_pattern="*.py")
    tc = _tc("Write", {"file_path": "/src/foo.py"})
    assert m.matches(tc, None, "PreTool") is True


def test_matcher_file_pattern_no_match():
    m = HookMatcher(file_pattern="*.py")
    tc = _tc("Write", {"file_path": "/src/foo.ts"})
    assert m.matches(tc, None, "PreTool") is False


def test_matcher_file_pattern_no_path_in_input():
    m = HookMatcher(file_pattern="*.py")
    tc = _tc("Bash", {"command": "ls"})
    assert m.matches(tc, None, "PreTool") is False


def test_matcher_combined_tool_name_and_event():
    m = HookMatcher(tool_name="Write", event_type="PreTool")
    assert m.matches(_tc("Write"), None, "PreTool") is True
    assert m.matches(_tc("Write"), None, "PostTool") is False
    assert m.matches(_tc("Read"), None, "PreTool") is False


# ── HookMatcher._extract_file_path ───────────────────────

def test_extract_file_path_file_path_key():
    m = HookMatcher()
    tc = _tc("Write", {"file_path": "/foo/bar.py"})
    assert m._extract_file_path(tc) == "/foo/bar.py"


def test_extract_file_path_path_key():
    m = HookMatcher()
    tc = _tc("Read", {"path": "/foo/bar.py"})
    assert m._extract_file_path(tc) == "/foo/bar.py"


def test_extract_file_path_filepath_key():
    m = HookMatcher()
    tc = _tc("Read", {"filepath": "/foo/bar.py"})
    assert m._extract_file_path(tc) == "/foo/bar.py"


def test_extract_file_path_none_when_missing():
    m = HookMatcher()
    tc = _tc("Bash", {"command": "ls"})
    assert m._extract_file_path(tc) is None


def test_extract_file_path_skips_non_string():
    m = HookMatcher()
    tc = _tc("Write", {"file_path": 123})
    assert m._extract_file_path(tc) is None


# ── HookMatcher.matches_lifecycle ────────────────────────

def test_matches_lifecycle_correct_event():
    m = HookMatcher(event_type="PreModel")
    assert m.matches_lifecycle("PreModel", {}) is True


def test_matches_lifecycle_wrong_event():
    m = HookMatcher(event_type="PreModel")
    assert m.matches_lifecycle("PostModel", {}) is False


def test_matches_lifecycle_none_event_type():
    m = HookMatcher(event_type=None)
    assert m.matches_lifecycle("OnComplete", {}) is False


# ── HookRegistry.register/unregister ─────────────────────

def test_registry_register_hook():
    reg = HookRegistry()
    hook = Hook(name="test", matcher=HookMatcher(), handler=AsyncMock())
    reg.register(hook)
    assert len(reg._hooks) == 1


def test_registry_unregister_hook():
    reg = HookRegistry()
    hook = Hook(name="test", matcher=HookMatcher(), handler=AsyncMock())
    reg.register(hook)
    reg.unregister("test")
    assert len(reg._hooks) == 0


def test_registry_unregister_nonexistent_is_noop():
    reg = HookRegistry()
    reg.unregister("nonexistent")  # Should not raise
    assert len(reg._hooks) == 0


def test_registry_priority_ordering():
    reg = HookRegistry()
    low = Hook(name="low", matcher=HookMatcher(), handler=AsyncMock(), priority=1)
    high = Hook(name="high", matcher=HookMatcher(), handler=AsyncMock(), priority=10)
    reg.register(low)
    reg.register(high)
    assert reg._hooks[0].name == "high"


# ── HookRegistry.get_matching_hooks ──────────────────────

def test_get_matching_hooks_returns_enabled():
    reg = HookRegistry()
    hook = Hook(name="h1", matcher=HookMatcher(tool_name="Read"), handler=AsyncMock())
    reg.register(hook)
    tc = _tc("Read")
    result = reg.get_matching_hooks(tc, None, "PreTool")
    assert len(result) == 1


def test_get_matching_hooks_skips_disabled():
    reg = HookRegistry()
    hook = Hook(name="h1", matcher=HookMatcher(), handler=AsyncMock(), enabled=False)
    reg.register(hook)
    result = reg.get_matching_hooks(_tc("Read"), None, "PreTool")
    assert len(result) == 0


def test_get_matching_hooks_filters_by_name():
    reg = HookRegistry()
    reg.register(Hook(name="h_read", matcher=HookMatcher(tool_name="Read"), handler=AsyncMock()))
    reg.register(Hook(name="h_write", matcher=HookMatcher(tool_name="Write"), handler=AsyncMock()))
    result = reg.get_matching_hooks(_tc("Read"), None, "PreTool")
    assert len(result) == 1
    assert result[0].name == "h_read"


# ── HookRegistry.execute_hooks ───────────────────────────

@pytest.mark.asyncio
async def test_execute_hooks_returns_none_when_no_match():
    reg = HookRegistry()
    result = await reg.execute_hooks(_tc("Bash"), None, "PreTool")
    assert result is None


@pytest.mark.asyncio
async def test_execute_hooks_short_circuits_on_result():
    reg = HookRegistry()
    expected = ToolResult(content="blocked", is_error=True)
    handler = AsyncMock(return_value=expected)
    second = AsyncMock(return_value=None)
    reg.register(Hook(name="h1", matcher=HookMatcher(), handler=handler, priority=10))
    reg.register(Hook(name="h2", matcher=HookMatcher(), handler=second, priority=5))
    result = await reg.execute_hooks(_tc("Read"), None, "PreTool")
    assert result == expected
    second.assert_not_called()


@pytest.mark.asyncio
async def test_execute_hooks_continues_on_error():
    reg = HookRegistry()
    failing = AsyncMock(side_effect=RuntimeError("boom"))
    ok = AsyncMock(return_value=None)
    reg.register(Hook(name="fail", matcher=HookMatcher(), handler=failing, priority=10))
    reg.register(Hook(name="ok", matcher=HookMatcher(), handler=ok, priority=5))
    result = await reg.execute_hooks(_tc("Read"), None, "PreTool")
    assert result is None
    ok.assert_called_once()


@pytest.mark.asyncio
async def test_execute_hooks_pass_through_returns_none():
    reg = HookRegistry()
    handler = AsyncMock(return_value=None)
    reg.register(Hook(name="h1", matcher=HookMatcher(), handler=handler))
    result = await reg.execute_hooks(_tc("Read"), None, "PreTool")
    assert result is None


# ── HookRegistry.execute_lifecycle_hooks ─────────────────

@pytest.mark.asyncio
async def test_execute_lifecycle_hooks_calls_lifecycle_handler():
    reg = HookRegistry()
    lifecycle_handler = AsyncMock()
    hook = Hook(
        name="lc",
        matcher=HookMatcher(event_type="OnComplete"),
        handler=AsyncMock(),
        lifecycle_handler=lifecycle_handler,
    )
    reg.register(hook)
    ctx = {"status": "done"}
    await reg.execute_lifecycle_hooks("OnComplete", ctx)
    lifecycle_handler.assert_called_once_with(ctx)


@pytest.mark.asyncio
async def test_execute_lifecycle_hooks_skips_wrong_event():
    reg = HookRegistry()
    lifecycle_handler = AsyncMock()
    hook = Hook(
        name="lc",
        matcher=HookMatcher(event_type="OnError"),
        handler=AsyncMock(),
        lifecycle_handler=lifecycle_handler,
    )
    reg.register(hook)
    await reg.execute_lifecycle_hooks("OnComplete", {})
    lifecycle_handler.assert_not_called()


@pytest.mark.asyncio
async def test_execute_lifecycle_hooks_skips_disabled():
    reg = HookRegistry()
    lifecycle_handler = AsyncMock()
    hook = Hook(
        name="lc",
        matcher=HookMatcher(event_type="OnComplete"),
        handler=AsyncMock(),
        lifecycle_handler=lifecycle_handler,
        enabled=False,
    )
    reg.register(hook)
    await reg.execute_lifecycle_hooks("OnComplete", {})
    lifecycle_handler.assert_not_called()


# ── load_from_file ────────────────────────────────────────

def test_load_from_file_nonexistent(tmp_path):
    reg = HookRegistry()
    reg.load_from_file(tmp_path / "missing.json")
    assert len(reg._hooks) == 0


def test_load_from_file_loads_hooks(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({
        "hooks": [
            {
                "name": "fmt",
                "matcher": {"tool_name": "Write"},
                "command": "echo hello",
            }
        ]
    }))
    reg = HookRegistry()
    reg.load_from_file(cfg)
    assert len(reg._hooks) == 1
    assert reg._hooks[0].name == "fmt"


def test_load_from_file_mtime_cache(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"name": "h1", "matcher": {}, "command": "echo 1"}
    ]}))
    reg = HookRegistry()
    reg.load_from_file(cfg)
    count_before = len(reg._hooks)
    reg.load_from_file(cfg)  # Second call should be a no-op
    assert len(reg._hooks) == count_before


def test_load_from_file_no_command_skips(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"name": "h1", "matcher": {}}  # No command
    ]}))
    reg = HookRegistry()
    reg.load_from_file(cfg)
    assert len(reg._hooks) == 0


def test_load_from_file_hook_disabled(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": [
        {"name": "h1", "matcher": {}, "command": "echo 1", "enabled": False}
    ]}))
    reg = HookRegistry()
    reg.load_from_file(cfg)
    assert len(reg._hooks) == 1
    assert reg._hooks[0].enabled is False


# ── load_from_claude_settings ─────────────────────────────

def test_load_from_claude_settings_missing(tmp_path):
    reg = HookRegistry()
    reg.load_from_claude_settings(str(tmp_path))
    assert len(reg._hooks) == 0


def test_load_from_claude_settings_pretoolose(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": "echo pre"}]
                }
            ]
        }
    }))
    reg = HookRegistry()
    reg.load_from_claude_settings(str(tmp_path))
    assert len(reg._hooks) == 1
    assert reg._hooks[0].matcher.event_type == "PreToolUse"
    assert reg._hooks[0].matcher.tool_pattern == "Edit|Write"


def test_load_from_claude_settings_lifecycle(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "echo start"}]}
            ]
        }
    }))
    reg = HookRegistry()
    reg.load_from_claude_settings(str(tmp_path))
    assert len(reg._hooks) == 1
    assert reg._hooks[0].matcher.event_type == "SessionStart"


def test_load_from_claude_settings_skips_non_command(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(json.dumps({
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "script", "command": "run.py"}]}
            ]
        }
    }))
    reg = HookRegistry()
    reg.load_from_claude_settings(str(tmp_path))
    assert len(reg._hooks) == 0


def test_load_from_claude_settings_timeout_conversion(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(json.dumps({
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "echo x", "timeout": 10}]}
            ]
        }
    }))
    reg = HookRegistry()
    reg.load_from_claude_settings(str(tmp_path))
    # timeout 10 seconds → 10000 ms
    assert reg._hooks[0]._timeout == 10000


# ── get_hook_registry singleton ───────────────────────────

def test_get_hook_registry_returns_singleton(monkeypatch):
    monkeypatch.setattr(hooks_module, "_global_registry", None)
    r1 = get_hook_registry()
    r2 = get_hook_registry()
    assert r1 is r2


def test_get_hook_registry_returns_hook_registry_instance(monkeypatch):
    monkeypatch.setattr(hooks_module, "_global_registry", None)
    r = get_hook_registry()
    assert isinstance(r, HookRegistry)


def test_get_hook_registry_reuses_existing(monkeypatch):
    existing = HookRegistry()
    monkeypatch.setattr(hooks_module, "_global_registry", existing)
    r = get_hook_registry()
    assert r is existing


# ── load_hooks_from_directory ─────────────────────────────

def test_load_hooks_from_directory_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks_module, "_global_registry", None)
    load_hooks_from_directory(tmp_path / "nonexistent")
    # Should not raise; registry has no hooks
    reg = get_hook_registry()
    assert len(reg._hooks) == 0


def test_load_hooks_from_directory_loads_json_files(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks_module, "_global_registry", None)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "a.json").write_text(json.dumps({"hooks": [
        {"name": "h_a", "matcher": {}, "command": "echo a"}
    ]}))
    (hooks_dir / "b.json").write_text(json.dumps({"hooks": [
        {"name": "h_b", "matcher": {}, "command": "echo b"}
    ]}))
    load_hooks_from_directory(hooks_dir)
    reg = get_hook_registry()
    names = {h.name for h in reg._hooks}
    assert "h_a" in names
    assert "h_b" in names


def test_load_hooks_from_directory_ignores_non_json(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks_module, "_global_registry", None)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "not_hooks.yaml").write_text("hooks: []")
    load_hooks_from_directory(hooks_dir)
    reg = get_hook_registry()
    assert len(reg._hooks) == 0


# ── _create_command_handler ───────────────────────────────

@pytest.mark.asyncio
async def test_command_handler_success():
    reg = HookRegistry()
    handler = reg._create_command_handler("true", timeout=5000)
    tc = _tc("Read")
    result = await handler(tc, None)
    assert result is None  # Success → pass through


@pytest.mark.asyncio
async def test_command_handler_failure_returns_error():
    reg = HookRegistry()
    handler = reg._create_command_handler("exit 1", timeout=5000)
    tc = _tc("Read")
    result = await handler(tc, None)
    assert result is not None
    assert result.is_error is True
