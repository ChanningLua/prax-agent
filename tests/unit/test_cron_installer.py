"""Tests for the cron installer — macOS LaunchAgent + Linux crontab helpers.

The installer only arranges for a single ``prax cron run`` dispatcher tick to
fire once a minute; individual jobs are evaluated by the dispatcher reading
``.prax/cron.yaml`` at tick time.
"""

from __future__ import annotations

import plistlib
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prax.core import cron_installer

# Also expose shutil inside cron_installer for monkeypatch targets.
# The module already imports shutil; we don't re-import it here, just reference.


# ── plist generation (platform-independent) ─────────────────────────────────


def test_build_plist_contents_has_start_interval_and_argv(tmp_path):
    plist = cron_installer.build_macos_plist(
        label="dev.prax.cron.dispatcher",
        cwd=str(tmp_path),
        prax_argv=["/usr/bin/python3", "-m", "prax", "cron", "run"],
        log_dir=str(tmp_path / ".prax" / "logs" / "cron"),
        env={"PATH": "/usr/bin"},
    )
    parsed = plistlib.loads(plist.encode("utf-8"))
    assert parsed["Label"] == "dev.prax.cron.dispatcher"
    assert parsed["StartInterval"] == 60
    assert parsed["ProgramArguments"][:3] == ["/usr/bin/python3", "-m", "prax"]
    assert "cron" in parsed["ProgramArguments"]
    assert "run" in parsed["ProgramArguments"]
    assert parsed["WorkingDirectory"] == str(tmp_path)
    assert parsed["StandardOutPath"].endswith("dispatcher.stdout.log")
    assert parsed["StandardErrorPath"].endswith("dispatcher.stderr.log")
    assert parsed["RunAtLoad"] is False
    assert parsed["EnvironmentVariables"] == {"PATH": "/usr/bin"}


# ── argv & env resolution (the P0-2 polish) ─────────────────────────────────


def test_resolve_prax_argv_prefers_env_override(monkeypatch):
    monkeypatch.setenv("PRAX_BIN", "/opt/custom/prax")
    monkeypatch.setattr(shutil, "which", lambda _name: "/should/not/use")
    assert cron_installer._resolve_prax_argv() == ["/opt/custom/prax", "cron", "run"]


def test_resolve_prax_argv_uses_which_when_no_override(monkeypatch):
    monkeypatch.delenv("PRAX_BIN", raising=False)
    monkeypatch.setattr(cron_installer.shutil, "which", lambda name: "/opt/homebrew/bin/prax" if name == "prax" else None)
    assert cron_installer._resolve_prax_argv() == ["/opt/homebrew/bin/prax", "cron", "run"]


def test_resolve_prax_argv_falls_back_to_python_m_prax(monkeypatch):
    monkeypatch.delenv("PRAX_BIN", raising=False)
    monkeypatch.setattr(cron_installer.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cron_installer.sys, "executable", "/opt/py/bin/python3")
    argv = cron_installer._resolve_prax_argv()
    assert argv == ["/opt/py/bin/python3", "-m", "prax", "cron", "run"]


