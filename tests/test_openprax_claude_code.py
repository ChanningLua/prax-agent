from pathlib import Path
import json

from prax.cli import build_parser
from prax.integrations.claude_code.backups import list_claude_backups
from prax.integrations.claude_code.doctor import doctor_claude_install
from prax.integrations.claude_code.history import list_claude_history
from prax.integrations.claude_code.installer import install_claude_integration
from prax.integrations.claude_code.list_archives import list_claude_archives
from prax.integrations.claude_code.list_installed import list_installed_claude_assets
from prax.integrations.claude_code.package_bundle import export_claude_marketplace_bundle
from prax.integrations.claude_code.repair import repair_claude_integration
from prax.integrations.claude_code.restore import restore_claude_backup
from prax.integrations.claude_code.show_state import show_claude_state
from prax.integrations.claude_code.mcp import mcp_path
from prax.integrations.claude_code.settings import settings_path
from prax.integrations.claude_code.state import install_state_path, load_install_state
from prax.integrations.claude_code.uninstall import uninstall_claude_integration


def test_claude_install_lifecycle(tmp_path):
    target_root = tmp_path / ".claude"
    settings_path(target_root).parent.mkdir(parents=True, exist_ok=True)
    settings_path(target_root).write_text(json.dumps({"permissions": {"allow": ["Read"]}}, indent=2), encoding="utf-8")

    install = install_claude_integration(target_root=str(target_root), profile="developer")
    assert install["target"] == "claude"
    assert install["assets_installed"] > 0
    assert install["profile"] == "developer"
    assert install_state_path(target_root).exists()

    state = load_install_state(target_root)
    assert state is not None
    assert state.assets
    assert state.managed_settings
    assert state.backups
    settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    assert "permissions" in settings
    assert "hooks" in settings
    assert "enabledMcpjsonServers" in settings
    assert "Bash(prax *)" in settings["permissions"]["allow"]
    mcp = json.loads(mcp_path(target_root).read_text(encoding="utf-8"))
    assert "prax-memory" in mcp["mcpServers"]

    ok_report = doctor_claude_install(target_root=str(target_root))
    assert ok_report["status"] == "ok"
    assert ok_report["summary"]["error_count"] == 0
    assert ok_report["summary"]["warning_count"] == 0
    assert ok_report["layers"]["assets"]["status"] == "ok"
    assert ok_report["ownership"]["openprax-managed"] == len(state.assets)

    inventory = list_installed_claude_assets(target_root=str(target_root))
    assert inventory["installed"] is True
    assert inventory["asset_count"] == len(state.assets)
    assert inventory["profile"] == "developer"
    assert "prax-memory" in inventory["managed_mcp_servers"]
    assert inventory["layers"]["assets"]["count"] == len(state.assets)

    first_asset = state.assets[0]
    drifted_path = target_root / first_asset.destination_relative_path
    drifted_path.write_text("drifted", encoding="utf-8")

    drift_report = doctor_claude_install(target_root=str(target_root))
    assert drift_report["status"] == "warning"
    assert any(issue["code"] == "drifted_asset" for issue in drift_report["issues"])
    assert drift_report["layers"]["assets"]["status"] == "warning"

    settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    settings["permissions"]["allow"] = []
    settings["enabledMcpjsonServers"] = []
    settings_path(target_root).write_text(json.dumps(settings, indent=2), encoding="utf-8")
    mcp_path(target_root).write_text(json.dumps({"mcpServers": {}}, indent=2), encoding="utf-8")
    settings_report = doctor_claude_install(target_root=str(target_root))
    assert any(issue["code"] == "missing_settings_permission" for issue in settings_report["issues"])
    assert any(issue["code"] == "missing_enabled_mcp_server" for issue in settings_report["issues"])
    assert any(issue["code"] == "missing_mcp_server" for issue in settings_report["issues"])

    repair = repair_claude_integration(target_root=str(target_root))
    assert first_asset.destination_relative_path in repair["repaired_assets"]
    assert repair["profile"] == "developer"
    repaired_settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    assert "Bash(prax *)" in repaired_settings["permissions"]["allow"]
    assert "prax-memory" in repaired_settings["enabledMcpjsonServers"]
    repaired_mcp = json.loads(mcp_path(target_root).read_text(encoding="utf-8"))
    assert "prax-memory" in repaired_mcp["mcpServers"]

    repaired_report = doctor_claude_install(target_root=str(target_root))
    assert repaired_report["status"] == "ok"

    current_settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    current_settings["permissions"]["allow"] = ["Corrupted"]
    settings_path(target_root).write_text(json.dumps(current_settings, indent=2), encoding="utf-8")
    restore_result = restore_claude_backup(target_root=str(target_root), artifact="settings.json")
    assert restore_result["restored"] is True
    restored_settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    assert "Read" in restored_settings["permissions"]["allow"]

    uninstall = uninstall_claude_integration(target_root=str(target_root))
    assert uninstall["status"] == "ok"
    assert not install_state_path(target_root).exists()
    if settings_path(target_root).exists():
        removed_settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
        assert "hooks" not in removed_settings or not removed_settings["hooks"]


