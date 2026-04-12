"""Unit tests for prax/cli.py.

All tests are pure unit tests with no real I/O, no subprocess calls,
and no LLM API calls. External integrations are fully mocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from prax.cli import (
    _render,
    _ensure_claude_target,
    build_parser,
    _status_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_command_result(text="ok", data=None):
    result = MagicMock()
    result.text = text
    result.data = data or {}
    return result


# ---------------------------------------------------------------------------
# TestBuildParser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_parser_created_with_prax_prog(self):
        parser = build_parser()
        assert parser.prog == "prax"

    def test_prompt_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["prompt", "do", "something"])
        assert args.command == "prompt"
        assert args.task == ["do", "something"]

    def test_prompt_subcommand_with_model_flag(self):
        parser = build_parser()
        args = parser.parse_args(["prompt", "--model", "gpt-4", "task text"])
        assert args.model == "gpt-4"
        assert args.task == ["task text"]

    def test_prompt_subcommand_with_session_id(self):
        parser = build_parser()
        args = parser.parse_args(["prompt", "--session-id", "s123", "task"])
        assert args.session_id == "s123"

    def test_status_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"
        assert args.json is False

    def test_status_with_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["status", "--json"])
        assert args.json is True

    def test_providers_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["providers"])
        assert args.command == "providers"

    def test_install_subcommand_with_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["install", "--dry-run"])
        assert args.command == "install"
        assert args.dry_run is True

    def test_install_subcommand_with_profile(self):
        parser = build_parser()
        args = parser.parse_args(["install", "--profile", "minimal"])
        assert args.profile == "minimal"

    def test_doctor_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"

    def test_list_installed_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["list-installed"])
        assert args.command == "list-installed"

    def test_show_state_subcommand_with_archived_name(self):
        parser = build_parser()
        args = parser.parse_args(["show-state", "--archived-name", "backup-1"])
        assert args.archived_name == "backup-1"

    def test_repair_subcommand_with_plugin_repo(self):
        parser = build_parser()
        args = parser.parse_args(["repair", "--plugin-repo", "https://example.com"])
        assert args.plugin_repo == "https://example.com"

    def test_restore_requires_artifact(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["restore"])  # --artifact is required

    def test_restore_with_artifact(self):
        parser = build_parser()
        args = parser.parse_args(["restore", "--artifact", "mcp"])
        assert args.artifact == "mcp"

    def test_export_plugin_requires_out_dir(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-plugin"])

    def test_export_plugin_with_out_dir(self):
        parser = build_parser()
        args = parser.parse_args(["export-plugin", "--out-dir", "/tmp/out"])
        assert args.out_dir == "/tmp/out"

    def test_uninstall_subcommand_with_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["uninstall", "--dry-run"])
        assert args.dry_run is True

    def test_debug_claude_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["debug-claude", "run tests"])
        assert args.command == "debug-claude"
        assert args.task == ["run tests"]

    def test_missing_command_exits(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_history_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["history"])
        assert args.command == "history"

    def test_list_archives_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["list-archives"])
        assert args.command == "list-archives"

    def test_list_backups_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["list-backups"])
        assert args.command == "list-backups"


# ---------------------------------------------------------------------------
# TestRender
# ---------------------------------------------------------------------------


class TestRender:
    def test_render_as_json(self, capsys):
        _render({"status": "ok", "text": "all good"}, as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["status"] == "ok"

    def test_render_text_mode_uses_text_field(self, capsys):
        _render({"text": "human readable output", "extra": "data"}, as_json=False)
        out = capsys.readouterr().out
        assert "human readable output" in out

    def test_render_text_mode_falls_back_to_json_when_no_text(self, capsys):
        _render({"status": "ok"}, as_json=False)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["status"] == "ok"


# ---------------------------------------------------------------------------
# TestEnsureClaudeTarget
# ---------------------------------------------------------------------------


class TestEnsureClaudeTarget:
    def test_claude_target_is_valid(self):
        _ensure_claude_target("claude")  # should not raise

    def test_non_claude_target_raises(self):
        with pytest.raises(ValueError, match="Only --target claude"):
            _ensure_claude_target("vscode")

    def test_empty_target_raises(self):
        with pytest.raises(ValueError):
            _ensure_claude_target("")


# ---------------------------------------------------------------------------
# TestStatusPayload
# ---------------------------------------------------------------------------


class TestStatusPayload:
    def test_status_payload_contains_required_keys(self, tmp_path):
        fake_status = _fake_command_result(data={"uptime": 100})
        fake_providers = _fake_command_result(data={"providers": ["openai"]})
        fake_claude_result = {"status": "ok"}
        fake_inventory = {"assets": []}

        with (
            patch("prax.cli._command_context") as mock_ctx,
            patch("prax.cli.run_command") as mock_run_command,
            patch("prax.cli.doctor_claude_install", return_value=fake_claude_result),
            patch("prax.cli.list_installed_claude_assets", return_value=fake_inventory),
        ):
            mock_ctx.return_value = MagicMock()
            mock_run_command.side_effect = [fake_status, fake_providers]

            payload = _status_payload(str(tmp_path))

        assert "runtime" in payload
        assert "providers" in payload
        assert "claude_integration" in payload
        assert "claude_inventory" in payload
        assert "text" in payload

    def test_status_payload_text_includes_runtime_path(self, tmp_path):
        fake_status = _fake_command_result(data={})
        fake_providers = _fake_command_result(data={"providers": []})

        with (
            patch("prax.cli._command_context"),
            patch("prax.cli.run_command", side_effect=[fake_status, fake_providers]),
            patch("prax.cli.doctor_claude_install", return_value={"status": "healthy"}),
            patch("prax.cli.list_installed_claude_assets", return_value={}),
        ):
            payload = _status_payload(str(tmp_path))

        assert "runtime_path" in payload["text"]
        assert "integration_mode" in payload["text"]

    def test_status_payload_runtime_has_defaults(self, tmp_path):
        """When status result data is empty, defaults are set."""
        fake_status = _fake_command_result(data={})
        fake_providers = _fake_command_result(data={})

        with (
            patch("prax.cli._command_context"),
            patch("prax.cli.run_command", side_effect=[fake_status, fake_providers]),
            patch("prax.cli.doctor_claude_install", return_value={}),
            patch("prax.cli.list_installed_claude_assets", return_value={}),
        ):
            payload = _status_payload(str(tmp_path))

        assert payload["runtime"]["integration_mode"] == "native"
        assert payload["runtime"]["executor"] == "direct-api"


# ---------------------------------------------------------------------------
# TestMain
# ---------------------------------------------------------------------------


class TestMain:
    def _run_main_with_args(self, args: list[str], monkeypatch):
        """Helper: monkeypatch sys.argv and call main()."""
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax"] + args)
        return main

    def test_prompt_command_calls_native_runtime(self, monkeypatch, tmp_path):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "prompt", "run tests"])
        monkeypatch.chdir(tmp_path)

        with patch("prax.cli.NativeRuntime") as MockRuntime:
            mock_rt = MagicMock()
            MockRuntime.return_value = mock_rt
            main()

        mock_rt.run_task_sync.assert_called_once()
        call_kwargs = mock_rt.run_task_sync.call_args
        task_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("task", "")
        assert "run tests" in task_arg

    def test_prompt_command_with_model_and_session(self, monkeypatch, tmp_path):
        from prax.cli import main
        monkeypatch.setattr(
            sys, "argv",
            ["prax", "prompt", "--model", "m1", "--session-id", "s1", "task"],
        )
        monkeypatch.chdir(tmp_path)

        with patch("prax.cli.NativeRuntime") as MockRuntime:
            mock_rt = MagicMock()
            MockRuntime.return_value = mock_rt
            main()

        mock_rt.run_task_sync.assert_called_once()
        kwargs = mock_rt.run_task_sync.call_args[1]
        assert kwargs.get("model_override") == "m1"
        assert kwargs.get("session_id") == "s1"

    def test_status_command_renders_output(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "status"])
        monkeypatch.chdir(tmp_path)

        fake_payload = {
            "runtime": {"runtime_path": "native"},
            "providers": {},
            "claude_integration": {"status": "ok"},
            "claude_inventory": {},
            "text": "runtime_path=native integration_mode=native claude_status=ok",
        }
        with patch("prax.cli._status_payload", return_value=fake_payload):
            main()

        out = capsys.readouterr().out
        assert "runtime_path=native" in out

    def test_status_command_json_flag(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "status", "--json"])
        monkeypatch.chdir(tmp_path)

        fake_payload = {
            "runtime": {},
            "providers": {},
            "claude_integration": {},
            "claude_inventory": {},
            "text": "ok",
        }
        with patch("prax.cli._status_payload", return_value=fake_payload):
            main()

        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert "runtime" in parsed

    def test_providers_command_renders_text(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "providers"])
        monkeypatch.chdir(tmp_path)

        fake_result = _fake_command_result(
            text="providers: openai", data={"providers": ["openai"]}
        )
        with (
            patch("prax.cli._command_context"),
            patch("prax.cli.run_command", return_value=fake_result),
        ):
            main()

        out = capsys.readouterr().out
        assert "openai" in out

    def test_install_command_calls_install_function(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "install"])
        monkeypatch.chdir(tmp_path)

        with patch(
            "prax.cli.install_claude_integration",
            return_value={"text": "installed", "status": "ok"},
        ) as mock_install:
            main()

        mock_install.assert_called_once()

    def test_doctor_command_calls_doctor_function(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "doctor"])
        monkeypatch.chdir(tmp_path)

        with patch(
            "prax.cli.doctor_claude_install",
            return_value={"text": "all ok", "status": "healthy"},
        ) as mock_doctor:
            main()

        mock_doctor.assert_called_once()

    def test_uninstall_command_dry_run(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "uninstall", "--dry-run"])
        monkeypatch.chdir(tmp_path)

        with patch(
            "prax.cli.uninstall_claude_integration",
            return_value={"text": "dry run ok"},
        ) as mock_uninstall:
            main()

        mock_uninstall.assert_called_once()
        call_kwargs = mock_uninstall.call_args[1]
        assert call_kwargs.get("dry_run") is True

    def test_export_plugin_command(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(
            sys, "argv",
            ["prax", "export-plugin", "--out-dir", str(tmp_path)],
        )
        monkeypatch.chdir(tmp_path)

        with patch(
            "prax.cli.export_claude_plugin_bundle",
            return_value={"text": "exported"},
        ) as mock_export:
            main()

        mock_export.assert_called_once_with(out_dir=str(tmp_path), profile="full")

    def test_debug_claude_command_calls_async_run(self, monkeypatch, tmp_path):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "debug-claude", "run task"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main._run", new_callable=AsyncMock) as mock_run,
            patch("prax.cli.PermissionMode"),
        ):
            main()

        mock_run.assert_awaited_once()

    def test_list_installed_command(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "list-installed"])
        monkeypatch.chdir(tmp_path)

        with patch(
            "prax.cli.list_installed_claude_assets",
            return_value={"text": "asset list", "assets": []},
        ) as mock_list:
            main()

        mock_list.assert_called_once()

    def test_repair_command(self, monkeypatch, tmp_path, capsys):
        from prax.cli import main
        monkeypatch.setattr(sys, "argv", ["prax", "repair"])
        monkeypatch.chdir(tmp_path)

        with patch(
            "prax.cli.repair_claude_integration",
            return_value={"text": "repaired"},
        ) as mock_repair:
            main()

        mock_repair.assert_called_once()
