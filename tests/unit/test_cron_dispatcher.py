"""Tests for the cron dispatcher — `prax cron run`."""

from __future__ import annotations

from datetime import datetime
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
        model="claude-sonnet-4-6",
    )
    now = datetime(2026, 4, 22, 12, 0)

    await run_due_jobs(str(cwd), now=now, runner=fake_runner, notifier=fake_notifier)

    argv = fake_runner.call_args[0][0]
    assert "--session-id" in argv
    assert "cron-demo" in argv
    assert "--model" in argv
    assert "claude-sonnet-4-6" in argv


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
