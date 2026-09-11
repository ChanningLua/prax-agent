"""Codex personal-plugin install, diagnosis, inventory, and removal."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from prax.integrations.codex import (
    doctor_codex_install,
    install_codex_integration,
    list_installed_codex_assets,
    uninstall_codex_integration,
)


def _runner(command, **kwargs):
    if command[1:3] == ["plugin", "list"]:
        stdout = json.dumps(
            {
                "installed": [
                    {
                        "pluginId": "prax@personal",
                        "name": "prax",
                        "installed": True,
                        "enabled": True,
                    }
                ]
            }
        )
    else:
        stdout = json.dumps({"ok": True})
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_install_registers_personal_marketplace_and_activates(tmp_path):
    home = tmp_path / "home"
    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "personal",
                "plugins": [
                    {
                        "name": "keep-me",
                        "source": {"source": "local", "path": "./plugins/keep-me"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def recording_runner(command, **kwargs):
        calls.append(command)
        return _runner(command, **kwargs)

    result = install_codex_integration(
        home=str(home),
        codex_bin="/bin/codex",
        runner=recording_runner,
        now=datetime(2026, 7, 28, 12, 34, 56, tzinfo=timezone.utc),
    )

    plugin_root = home / "plugins" / "prax"
    manifest = json.loads(
        (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    market = json.loads(marketplace.read_text(encoding="utf-8"))
    assert result["version"] == "1.0.0+codex.local-20260728-123456"
    assert manifest["version"] == result["version"]
    assert (plugin_root / "skills" / "prax-shift" / "SKILL.md").exists()
    assert [item["name"] for item in market["plugins"]] == ["keep-me", "prax"]
    assert market["plugins"][1]["source"]["path"] == "./plugins/prax"
    assert calls == [
        ["/bin/codex", "plugin", "add", "prax@personal", "--json"]
    ]


def test_doctor_and_inventory_report_installed_assets(tmp_path):
    home = tmp_path / "home"
    install_codex_integration(
        home=str(home),
        codex_bin="/bin/codex",
        runner=_runner,
    )

    report = doctor_codex_install(
        home=str(home),
        codex_bin="/bin/codex",
        runner=_runner,
    )
    inventory = list_installed_codex_assets(home=str(home))

    assert report["status"] == "ok"
    assert all(report["checks"].values())
    assert ".codex-plugin/plugin.json" in inventory["assets"]
    assert "skills/prax-shift/SKILL.md" in inventory["assets"]


def test_install_removes_legacy_7x24_skill(tmp_path):
    home = tmp_path / "home"
    legacy = home / "plugins" / "prax" / "skills" / "prax-7x24"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("legacy", encoding="utf-8")

    install_codex_integration(
        home=str(home),
        codex_bin="/bin/codex",
        runner=_runner,
    )

    assert not legacy.exists()
    assert (home / "plugins" / "prax" / "skills" / "prax-shift" / "SKILL.md").exists()


def test_dry_run_does_not_write(tmp_path):
    home = tmp_path / "home"
    result = install_codex_integration(home=str(home), dry_run=True)
    assert result["dry_run"] is True
    assert not home.exists()


def test_uninstall_preserves_unrelated_marketplace_entries(tmp_path):
    home = tmp_path / "home"
    install_codex_integration(
        home=str(home),
        codex_bin="/bin/codex",
        runner=_runner,
    )
    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    payload = json.loads(marketplace.read_text(encoding="utf-8"))
    payload["plugins"].append(
        {
            "name": "keep-me",
            "source": {"source": "local", "path": "./plugins/keep-me"},
        }
    )
    marketplace.write_text(json.dumps(payload), encoding="utf-8")

    uninstall_codex_integration(
        home=str(home),
        codex_bin="/bin/codex",
        runner=_runner,
    )

    remaining = json.loads(marketplace.read_text(encoding="utf-8"))
    assert [item["name"] for item in remaining["plugins"]] == ["keep-me"]
    assert not (home / "plugins" / "prax").exists()
