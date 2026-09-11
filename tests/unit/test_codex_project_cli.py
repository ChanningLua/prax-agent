"""Offline CLI routing for repository-scoped Codex installation and diagnosis."""

from __future__ import annotations

import json
import runpy
import sys
from unittest.mock import Mock

import pytest

from prax import cli
from prax.core.permissions import PermissionMode


@pytest.fixture(autouse=True)
def integration_mocks(monkeypatch, tmp_path):
    """No test may reach a real installer, doctor, worker, or scheduler."""
    names = (
        "install_codex_integration",
        "doctor_codex_install",
        "install_claude_integration",
        "doctor_claude_install",
        "repair_claude_integration",
        "uninstall_codex_integration",
        "uninstall_claude_integration",
    )
    mocks = {}
    for name in names:
        mocked = Mock(name=name, side_effect=AssertionError(f"unexpected call: {name}"))
        monkeypatch.setattr(cli, name, mocked)
        mocks[name] = mocked

    def no_process(*args, **kwargs):
        pytest.fail("CLI routing tests must not start subprocesses")

    monkeypatch.setattr("subprocess.run", no_process)
    monkeypatch.setattr("subprocess.Popen", no_process)
    monkeypatch.setenv("HOME", str(tmp_path / "unused-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unused-home" / ".codex"))
    monkeypatch.setattr(
        "prax.core.config_files.load_permission_mode",
        Mock(return_value=PermissionMode.WORKSPACE_WRITE),
    )
    return mocks


@pytest.fixture(params=["directory", "file"])
def project(request, tmp_path, monkeypatch):
    """Nested repositories and worktrees both resolve to the nearest Git root."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / ".git").mkdir()
    root = outer / "project"
    root.mkdir()
    if request.param == "directory":
        (root / ".git").mkdir()
    else:
        (root / ".git").touch()
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    return root.resolve()


def allow_result(integration_mocks, name, **payload):
    mocked = integration_mocks[name]
    mocked.side_effect = None
    mocked.return_value = payload
    return mocked


def run_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["prax", *args])
    cli.main()


def assert_only_called(integration_mocks, expected):
    for name, mocked in integration_mocks.items():
        if name != expected:
            mocked.assert_not_called()


@pytest.mark.parametrize("command", ["install", "doctor"])
@pytest.mark.parametrize("flag,local,global_scope", [
    ("--local", True, False),
    ("--global", False, True),
])
def test_parser_accepts_explicit_codex_scope(command, flag, local, global_scope):
    args = cli.build_parser().parse_args([command, "--target", "codex", flag])
    assert args.local is local
    assert args.global_scope is global_scope


@pytest.mark.parametrize("dry_run", [False, True])
def test_local_install_routes_nearest_project_root(
    project, integration_mocks, monkeypatch, capsys, dry_run,
):
    installer = allow_result(
        integration_mocks, "install_codex_integration",
        target="codex", scope="project", status="planned" if dry_run else "ok",
    )
    args = ["install", "--target", "codex", "--local", "--json"]
    if dry_run:
        args.append("--dry-run")

    run_cli(monkeypatch, *args)

    installer.assert_called_once_with(
        local=True, project_root=str(project), dry_run=dry_run,
    )
    assert json.loads(capsys.readouterr().out)["scope"] == "project"
    assert_only_called(integration_mocks, "install_codex_integration")


@pytest.mark.parametrize("explicit_local", [False, True])
def test_doctor_routes_current_repository_without_user_fallback(
    project, integration_mocks, monkeypatch, capsys, explicit_local,
):
    doctor = allow_result(
        integration_mocks, "doctor_codex_install",
        target="codex", scope="project", status="ok",
    )
    args = ["doctor", "--target", "codex", "--json"]
    if explicit_local:
        args.append("--local")

    run_cli(monkeypatch, *args)

    doctor.assert_called_once_with(local=True, project_root=str(project))
    assert json.loads(capsys.readouterr().out)["scope"] == "project"
    assert_only_called(integration_mocks, "doctor_codex_install")


def test_global_doctor_explicitly_selects_user_even_inside_repository(
    project, integration_mocks, monkeypatch, capsys,
):
    doctor = allow_result(
        integration_mocks, "doctor_codex_install",
        target="codex", scope="user", status="ok",
    )

    run_cli(monkeypatch, "doctor", "--target", "codex", "--global", "--json")

    doctor.assert_called_once_with(target_root=None, local=False)
    assert json.loads(capsys.readouterr().out)["scope"] == "user"
    assert_only_called(integration_mocks, "doctor_codex_install")


@pytest.mark.parametrize("command", ["install", "doctor"])
@pytest.mark.parametrize("args,exit_code,message", [
    (["--target", "codex", "--local", "--root", "not-a-project-root"], 1,
     "do not combine it with --root"),
    (["--target", "claude", "--local"], 1, "only with --target codex"),
    (["--target", "codex", "--local", "--global"], 2, "not allowed with argument"),
])
def test_invalid_scope_never_calls_any_integration(
    integration_mocks, monkeypatch, capsys, command, args, exit_code, message,
):
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, command, *args)

    assert exc.value.code == exit_code
    assert message in capsys.readouterr().err
    for mocked in integration_mocks.values():
        mocked.assert_not_called()


@pytest.mark.parametrize("status", ["degraded", "missing", "error"])
def test_project_doctor_failure_exits_two_without_global_fallback(
    project, integration_mocks, monkeypatch, capsys, status,
):
    doctor = allow_result(
        integration_mocks, "doctor_codex_install",
        target="codex", scope="project", status=status,
    )

    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, "doctor", "--target", "codex", "--json")

    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == status
    doctor.assert_called_once_with(local=True, project_root=str(project))
    assert_only_called(integration_mocks, "doctor_codex_install")


def test_project_doctor_exception_exits_two_without_global_fallback(
    project, integration_mocks, monkeypatch, capsys,
):
    doctor = integration_mocks["doctor_codex_install"]
    doctor.side_effect = RuntimeError("offline probe unavailable")

    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, "doctor", "--target", "codex", "--json")

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "project"
    assert payload["status"] == "error"
    assert payload["error"] == "offline probe unavailable"
    doctor.assert_called_once_with(local=True, project_root=str(project))
    assert_only_called(integration_mocks, "doctor_codex_install")


@pytest.mark.parametrize("command,mock_name", [
    ("install", "install_codex_integration"),
    ("doctor", "doctor_codex_install"),
])
def test_python_module_entry_preserves_local_scope(
    project, integration_mocks, monkeypatch, capsys, command, mock_name,
):
    integration = allow_result(
        integration_mocks, mock_name, target="codex", scope="project", status="ok",
    )
    monkeypatch.setattr(
        sys, "argv", ["prax", command, "--target", "codex", "--local", "--json"],
    )

    runpy.run_module("prax", run_name="__main__")

    expected = {"local": True, "project_root": str(project)}
    if command == "install":
        expected["dry_run"] = False
    integration.assert_called_once_with(**expected)
    assert json.loads(capsys.readouterr().out)["scope"] == "project"
    assert_only_called(integration_mocks, mock_name)
