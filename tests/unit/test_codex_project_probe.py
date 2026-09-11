"""The project probe must never fall back to an unconfined Codex process."""
from __future__ import annotations

import io
import json
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from prax.integrations.codex import probe

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the project's conditional dependency.
    import tomli as tomllib


@pytest.fixture
def isolated_probe(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(probe, "_sandbox_available", lambda: True)
    monkeypatch.setattr(probe, "_find_codex", lambda supplied: "/probe/codex")
    proc = Mock(pid=987654321, stdin=io.BytesIO(), stdout=io.BytesIO())
    popen = Mock(return_value=proc)
    monkeypatch.setattr(probe.subprocess, "Popen", popen)
    stop = Mock()
    monkeypatch.setattr(probe, "_stop_process_group", stop)
    rpc = Mock()
    rpc.call.side_effect = [
        {"userAgent": "prax_project_probe/0.153.4 (Mac OS)"},
        {"data": [{"cwd": str(root), "skills": [], "errors": []}]},
    ]
    monkeypatch.setattr(probe, "_Rpc", Mock(return_value=rpc))
    return root, proc, popen, stop, rpc


def test_missing_sandbox_never_spawns(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "_sandbox_available", lambda: False)
    popen = Mock()
    monkeypatch.setattr(probe.subprocess, "Popen", popen)
    result = probe.probe_project_skills(tmp_path)
    assert not result["verified"] and not result["activation_verified"]
    assert "unconfined" in result["errors"][0]
    popen.assert_not_called()


def test_missing_codex_never_spawns(isolated_probe, monkeypatch):
    root, _, popen, _, _ = isolated_probe
    monkeypatch.setattr(probe, "_find_codex", lambda supplied: None)
    result = probe.probe_project_skills(root)
    assert not result["verified"] and "not found" in result["errors"][0]
    popen.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), "15"])
def test_invalid_timeout_never_spawns(isolated_probe, timeout):
    root, _, popen, _, _ = isolated_probe
    assert not probe.probe_project_skills(root, timeout_seconds=timeout)["verified"]
    popen.assert_not_called()


