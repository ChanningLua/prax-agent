"""Real process-lifecycle checks; all work stays in a temporary Git fixture.

The verifier deliberately ignores SIGTERM so merely killing the runtime's
immediate process cannot satisfy these tests. No coding model is dispatched.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from prax import repository_harness as harness


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX harness process groups")


def _process_state(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True, text=True, timeout=2, check=False,
    )
    return result.stdout.strip()


def _active(pid: int) -> bool:
    """A reparented zombie is not an executing worker or a lock owner."""
    state = _process_state(pid)
    return bool(state) and not state.startswith("Z")


def _wait_for(predicate, *, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    assert predicate(), "process-lifecycle condition did not become true before deadline"


def _repository_lock_available(root: Path) -> bool:
    with (root / ".git/prax-harness.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


@pytest.fixture
def running_runtime(tmp_path):
    root = (tmp_path / "repo").resolve()
    (root / ".prax").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src/answer.txt").write_text("unchanged\n", encoding="utf-8")
    (root / ".gitignore").write_text(".prax/harness-runs/\n__pycache__/\n", encoding="utf-8")
    (root / ".prax/verifier.py").write_text(
        "import os, pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path('.prax/harness-runs/lifecycle/verifier.pid').write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    profile = {"schemaVersion": 1, "worker": "codex", "policy_files": []}
    task = {
        "schemaVersion": 1, "id": "lifecycle", "goal": "Offline verifier lifecycle test",
        "kind": "code-check",
        "repositories": [{"path": ".", "branch": "dev", "scope": ["src"]}],
        "verifier": {"cwd": ".", "argv": [sys.executable, "-B", ".prax/verifier.py"],
                     "timeout_seconds": 60},
        "budget": {"max_iterations": 1, "wall_seconds": 60, "stuck_after": 2},
    }
    harness.write_json(root / ".prax/harness.json", profile)
    harness.write_json(root / ".prax/task.json", task)
    for argv in (
        ["git", "init", "-b", "dev"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Lifecycle Test", "-c", "user.email=lifecycle@example.invalid",
         "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True, timeout=15)
    source = str(Path(harness.__file__).resolve().parents[1])
    environment = {**os.environ, "PYTHONPATH": source, "PYTHONDONTWRITEBYTECODE": "1"}
    parent = subprocess.Popen(
        [sys.executable, "-m", "prax", "shift", "run", "--task", ".prax/task.json"],
        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    directory = root / ".prax/harness-runs/lifecycle"
    runtime_pid = None
    verifier_pid = None
    try:
        def ready():
            if parent.poll() is not None:
                stdout, stderr = parent.communicate(timeout=2)
                pytest.fail(f"CLI exited before starting verifier: {stdout}\n{stderr}")
            marker = directory / "verifier.pid"
            return ((directory / "attempt.json").exists() and marker.exists()
                    and marker.read_text().isdigit())

        _wait_for(ready, timeout=15)
        runtime_pid = harness.read_json(directory / "attempt.json")["pid"]
        verifier_pid = int((directory / "verifier.pid").read_text())
        assert _active(runtime_pid) and _active(verifier_pid)
        assert not _repository_lock_available(root)
        yield SimpleNamespace(root=root, directory=directory, parent=parent,
                              runtime_pid=runtime_pid, verifier_pid=verifier_pid)
    finally:
        # Cleanup runs even against the broken implementation and handles the
        # SIGSTOP case without ever targeting the pytest process group.
        if runtime_pid is None and (directory / "attempt.json").exists():
            runtime_pid = harness.read_json(directory / "attempt.json")["pid"]
        if runtime_pid is not None:
            try:
                os.killpg(runtime_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)
        if verifier_pid is not None:
            _wait_for(lambda: not _active(verifier_pid), timeout=5)


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGKILL], ids=["sigterm", "sigkill"])
def test_cli_parent_exit_stops_runtime_and_term_ignoring_verifier(running_runtime, stop_signal):
    run = running_runtime
    run.parent.send_signal(stop_signal)
    stdout, _ = run.parent.communicate(timeout=8)
    _wait_for(lambda: not _active(run.runtime_pid) and not _active(run.verifier_pid))
    assert _repository_lock_available(run.root)
    result = harness.read_json(run.directory / "result.json")
    assert result.get("verified") is not True
    assert result.get("status") != "PASS"
    if stdout.strip():
        assert json.loads(stdout).get("verified") is not True


def test_killed_parent_does_not_release_lock_before_stopped_runtime_can_cleanup(running_runtime):
    run = running_runtime
    os.kill(run.runtime_pid, signal.SIGSTOP)
    try:
        _wait_for(lambda: _process_state(run.runtime_pid).startswith("T"))
        run.parent.kill()
        run.parent.communicate(timeout=8)
        assert _active(run.runtime_pid) and _active(run.verifier_pid)
        # Stopping the runtime also freezes its watchdog. The inherited lock
        # must bridge this deterministic parent-dead / runtime-alive window.
        assert not _repository_lock_available(run.root)
    finally:
        try:
            os.kill(run.runtime_pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
    _wait_for(lambda: not _active(run.runtime_pid) and not _active(run.verifier_pid))
    assert _repository_lock_available(run.root)


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT],
                         ids=["sigterm", "sighup", "sigint"])
def test_first_termination_signal_during_cleanup_cannot_skip_group_kill(
    tmp_path, monkeypatch, stop_signal,
):
    """The runtime may already be gone, taking its pipe watchdog with it."""
    directory = tmp_path / "run"
    directory.mkdir()
    contract = {"task": {"id": "cleanup", "budget": {"wall_seconds": 60}}}
    harness.write_json(directory / "result.json", {
        "status": "PASS", "verified": True, "elapsed_seconds": 0,
    })
    proc = Mock(pid=987654321, returncode=0)
    proc.wait.return_value = 0
    proc.poll.return_value = 0
    sent_signals = []
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(harness.os, "killpg", lambda pid, sig: sent_signals.append(sig))

    def interrupt_cleanup(_seconds):
        # This is delivered only while supervise owns its temporary handler;
        # killpg is mocked, so no external process receives a signal.
        signal.raise_signal(stop_signal)

    monkeypatch.setattr(harness.time, "sleep", interrupt_cleanup)
    interrupted = None
    try:
        harness.supervise(tmp_path, directory, contract)
    except BaseException as exc:  # Keep a broken SIGINT handler from interrupting the whole test run.
        interrupted = exc
    assert {sig: signal.getsignal(sig) for sig in previous_handlers} == previous_handlers
    assert interrupted is None, f"cleanup escaped before KILL/wait: {interrupted!r}"
    assert sent_signals == [signal.SIGTERM, signal.SIGKILL]
    assert proc.wait.call_count >= 2