def test_openprax_cli_parser_supports_claude_lifecycle():
    parser = build_parser()

    args = parser.parse_args(["install", "--target", "claude", "--json", "--plugin-repo", "openprax/openprax", "--profile", "developer"])
    assert args.command == "install"
    assert args.target == "claude"
    assert args.json is True
    assert args.dry_run is False
    assert args.plugin_repo == "openprax/openprax"
    assert args.profile == "developer"

    dry_run_args = parser.parse_args(["list-installed", "--target", "claude", "--json"])
    assert dry_run_args.command == "list-installed"
    assert dry_run_args.target == "claude"

    history_args = parser.parse_args(["history", "--target", "claude", "--json"])
    assert history_args.command == "history"

    archives_args = parser.parse_args(["list-archives", "--target", "claude", "--json"])
    assert archives_args.command == "list-archives"

    backups_args = parser.parse_args(["list-backups", "--target", "claude", "--json"])
    assert backups_args.command == "list-backups"

    restore_args = parser.parse_args(["restore", "--target", "claude", "--artifact", "settings.json", "--dry-run"])
    assert restore_args.command == "restore"
    assert restore_args.artifact == "settings.json"
    assert restore_args.dry_run is True
    assert restore_args.backup_name is None
    assert restore_args.backup_path is None

    show_state_args = parser.parse_args(["show-state", "--target", "claude", "--json", "--archived-name", "foo.json"])
    assert show_state_args.command == "show-state"
    assert show_state_args.archived_name == "foo.json"

    export_args = parser.parse_args(["export-plugin", "--out-dir", "/tmp/openprax-plugin", "--profile", "minimal"])
    assert export_args.command == "export-plugin"
    assert export_args.out_dir == "/tmp/openprax-plugin"
    assert export_args.profile == "minimal"

    export_marketplace_args = parser.parse_args(["export-marketplace", "--out-dir", "/tmp/openprax-marketplace", "--profile", "developer"])
    assert export_marketplace_args.command == "export-marketplace"
    assert export_marketplace_args.out_dir == "/tmp/openprax-marketplace"
    assert export_marketplace_args.profile == "developer"

    prompt_args = parser.parse_args(["prompt", "--model", "claude-sonnet-4-6", "fix", "the", "bug"])
    assert prompt_args.command == "prompt"
    assert prompt_args.model == "claude-sonnet-4-6"
    assert prompt_args.task == ["fix", "the", "bug"]


def test_claude_install_dry_run_does_not_write_state(tmp_path):
    target_root = tmp_path / ".claude"
    result = install_claude_integration(target_root=str(target_root), dry_run=True, profile="minimal")
    assert result["dry_run"] is True
    assert result["profile"] == "minimal"
    assert result["install_state_path"] is None
    assert not install_state_path(target_root).exists()


def test_export_marketplace_bundle_writes_archive(tmp_path):
    result = export_claude_marketplace_bundle(out_dir=str(tmp_path), profile="developer")
    assert result["profile"] == "developer"
    assert Path(result["archive_path"]).exists()
    assert Path(result["manifest_path"]).exists()


def test_history_and_backups_are_reported(tmp_path):
    target_root = tmp_path / ".claude"
    settings_path(target_root).parent.mkdir(parents=True, exist_ok=True)
    settings_path(target_root).write_text(json.dumps({"permissions": {"allow": ["Read"]}}, indent=2), encoding="utf-8")
    install_claude_integration(target_root=str(target_root), profile="minimal")

    history = list_claude_history(target_root=str(target_root))
    backups = list_claude_backups(target_root=str(target_root))
    archives = list_claude_archives(target_root=str(target_root))
    state_view = show_claude_state(target_root=str(target_root))

    assert history["installed"] is True
    assert history["history"]
    assert backups["installed"] is True
    assert backups["available_versions"]["settings.json"] or backups["backups"] == {}
    assert archives["archives"]
    assert state_view["state"]["profile"] == "minimal"


def test_restore_supports_backup_path(tmp_path):
    target_root = tmp_path / ".claude"
    settings_path(target_root).parent.mkdir(parents=True, exist_ok=True)
    settings_path(target_root).write_text(json.dumps({"permissions": {"allow": ["Read"]}}, indent=2), encoding="utf-8")
    install_claude_integration(target_root=str(target_root), profile="minimal")

    backups = list_claude_backups(target_root=str(target_root))
    version = backups["available_versions"]["settings.json"][-1]

    current_settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    current_settings["permissions"]["allow"] = ["Broken"]
    settings_path(target_root).write_text(json.dumps(current_settings, indent=2), encoding="utf-8")

    restored = restore_claude_backup(
        target_root=str(target_root),
        artifact="settings.json",
        backup_path=str(version["path"]),
    )
    assert restored["restored"] is True
    restored_settings = json.loads(settings_path(target_root).read_text(encoding="utf-8"))
    assert "Read" in restored_settings["permissions"]["allow"]
