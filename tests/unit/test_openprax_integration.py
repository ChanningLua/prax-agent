"""Unit tests for prax integration layer.

Covers:
- integrations/claude_code/settings.py
- integrations/claude_code/plugin.py
- cli.py (non-I/O paths)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── settings.py ──────────────────────────────────────────────────────────────

from prax.integrations.claude_code.settings import (
    _append_unique,
    backup_settings,
    build_managed_settings,
    collect_settings_issues,
    load_settings,
    merge_settings,
    remove_managed_settings,
    save_settings,
    settings_path,
)


class TestSettingsPath:
    def test_returns_settings_json_under_root(self, tmp_path: Path) -> None:
        assert settings_path(tmp_path) == tmp_path / "settings.json"


class TestLoadSettings:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        result = load_settings(tmp_path)
        assert result == {}

    def test_existing_file_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text('{"key": "val"}', encoding="utf-8")
        assert load_settings(tmp_path) == {"key": "val"}


class TestSaveSettings:
    def test_creates_file_and_returns_path(self, tmp_path: Path) -> None:
        data = {"permissions": {"allow": ["Bash(foo *)"]}}
        path = save_settings(tmp_path, data)
        assert path.exists()
        assert json.loads(path.read_text()) == data


class TestBackupSettings:
    def test_returns_none_when_no_settings_file(self, tmp_path: Path) -> None:
        result = backup_settings(tmp_path)
        assert result is None

    def test_creates_backup_with_timestamp(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text('{"a": 1}', encoding="utf-8")
        backup = backup_settings(tmp_path)
        assert backup is not None
        assert backup.exists()
        assert backup.suffix == ".bak"
        assert "settings." in backup.name


class TestBuildManagedSettings:
    def test_contains_required_permissions(self, tmp_path: Path) -> None:
        result = build_managed_settings(tmp_path)
        assert "Bash(prax *)" in result["permissions"]["allow"]
        assert "Bash(python3 -m prax.cli *)" in result["permissions"]["allow"]

    def test_contains_mcp_servers(self, tmp_path: Path) -> None:
        result = build_managed_settings(tmp_path)
        assert "prax-memory" in result["enabledMcpjsonServers"]
        assert "prax-sequential-thinking" in result["enabledMcpjsonServers"]

    def test_contains_session_start_hook(self, tmp_path: Path) -> None:
        result = build_managed_settings(tmp_path)
        hooks = result["hooks"]
        assert "SessionStart" in hooks
        assert "Stop" in hooks


class TestAppendUnique:
    def test_no_duplicates_added(self) -> None:
        result = _append_unique(["a", "b"], ["b", "c"])
        assert result == ["a", "b", "c"]

    def test_empty_existing(self) -> None:
        result = _append_unique([], ["x", "y"])
        assert result == ["x", "y"]

    def test_dict_items_deduplicated(self) -> None:
        existing = [{"k": "v"}]
        required = [{"k": "v"}, {"k": "new"}]
        result = _append_unique(existing, required)
        assert len(result) == 2


class TestMergeSettings:
    def test_merges_permissions_allow(self) -> None:
        existing = {"permissions": {"allow": ["Bash(foo *)"]}}
        managed = {"permissions": {"allow": ["Bash(bar *)"]}}
        result = merge_settings(existing, managed)
        assert "Bash(foo *)" in result["permissions"]["allow"]
        assert "Bash(bar *)" in result["permissions"]["allow"]

    def test_merges_mcp_servers(self) -> None:
        existing: dict = {}
        managed = {"enabledMcpjsonServers": ["prax-memory"]}
        result = merge_settings(existing, managed)
        assert "prax-memory" in result["enabledMcpjsonServers"]

    def test_merges_marketplaces(self) -> None:
        existing: dict = {}
        managed = {"extraKnownMarketplaces": {"prax": {"source": {}}}}
        result = merge_settings(existing, managed)
        assert "prax" in result["extraKnownMarketplaces"]

    def test_merges_enabled_plugins(self) -> None:
        existing: dict = {}
        managed = {"enabledPlugins": {"prax@prax": True}}
        result = merge_settings(existing, managed)
        assert result["enabledPlugins"]["prax@prax"] is True

    def test_merges_hooks(self) -> None:
        hook_entry = {"type": "command", "command": "/path/to/hook"}
        existing: dict = {}
        managed = {"hooks": {"SessionStart": [hook_entry]}}
        result = merge_settings(existing, managed)
        assert hook_entry in result["hooks"]["SessionStart"]

    def test_existing_not_mutated(self) -> None:
        existing = {"permissions": {"allow": ["Bash(x *)"]}}
        managed = {"permissions": {"allow": ["Bash(y *)"]}}
        original_allow = list(existing["permissions"]["allow"])
        merge_settings(existing, managed)
        assert existing["permissions"]["allow"] == original_allow


class TestRemoveManagedSettings:
    def test_removes_permissions(self) -> None:
        existing = {"permissions": {"allow": ["Bash(prax *)", "Bash(other *)"]}}
        managed = {"permissions": {"allow": ["Bash(prax *)"]}}
        result = remove_managed_settings(existing, managed)
        assert "Bash(prax *)" not in result["permissions"]["allow"]
        assert "Bash(other *)" in result["permissions"]["allow"]

    def test_removes_mcp_servers(self) -> None:
        existing = {"enabledMcpjsonServers": ["prax-memory", "other"]}
        managed = {"enabledMcpjsonServers": ["prax-memory"]}
        result = remove_managed_settings(existing, managed)
        assert "prax-memory" not in result.get("enabledMcpjsonServers", [])

    def test_cleans_up_empty_permissions_key(self) -> None:
        existing = {"permissions": {"allow": ["Bash(prax *)"]}}
        managed = {"permissions": {"allow": ["Bash(prax *)"]}}
        result = remove_managed_settings(existing, managed)
        assert "permissions" not in result

    def test_removes_marketplaces(self) -> None:
        existing = {"extraKnownMarketplaces": {"prax": {}, "other": {}}}
        managed = {"extraKnownMarketplaces": {"prax": {}}}
        result = remove_managed_settings(existing, managed)
        assert "prax" not in result["extraKnownMarketplaces"]
        assert "other" in result["extraKnownMarketplaces"]

    def test_removes_hooks(self) -> None:
        hook = {"type": "command", "command": "/hook.sh"}
        existing = {"hooks": {"SessionStart": [hook, {"type": "command", "command": "/other.sh"}]}}
        managed = {"hooks": {"SessionStart": [hook]}}
        result = remove_managed_settings(existing, managed)
        remaining = result["hooks"]["SessionStart"]
        assert hook not in remaining


class TestCollectSettingsIssues:
    def test_detects_missing_permission(self) -> None:
        current: dict = {}
        managed = {"permissions": {"allow": ["Bash(prax *)"]}}
        issues = collect_settings_issues(current, managed)
        codes = [i["code"] for i in issues]
        assert "missing_settings_permission" in codes

    def test_detects_missing_mcp_server(self) -> None:
        current: dict = {}
        managed = {"enabledMcpjsonServers": ["prax-memory"]}
        issues = collect_settings_issues(current, managed)
        codes = [i["code"] for i in issues]
        assert "missing_enabled_mcp_server" in codes

    def test_detects_missing_hook(self) -> None:
        hook = {"hooks": [{"type": "command", "command": "/hook.sh"}]}
        current: dict = {}
        managed = {"hooks": {"SessionStart": [hook]}}
        issues = collect_settings_issues(current, managed)
        codes = [i["code"] for i in issues]
        assert "missing_settings_hook" in codes

    def test_no_issues_when_settings_match(self) -> None:
        managed = {"permissions": {"allow": ["Bash(prax *)"]}}
        current = {"permissions": {"allow": ["Bash(prax *)"]}}
        issues = collect_settings_issues(current, managed)
        assert issues == []


# ── plugin.py ────────────────────────────────────────────────────────────────

from prax.integrations.claude_code.plugin import (
    build_managed_plugin_settings,
    collect_plugin_issues,
)


class TestBuildManagedPluginSettings:
    def test_returns_empty_when_no_repo(self) -> None:
        assert build_managed_plugin_settings(plugin_repo=None) == {}
        assert build_managed_plugin_settings(plugin_repo="") == {}

    def test_returns_marketplace_and_plugin_entries(self) -> None:
        result = build_managed_plugin_settings(plugin_repo="gh:owner/repo")
        assert "prax" in result["extraKnownMarketplaces"]
        assert result["extraKnownMarketplaces"]["prax"]["source"]["repo"] == "gh:owner/repo"
        assert result["enabledPlugins"]["prax@prax"] is True


class TestCollectPluginIssues:
    def test_no_issues_when_managed_empty(self) -> None:
        issues = collect_plugin_issues({}, {})
        assert issues == []

    def test_detects_missing_marketplace(self) -> None:
        managed = {"extraKnownMarketplaces": {"prax": {}}, "enabledPlugins": {}}
        issues = collect_plugin_issues({}, managed)
        codes = [i["code"] for i in issues]
        assert "missing_plugin_marketplace" in codes

    def test_detects_missing_enabled_plugin(self) -> None:
        managed = {"extraKnownMarketplaces": {}, "enabledPlugins": {"prax@prax": True}}
        issues = collect_plugin_issues({}, managed)
        codes = [i["code"] for i in issues]
        assert "missing_enabled_plugin" in codes

    def test_no_issues_when_current_matches(self) -> None:
        managed = {
            "extraKnownMarketplaces": {"prax": {}},
            "enabledPlugins": {"prax@prax": True},
        }
        current = {
            "extraKnownMarketplaces": {"prax": {}},
            "enabledPlugins": {"prax@prax": True},
        }
        issues = collect_plugin_issues(current, managed)
        assert issues == []

    def test_handles_non_dict_marketplaces_in_current(self) -> None:
        managed = {"extraKnownMarketplaces": {"prax": {}}, "enabledPlugins": {}}
        current = {"extraKnownMarketplaces": "not-a-dict"}
        issues = collect_plugin_issues(current, managed)
        codes = [i["code"] for i in issues]
        assert "missing_plugin_marketplace" in codes


# ── cli.py – build_parser and _render ────────────────────────────────────────

from prax.cli import _render, build_parser, _ensure_claude_target


class TestBuildParser:
    def test_parser_has_prompt_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["prompt", "do", "something"])
        assert args.command == "prompt"
        assert args.task == ["do", "something"]

    def test_parser_has_status_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_parser_install_has_dry_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["install", "--dry-run"])
        assert args.dry_run is True

    def test_parser_export_plugin_requires_out_dir(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-plugin"])

    def test_parser_restore_requires_artifact(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["restore"])


class TestRender:
    def test_render_text_field_when_not_json(self, capsys: pytest.CaptureFixture) -> None:
        _render({"text": "hello"}, as_json=False)
        out = capsys.readouterr().out
        assert "hello" in out

    def test_render_json_dumps_when_as_json(self, capsys: pytest.CaptureFixture) -> None:
        _render({"key": "value"}, as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["key"] == "value"

    def test_render_falls_back_to_json_when_no_text(self, capsys: pytest.CaptureFixture) -> None:
        _render({"data": 42}, as_json=False)
        out = capsys.readouterr().out
        assert "42" in out


class TestEnsureClaudeTarget:
    def test_accepts_claude(self) -> None:
        _ensure_claude_target("claude")  # no exception

    def test_rejects_other_targets(self) -> None:
        with pytest.raises(ValueError, match="Only --target claude"):
            _ensure_claude_target("docker")