def test_probe_only_initializes_and_discovers_in_temporary_home(isolated_probe, monkeypatch):
    root, proc, popen, stop, rpc = isolated_probe
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-forward-test-value")
    captured = {}

    def inspect_launch(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        temporary_home = Path(kwargs["env"]["CODEX_HOME"])
        config = tomllib.loads((temporary_home / "config.toml").read_text())
        assert config["projects"][str(root)]["trust_level"] == "trusted"
        assert config["cli_auth_credentials_store"] == "file"
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["HOME"] != str(Path.home())
        return proc

    popen.side_effect = inspect_launch
    result = probe.probe_project_skills(root)
    assert result["verified"] and not result["activation_verified"]
    assert result["skills"] == []  # No arbitrary Prax name is used as a success shortcut.
    assert result["codex_version"] == "0.153.4"
    assert result["method"] == "isolated_discovery"
    assert [call.args[1] for call in rpc.call.call_args_list] == ["initialize", "skills/list"]
    rpc.send.assert_called_once_with({"method": "initialized", "params": {}})
    argv = captured["argv"]
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert "(deny network*)" in argv[2] and "(deny file-write*)" in argv[2]
    assert argv[3:] == ["/probe/codex", "-C", str(root), "app-server", "--stdio", "--strict-config"]
    assert captured["start_new_session"] is True
    assert not Path(captured["env"]["CODEX_HOME"]).exists()
    stop.assert_called_once_with(proc)
    assert proc.stdin.closed and proc.stdout.closed


def test_raw_metadata_keeps_disabled_and_nonrepo_scope(isolated_probe):
    root, _, _, _, rpc = isolated_probe
    skills = [{"name": "prax-shift", "scope": "user", "enabled": False,
               "path": "/not/the/project/SKILL.md"}]
    rpc.call.side_effect = [
        {"userAgent": "prax_project_probe/0.153.4"},
        {"data": [{"cwd": str(root), "skills": skills, "errors": []}]},
    ]
    result = probe.probe_project_skills(root)
    assert result["verified"] and result["skills"] == skills
    assert not result["activation_verified"]


def test_timeout_is_reported_and_process_group_is_cleaned(isolated_probe):
    root, proc, _, stop, rpc = isolated_probe
    rpc.call.side_effect = TimeoutError("offline discovery timed out")
    result = probe.probe_project_skills(root)
    assert not result["verified"] and "timed out" in result["errors"][0]
    stop.assert_called_once_with(proc)
    assert proc.stdin.closed and proc.stdout.closed


@pytest.mark.parametrize("data", [None, [], [{"cwd": "/wrong", "skills": [], "errors": []}]])
def test_missing_requested_project_is_not_verified(isolated_probe, data):
    root, _, _, _, rpc = isolated_probe
    rpc.call.side_effect = [{"userAgent": "prax_project_probe/0.153.4"}, {"data": data}]
    result = probe.probe_project_skills(root)
    assert not result["verified"] and result["errors"]


def test_discovery_errors_prevent_verified_result(isolated_probe):
    root, _, _, _, rpc = isolated_probe
    rpc.call.side_effect = [
        {"userAgent": "prax_project_probe/0.153.4"},
        {"data": [{"cwd": str(root), "skills": [], "errors": [{"message": "bad skill"}]}]},
    ]
    result = probe.probe_project_skills(root)
    assert not result["verified"] and "bad skill" in result["errors"][0]


def test_spawn_failure_does_not_try_unconfined_fallback(isolated_probe):
    root, _, popen, stop, _ = isolated_probe
    popen.side_effect = OSError("sandbox unavailable")
    result = probe.probe_project_skills(root)
    assert not result["verified"] and "sandbox unavailable" in result["errors"][0]
    assert popen.call_count == 1
    stop.assert_not_called()


def test_process_cleanup_kills_private_group_and_is_bounded(monkeypatch):
    kill = Mock()
    monkeypatch.setattr(probe.os, "killpg", kill)
    proc = Mock(pid=123456789)
    probe._stop_process_group(proc)
    kill.assert_called_once_with(proc.pid, signal.SIGKILL)
    proc.wait.assert_called_once_with(timeout=2)


def test_cleanup_failure_is_not_reported_as_verified(isolated_probe):
    root, proc, _, stop, _ = isolated_probe
    stop.side_effect = subprocess.TimeoutExpired("probe", 2)
    result = probe.probe_project_skills(root)
    assert not result["verified"] and result["errors"]
    assert proc.stdin.closed and proc.stdout.closed


@pytest.mark.skipif(not probe._sandbox_available(), reason="requires the real macOS sandbox")
def test_real_sandbox_denies_project_writes_and_network(tmp_path):
    """Use a fake metadata server, never a coding agent or a real user path."""
    root = tmp_path / "project"
    root.mkdir()
    server = tmp_path / "fake_server.py"
    server.write_text(
        "import errno, json, pathlib, socket, sys\n"
        "root = pathlib.Path.cwd()\n"
        "write_blocked = False\n"
        "try:\n"
        "    (root / 'must-not-be-created').write_text('blocked')\n"
        "except PermissionError:\n"
        "    write_blocked = True\n"
        "network_blocked = False\n"
        "try:\n"
        "    with socket.socket() as client:\n"
        "        client.settimeout(0.1)\n"
        "        client.connect(('127.0.0.1', 9))\n"
        "except OSError as error:\n"
        "    network_blocked = error.errno in (errno.EPERM, errno.EACCES)\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message['method'] == 'initialize':\n"
        "        result = {'userAgent': 'prax_project_probe/fake-test'}\n"
        "    elif message['method'] == 'skills/list':\n"
        "        result = {'data': [{'cwd': str(root), 'errors': [], 'skills': [\n"
        "            {'name': 'isolation-proof', 'write_blocked': write_blocked,\n"
        "             'network_blocked': network_blocked}]}]}\n"
        "    else:\n"
        "        continue\n"
        "    print(json.dumps({'id': message['id'], 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake-codex"
    wrapper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(server))} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    result = probe.probe_project_skills(root, codex_bin=str(wrapper), timeout_seconds=5)
    assert result["verified"], json.dumps(result)
    assert result["skills"] == [{"name": "isolation-proof", "write_blocked": True,
                                  "network_blocked": True}]
    assert not (root / "must-not-be-created").exists()
