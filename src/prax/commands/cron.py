"""Cron command handlers — `prax cron {list,add,remove,run,install,uninstall}`.

The dispatcher (`run_due_jobs`) is the core runtime piece. Everything else is
thin CRUD over ``.prax/cron.yaml`` or OS-level install helpers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..core.cron_store import CronJob, CronStore, is_due

logger = logging.getLogger(__name__)


Runner = Callable[[list[str], Path], Awaitable[tuple[int, str, str]]]
Notifier = Callable[..., Awaitable[None]]


@dataclass
class JobRunResult:
    job_name: str
    status: str  # success | failure | paused | migration-required
    returncode: int
    log_path: Path
    error: str | None = None


# ── Dispatcher ───────────────────────────────────────────────────────────────


async def run_due_jobs(
    cwd: str,
    *,
    now: datetime,
    runner: Runner | None = None,
    notifier: Notifier | None = None,
    prax_argv_prefix: list[str] | None = None,
) -> list[JobRunResult]:
    """Serialize contract-job dispatchers; task locks are shared with harness."""
    jobs = CronStore(cwd).load()
    kwargs = {"now": now, "runner": runner, "notifier": notifier, "prax_argv_prefix": prax_argv_prefix}
    if any(job.run_mode == "orchestrate" and not job.requires_migration
           and is_due(job.schedule, now) for job in jobs):
        from ..repository_harness import run_lock
        directory = Path(cwd).resolve() / ".prax/harness-runs/.cron-dispatch"
        if directory.resolve() != directory:
            raise ValueError("cron control directory must not follow symbolic links")
        with run_lock(directory):
            return await _run_due_jobs_unlocked(cwd, **kwargs)
    return await _run_due_jobs_unlocked(cwd, **kwargs)


async def _run_due_jobs_unlocked(
    cwd: str, *, now: datetime, runner=None, notifier=None, prax_argv_prefix=None,
) -> list[JobRunResult]:
    """Run all cron jobs whose schedule fires at ``now``.

    ``runner`` (argv, log_path) -> (returncode, stdout, stderr) is injected for
    testability; default uses an asyncio subprocess.

    ``notifier`` (**kwargs matching NotifyTool) is injected similarly.
    """
    store = CronStore(cwd)
    jobs = store.load()
    due = [j for j in jobs if is_due(j.schedule, now)]
    if not due:
        return []

    runner = runner or _default_runner
    notifier = notifier or _build_default_notifier(cwd)
    prax_argv_prefix = prax_argv_prefix or _default_prax_argv_prefix()

    results: list[JobRunResult] = []
    for job in due:
        log_path = _log_path_for(cwd, job.name, now)
        if job.requires_migration:
            error = (
                "Legacy Shift job is disabled: create an explicit task contract "
                "with goal, scope, verifier, and budget, then remove/re-add this "
                "job with --run-mode shift --task. No worker was started."
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(error + "\n", encoding="utf-8")
            results.append(JobRunResult(job.name, "migration-required", 2, log_path, error))
            continue
        argv = _argv_for_job(job, prefix=prax_argv_prefix)
        try:
            returncode, stdout, stderr = await runner(argv, log_path)
        except Exception as e:
            logger.exception("cron runner raised for job %s", job.name)
            returncode, stdout, stderr = 1, "", f"runner error: {e}"

        status = "success" if returncode == 0 else "failure"
        if job.run_mode == "orchestrate":
            # Only independent, structured PASS proves completion. A normal
            # process exit or a worker's prose is not acceptance evidence.
            verdict = {}
            try:
                verdict = json.loads(stdout)
                if not isinstance(verdict, dict):
                    raise TypeError("Shift result must be a JSON object")
                task_file = (Path(cwd) / job.task_path).resolve()
                task_file.relative_to(Path(cwd).resolve())
                expected_id = json.loads(task_file.read_text(encoding="utf-8"))["id"]
                matching = verdict.get("run_id") == expected_id
                status = (
                    "success" if matching and returncode == 0 and verdict.get("status") == "PASS"
                    and verdict.get("verified") is True else
                    "paused" if matching and verdict.get("status") == "PAUSED" else "failure"
                )
            except (OSError, ValueError, KeyError, TypeError):
                status = "failure"
        result = JobRunResult(
            job_name=job.name,
            status=status,
            returncode=returncode,
            log_path=log_path,
        )
        results.append(result)

        notify_changed = True
        if job.run_mode == "orchestrate":
            from ..repository_harness import read_json, write_json
            state_dir = Path(cwd).resolve() / ".prax/harness-runs/.cron-dispatch"
            state_file = state_dir / f"{job.name}.json"
            if state_file.is_symlink():
                raise ValueError("cron notification state must not be a symbolic link")
            signal = {"status": status, "stop_reason": verdict.get("stop_reason") if isinstance(verdict, dict) else "invalid_result",
                      "task_path": job.task_path,
                      "run_id": verdict.get("run_id") if isinstance(verdict, dict) else None}
            previous = read_json(state_file) if state_file.exists() else None
            notify_changed = previous != signal
            # Record only after successful delivery, so a failed notification
            # can retry at the next explicitly scheduled opportunity.
        if notify_changed and status in (job.notify_on or []) and job.notify_channel:
            body = _format_notify_body(job, status, returncode, log_path, stdout, stderr)
            try:
                await notifier(
                    channel=job.notify_channel,
                    title=f"cron [{job.name}] {status}",
                    body=body,
                    level="info" if status == "success" else "error",
                )
                if job.run_mode == "orchestrate":
                    write_json(state_file, signal)
            except Exception as e:
                logger.warning("cron notify failed for job %s: %s", job.name, e)

    return results


def _argv_for_job(job: CronJob, *, prefix: list[str]) -> list[str]:
    if job.run_mode == "orchestrate":
        job.validate()
        return list(prefix) + ["shift", "run", "--task", job.task_path, "--resume-existing"]
    argv = list(prefix) + ["prompt", job.prompt]
    if job.session_id:
        argv += ["--session-id", job.session_id]
    if job.model:
        argv += ["--model", job.model]
    return argv


def _log_path_for(cwd: str, name: str, moment: datetime) -> Path:
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    return Path(cwd) / ".prax" / "logs" / "cron" / f"{name}-{stamp}.log"


def _format_notify_body(
    job: CronJob,
    status: str,
    returncode: int,
    log_path: Path,
    stdout: str,
    stderr: str,
) -> str:
    parts = [
        f"**job**: `{job.name}`",
        f"**schedule**: `{job.schedule}`",
        f"**status**: {status} (exit {returncode})",
        f"**log**: `{log_path}`",
    ]
    tail = (stdout or stderr).strip()
    if tail:
        tail = tail[-800:]
        parts.append(f"**tail**\n```\n{tail}\n```")
    return "\n\n".join(parts)


def _default_prax_argv_prefix() -> list[str]:
    """Default argv prefix — prefer `sys.executable -m prax` for portability."""
    override = os.environ.get("PRAX_BIN")
    if override:
        return [override]
    return [sys.executable, "-m", "prax"]


async def _default_runner(argv: list[str], log_path: Path) -> tuple[int, str, str]:
    """Launch argv as a subprocess, tee output to log_path, return (rc, stdout, stderr)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(log_path.parents[3]),
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(f"$ {' '.join(argv)}\n\n")
        if stdout:
            fh.write(stdout)
        if stderr:
            fh.write("\n--- stderr ---\n")
            fh.write(stderr)
    return proc.returncode or 0, stdout, stderr


