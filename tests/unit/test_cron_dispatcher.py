"""Tests for the cron dispatcher — `prax cron run`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prax.core.cron_store import CronJob, CronStore


@pytest.fixture
def cwd(tmp_path):
    return tmp_path


@pytest.fixture
def fake_runner():
    """A subprocess runner that records calls and returns a canned returncode."""
    runner = AsyncMock()
    runner.return_value = (0, "ok-stdout", "")
    return runner


@pytest.fixture
def fake_notifier():
    return AsyncMock()


def _seed_job(cwd, **overrides) -> CronJob:
    job = CronJob(
        name=overrides.pop("name", "demo"),
        schedule=overrides.pop("schedule", "*/5 * * * *"),
        prompt=overrides.pop("prompt", "say hi"),
        **overrides,
    )
    store = CronStore(str(cwd))
    store.add(job)
    return job


# ── due filtering ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatches_due_job(cwd, fake_runner, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(cwd, schedule="*/5 * * * *")
    now = datetime(2026, 4, 22, 12, 0)  # minute 0 → matches */5

    results = await run_due_jobs(
        str(cwd), now=now, runner=fake_runner, notifier=fake_notifier
    )

    assert len(results) == 1
    assert results[0].status == "success"
    fake_runner.assert_called_once()
    args, kwargs = fake_runner.call_args
    # Runner is given the argv list and the log path.
    argv, log_path = args[0], args[1]
    assert "prompt" in argv
    assert "say hi" in argv
    assert str(log_path).endswith(".log")


@pytest.mark.asyncio
async def test_skips_not_due_job(cwd, fake_runner, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(cwd, schedule="*/5 * * * *")
    now = datetime(2026, 4, 22, 12, 3)  # not divisible by 5

    results = await run_due_jobs(
        str(cwd), now=now, runner=fake_runner, notifier=fake_notifier
    )

    assert results == []
    fake_runner.assert_not_called()


# ── session_id / model pass-through ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_passes_session_id_and_model(cwd, fake_runner, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(
        cwd,
        schedule="* * * * *",
        session_id="cron-demo",
        model="claude-sonnet-4-8",
    )
    now = datetime(2026, 4, 22, 12, 0)

    await run_due_jobs(str(cwd), now=now, runner=fake_runner, notifier=fake_notifier)

    argv = fake_runner.call_args[0][0]
    assert "--session-id" in argv
    assert "cron-demo" in argv
    assert "--model" in argv
    assert "claude-sonnet-4-8" in argv


@pytest.mark.asyncio
async def test_shift_passes_frozen_task_and_resume_semantics(cwd, fake_runner, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(
        cwd,
        schedule="* * * * *",
        run_mode="orchestrate",
        task_path=".prax/task.json",
    )

    await run_due_jobs(
        str(cwd),
        now=datetime(2026, 4, 22, 12, 0),
        runner=fake_runner,
        notifier=fake_notifier,
    )

    argv = fake_runner.call_args.args[0]
    verb_index = argv.index("shift")
    assert argv[verb_index:] == ["shift", "run", "--task", ".prax/task.json", "--resume-existing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code,payload,status", [
    (0, {"run_id": "task-a", "status": "PASS", "verified": True}, "success"),
    (0, {"run_id": "task-a", "status": "PASS", "verified": False}, "failure"),
    (0, {"run_id": "task-a", "status": "FEATURE-FAIL", "verified": False}, "failure"),
    (0, {"run_id": "other", "status": "PASS", "verified": True}, "failure"),
    (2, {"run_id": "task-a", "status": "PASS", "verified": True}, "failure"),
    (3, {"run_id": "task-a", "status": "PAUSED", "verified": False}, "paused"),
    (0, "worker says done", "failure"),
])
async def test_shift_requires_structured_matching_acceptance(cwd, fake_runner, fake_notifier, code, payload, status):
    from prax.commands.cron import run_due_jobs
    _seed_job(cwd, run_mode="orchestrate", task_path=".prax/task.json")
    (cwd / ".prax/task.json").write_text(json.dumps({"id": "task-a"}))
    fake_runner.return_value = (code, json.dumps(payload), "")
    result = await run_due_jobs(str(cwd), now=datetime(2026, 9, 10, 12, 0),
                                runner=fake_runner, notifier=fake_notifier)
    assert result[0].status == status


@pytest.mark.asyncio
async def test_shift_success_notification_is_not_repeated(cwd, fake_runner, fake_notifier):
    from prax.commands.cron import run_due_jobs
    _seed_job(cwd, run_mode="orchestrate", task_path=".prax/task.json",
              notify_on=["success"], notify_channel="ops")
    (cwd / ".prax/task.json").write_text(json.dumps({"id": "task-a"}))
    fake_runner.return_value = (0, json.dumps({"run_id": "task-a", "status": "PASS", "verified": True}), "")
    for _ in range(2):
        await run_due_jobs(str(cwd), now=datetime(2026, 9, 10, 12, 0),
                           runner=fake_runner, notifier=fake_notifier)
    fake_notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_notify_channel_does_not_acknowledge_delivery(cwd, fake_runner, monkeypatch):
    from prax.commands.cron import run_due_jobs
    _seed_job(cwd, run_mode="orchestrate", task_path=".prax/task.json",
              notify_on=["success"], notify_channel="ops")
    (cwd / ".prax/task.json").write_text(json.dumps({"id": "task-a"}))
    fake_runner.return_value = (0, json.dumps({"run_id": "task-a", "status": "PASS", "verified": True}), "")
    config = {"channels": {}}
    provider = AsyncMock()
    monkeypatch.setattr("prax.core.config_files.load_notify_config", lambda cwd: config)
    monkeypatch.setattr("prax.tools.notify.build_provider", lambda cfg: provider)
    now = datetime(2026, 9, 10, 12, 0)
    await run_due_jobs(str(cwd), now=now, runner=fake_runner)
    state_file = cwd / ".prax/harness-runs/.cron-dispatch/demo.json"
    assert not state_file.exists()
    provider.send.assert_not_awaited()
    config["channels"]["ops"] = {"type": "offline-test"}
    await run_due_jobs(str(cwd), now=now, runner=fake_runner)
    provider.send.assert_awaited_once()
    assert state_file.exists()
    await run_due_jobs(str(cwd), now=now, runner=fake_runner)
    provider.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_shift_is_reported_disabled_without_blocking_valid_jobs(cwd, fake_runner, fake_notifier):
    from prax.commands.cron import _argv_for_job, handle_list, run_due_jobs
    store = CronStore(str(cwd))
    legacy = CronJob("legacy", "* * * * *", "old goal", run_mode="orchestrate", agent_host="claude")
    store.save([legacy, CronJob("valid", "* * * * *", "read status")])
    listed = handle_list(str(cwd), as_json=True)
    assert listed["jobs"][0]["status"] == "migration-required"
    assert "migration-required; disabled" in handle_list(str(cwd), as_json=False)["text"]
    with pytest.raises(ValueError, match="require --task"):
        _argv_for_job(legacy, prefix=["prax"])
    results = await run_due_jobs(str(cwd), now=datetime(2026, 9, 10, 12, 0),
                                runner=fake_runner, notifier=fake_notifier)
    assert [result.status for result in results] == ["migration-required", "success"]
    assert results[0].returncode == 2
    assert "No worker was started" in results[0].error
    assert results[0].error in results[0].log_path.read_text()
    fake_runner.assert_awaited_once()
    assert fake_runner.await_args.args[0][-2:] == ["prompt", "read status"]
    fake_notifier.assert_not_awaited()


# ── log file creation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_file_written_under_prax_logs_cron(cwd, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(cwd, schedule="* * * * *")
    now = datetime(2026, 4, 22, 12, 0)

    # Real runner stub: just write stdout to the given log path.
    async def runner(argv, log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("hello world\n")
        return 0, "hello world\n", ""

    results = await run_due_jobs(str(cwd), now=now, runner=runner, notifier=fake_notifier)

    log = results[0].log_path
    assert log.exists()
    assert "hello world" in log.read_text()
    assert log.parent == cwd / ".prax" / "logs" / "cron"


@pytest.mark.asyncio
async def test_real_shift_cron_resume_is_not_invalidated_by_its_previous_log(cwd, monkeypatch):
    """Exercise the actual dispatcher/CLI/child with acceptance already passing."""
    import prax
    from prax.commands.cron import run_due_jobs

    monkeypatch.setenv("PYTHONPATH", str(Path(prax.__file__).resolve().parents[1]))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.delenv("PRAX_BIN", raising=False)
    (cwd / ".prax").mkdir()
    (cwd / "src").mkdir()
    (cwd / "tests").mkdir()
    (cwd / "src/answer.txt").write_text("correct")
    (cwd / "tests/test_acceptance.py").write_text(
        "import unittest\nclass Acceptance(unittest.TestCase):\n"
        "    def test_answer(self): self.assertEqual(1, 1)\n"
    )
    # Deliberately do not ignore cron logs: scheduler-owned output must not
    # require consumers to weaken their repository's drift checks.
    (cwd / ".gitignore").write_text("__pycache__/\n.prax/harness-runs/\n")
    (cwd / ".prax/harness.json").write_text(json.dumps({"schemaVersion": 1, "worker": "codex"}))
    task = {
        "schemaVersion": 1, "id": "cron-offline", "goal": "Check the answer", "kind": "code-check",
        "repositories": [{"path": ".", "branch": "main", "scope": ["src"]}],
        "verifier": {"argv": [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
                     "cwd": ".", "timeout_seconds": 15},
        "budget": {"max_iterations": 1, "wall_seconds": 60, "stuck_after": 2},
    }
    (cwd / ".prax/task.json").write_text(json.dumps(task))
    _seed_job(cwd, schedule="* * * * *", run_mode="orchestrate", task_path=".prax/task.json")
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Cron test", "GIT_AUTHOR_EMAIL": "cron@example.invalid",
           "GIT_COMMITTER_NAME": "Cron test", "GIT_COMMITTER_EMAIL": "cron@example.invalid"}
    for argv in (["git", "init", "-q", "-b", "main"], ["git", "add", "."],
                 ["git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"]):
        subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True)
    now = datetime(2026, 9, 10, 12, 0)
    for offset in range(2):
        results = await run_due_jobs(str(cwd), now=now + timedelta(minutes=offset))
        assert results[0].status == "success", results[0].log_path.read_text()
        assert results[0].returncode == 0
        assert results[0].log_path.exists()
    result = json.loads((cwd / ".prax/harness-runs/cron-offline/result.json").read_text())
    assert result["verified"] is True
    assert result["iterations"] == 0  # No real worker/model was invoked.


# ── notify_on semantics ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_on_success_fires(cwd, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(
        cwd,
        schedule="* * * * *",
        notify_on=["success"],
        notify_channel="daily",
    )
    now = datetime(2026, 4, 22, 12, 0)

    async def runner(argv, log_path):
        return 0, "ok", ""

    await run_due_jobs(str(cwd), now=now, runner=runner, notifier=fake_notifier)

    fake_notifier.assert_awaited_once()
    call_kwargs = fake_notifier.await_args.kwargs
    assert call_kwargs["channel"] == "daily"
    assert "success" in call_kwargs["title"].lower() or "demo" in call_kwargs["title"]
    assert call_kwargs["level"] == "info"


@pytest.mark.asyncio
async def test_notify_on_failure_fires_with_error_level(cwd, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(
        cwd,
        schedule="* * * * *",
        notify_on=["failure"],
        notify_channel="alert",
    )
    now = datetime(2026, 4, 22, 12, 0)

    async def runner(argv, log_path):
        return 1, "", "boom"

    results = await run_due_jobs(
        str(cwd), now=now, runner=runner, notifier=fake_notifier
    )

    assert results[0].status == "failure"
    fake_notifier.assert_awaited_once()
    kwargs = fake_notifier.await_args.kwargs
    assert kwargs["channel"] == "alert"
    assert kwargs["level"] == "error"


@pytest.mark.asyncio
async def test_notify_not_fired_when_trigger_absent(cwd, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(
        cwd,
        schedule="* * * * *",
        notify_on=["failure"],  # only failure
        notify_channel="alert",
    )
    now = datetime(2026, 4, 22, 12, 0)

    async def runner(argv, log_path):
        return 0, "ok", ""

    await run_due_jobs(str(cwd), now=now, runner=runner, notifier=fake_notifier)

    fake_notifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_noop_when_channel_not_set(cwd, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(cwd, schedule="* * * * *", notify_on=["success"])  # no channel
    now = datetime(2026, 4, 22, 12, 0)

    async def runner(argv, log_path):
        return 0, "ok", ""

    await run_due_jobs(str(cwd), now=now, runner=runner, notifier=fake_notifier)

    fake_notifier.assert_not_awaited()


# ── multiple jobs ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_due_jobs_run_sequentially(cwd, fake_notifier):
    from prax.commands.cron import run_due_jobs

    _seed_job(cwd, name="a", schedule="* * * * *")
    _seed_job(cwd, name="b", schedule="* * * * *")
    now = datetime(2026, 4, 22, 12, 0)

    calls: list[str] = []

    async def runner(argv, log_path):
        # pull the job name from log_path (e.g. a-2026...-log)
        calls.append(log_path.stem.split("-")[0])
        return 0, "", ""

    results = await run_due_jobs(str(cwd), now=now, runner=runner, notifier=fake_notifier)

    assert {r.job_name for r in results} == {"a", "b"}
    assert sorted(calls) == ["a", "b"]