def test_launchd_env_forwards_path_and_pythonpath(monkeypatch):
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/opt/homebrew/lib/node_modules/praxagent")
    env = cron_installer._launchd_env()
    assert env["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert env["PYTHONPATH"] == "/opt/homebrew/lib/node_modules/praxagent"


def test_launchd_env_omits_pythonpath_when_unset(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    env = cron_installer._launchd_env()
    assert "PYTHONPATH" not in env
    assert env["PATH"] == "/usr/bin"


def test_install_macos_writes_resolved_argv_and_env(tmp_path, monkeypatch):
    launch_agents = tmp_path / "LaunchAgents"
    monkeypatch.setattr(cron_installer, "_launchagents_dir", lambda: launch_agents)
    monkeypatch.setenv("PRAX_BIN", "/custom/prax")
    monkeypatch.setenv("PATH", "/custom/path")
    monkeypatch.setenv("PYTHONPATH", "/custom/pythonpath")
    monkeypatch.setattr(
        cron_installer.subprocess, "run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""),
    )

    result = cron_installer.install_macos(cwd=str(tmp_path))

    plist_path = launch_agents / "dev.prax.cron.dispatcher.plist"
    parsed = plistlib.loads(plist_path.read_text().encode("utf-8"))
    assert parsed["ProgramArguments"] == ["/custom/prax", "cron", "run"]
    assert parsed["EnvironmentVariables"] == {
        "PATH": "/custom/path",
        "PYTHONPATH": "/custom/pythonpath",
    }
    # Return payload includes the resolved argv for display, but hides env values.
    assert result["program_arguments"] == ["/custom/prax", "cron", "run"]
    assert set(result["env_keys"]) == {"PATH", "PYTHONPATH"}


# ── macOS install / uninstall (mocked launchctl + filesystem) ───────────────


def test_install_macos_writes_plist_and_loads(tmp_path, monkeypatch):
    launch_agents = tmp_path / "LaunchAgents"
    monkeypatch.setattr(cron_installer, "_launchagents_dir", lambda: launch_agents)

    calls: list[list[str]] = []
    def fake_run(argv, check=True, capture_output=True, text=True):
        calls.append(list(argv))
        return MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(cron_installer.subprocess, "run", fake_run)

    result = cron_installer.install_macos(
        cwd=str(tmp_path),
        prax_argv=["/usr/bin/prax"],
    )

    plist_path = launch_agents / "dev.prax.cron.dispatcher.plist"
    assert plist_path.exists()
    assert "StartInterval" in plist_path.read_text()

    # launchctl was asked to bootstrap (load) the plist.
    assert any("load" in c or "bootstrap" in c for c in calls)
    assert result["plist_path"] == str(plist_path)


def test_uninstall_macos_removes_plist_and_unloads(tmp_path, monkeypatch):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    plist_path = launch_agents / "dev.prax.cron.dispatcher.plist"
    plist_path.write_text("<plist />")
    monkeypatch.setattr(cron_installer, "_launchagents_dir", lambda: launch_agents)

    calls: list[list[str]] = []
    def fake_run(argv, check=True, capture_output=True, text=True):
        calls.append(list(argv))
        return MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(cron_installer.subprocess, "run", fake_run)

    result = cron_installer.uninstall_macos(cwd=str(tmp_path))

    assert not plist_path.exists()
    assert any("unload" in c or "bootout" in c for c in calls)
    assert result["uninstalled"] is True


def test_uninstall_macos_missing_plist_is_noop(tmp_path, monkeypatch):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    monkeypatch.setattr(cron_installer, "_launchagents_dir", lambda: launch_agents)
    monkeypatch.setattr(
        cron_installer.subprocess, "run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""),
    )

    result = cron_installer.uninstall_macos(cwd=str(tmp_path))
    assert result["uninstalled"] is False


# ── Linux crontab helper (print-line for manual install) ────────────────────


def test_linux_crontab_line_is_well_formed(tmp_path):
    line = cron_installer.build_linux_crontab_line(
        cwd=str(tmp_path),
        prax_argv=["/usr/bin/prax"],
    )
    assert line.startswith("* * * * * cd ")
    assert str(tmp_path) in line
    assert "prax cron run" in line
    assert "# prax-dispatcher" in line


# ── Platform dispatch ───────────────────────────────────────────────────────


def test_install_raises_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(cron_installer.sys, "platform", "win32")
    with pytest.raises(NotImplementedError, match="Windows"):
        cron_installer.install(cwd=str(tmp_path))


def test_install_routes_to_macos_on_darwin(monkeypatch, tmp_path):
    monkeypatch.setattr(cron_installer.sys, "platform", "darwin")
    called = {}

    def fake_install_macos(*, cwd, prax_argv=None):
        called["cwd"] = cwd
        called["prax_argv"] = prax_argv
        return {"plist_path": "/fake"}

    monkeypatch.setattr(cron_installer, "install_macos", fake_install_macos)
    result = cron_installer.install(cwd=str(tmp_path))
    assert called["cwd"] == str(tmp_path)
    assert result == {"plist_path": "/fake"}


def test_install_routes_to_linux_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(cron_installer.sys, "platform", "linux")
    result = cron_installer.install(cwd=str(tmp_path))
    # Linux path prints a crontab line for the user to add manually
    assert "crontab_line" in result
    assert "prax cron run" in result["crontab_line"]