def _build_default_notifier(cwd: str) -> Notifier:
    """Build a notifier that dispatches to NotifyTool's provider for a named channel."""
    async def _notify(**kwargs: Any) -> None:
        from ..core.config_files import load_notify_config
        from ..tools.notify import build_provider

        channel = kwargs["channel"]
        config = load_notify_config(cwd).get("channels", {})
        cfg = config.get(channel)
        if not cfg:
            raise ValueError(f"cron notify: channel {channel!r} not declared in notify.yaml")
        provider = build_provider(cfg)
        await provider.send(
            title=kwargs["title"],
            body=kwargs["body"],
            level=kwargs.get("level", "info"),
        )
    return _notify


# ── CRUD handlers (called by cli.py) ─────────────────────────────────────────


def handle_list(cwd: str, *, as_json: bool) -> dict:
    jobs = CronStore(cwd).load()
    data = [
        {**j.to_dict(), **({"status": "migration-required"} if j.requires_migration else {})}
        for j in jobs
    ]
    if as_json:
        return {"jobs": data}
    if not jobs:
        text = "(no cron jobs defined)"
    else:
        lines = [
            f"{j.name:<20} {j.schedule:<20} {j.task_path or j.prompt[:40]}"
            + (" [migration-required; disabled]" if j.requires_migration else "")
            for j in jobs
        ]
        text = "\n".join(lines)
    return {"jobs": data, "text": text}


def handle_add(cwd: str, *, name: str, schedule: str, prompt: str,
               session_id: str | None = None, model: str | None = None,
               notify_on: list[str] | None = None,
               notify_channel: str | None = None,
               run_mode: str = "prompt",
               agent_host: str | None = None,
               task_path: str | None = None) -> dict:
    job = CronJob(
        name=name,
        schedule=schedule,
        prompt=prompt,
        session_id=session_id,
        model=model,
        notify_on=notify_on or [],
        notify_channel=notify_channel,
        run_mode=run_mode,
        agent_host=agent_host,
        task_path=task_path,
    )
    CronStore(cwd).add(job)
    return {"text": f"Added cron job {name!r}", "job": job.to_dict()}


def handle_remove(cwd: str, *, name: str) -> dict:
    removed = CronStore(cwd).remove(name)
    return {"text": f"Removed cron job {name!r}", "job": removed.to_dict()}


def handle_run(cwd: str, *, as_json: bool) -> dict:
    """Entry point for the one-minute dispatcher tick."""
    now = datetime.now().replace(second=0, microsecond=0)
    results = asyncio.run(run_due_jobs(cwd, now=now))
    summary = [
        {"job": r.job_name, "status": r.status, "returncode": r.returncode,
         "log": str(r.log_path), **({"error": r.error} if r.error else {})}
        for r in results
    ]
    if as_json:
        return {"results": summary}
    text = (
        f"Processed {len(summary)} due job(s) at {now.isoformat()}:\n"
        + "\n".join(f"  - {r['job']}: {r['status']} (exit {r['returncode']})" for r in summary)
        if summary else f"No due jobs at {now.isoformat()}"
    )
    return {"results": summary, "text": text}
