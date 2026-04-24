"""Entry point for detached background tasks.

Launched by ``StartTaskTool`` via::

    python -m prax._background_runner <task_id> <cwd>

The parent ``prax prompt`` process uses ``subprocess.Popen`` with
``start_new_session=True`` on POSIX (or ``CREATE_NEW_PROCESS_GROUP`` on
Windows) so this runner keeps executing after the parent exits. That is
the whole point of the M1 milestone: background tasks must survive the
process that scheduled them.

Responsibilities:

1. Record ``pid`` and ``started_at`` in the task JSON so ``CheckTask``
   can detect liveness via ``os.kill(pid, 0)``.
2. Launch ``prax prompt <task.prompt>`` as a subprocess inside the
   provided cwd and stream its output to a log file.
3. While the agent subprocess runs, heartbeat every 30 s so stuck
   checks can tell the difference between "working" and "died silently".
4. On completion, write ``status`` + ``result`` / ``error`` + ``exit_code``
   + ``finished_at`` back to the task JSON.

Failure modes are all best-effort: if the task JSON disappears or the
agent subprocess crashes, we still try to record what we can before
exiting.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .core.background_store import BackgroundTaskStore


HEARTBEAT_INTERVAL_SECONDS = 30
RESULT_TAIL_BYTES = 8000
ERROR_TAIL_BYTES = 4000


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat_loop(store: BackgroundTaskStore, task_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        try:
            store.update_runtime(task_id, heartbeat_at=_iso_now())
        except Exception:
            # Heartbeat failures are never fatal — the agent subprocess is
            # the real work. We'll try again next tick.
            pass


def _resolve_prax_bin() -> list[str]:
    """Return the argv prefix to invoke `prax prompt ...`.

    Prefers the installed `prax` executable on PATH so the background
    subprocess behaves exactly like a fresh user-driven `prax prompt`
    invocation (same Python, same entry point, same config discovery).
    Falls back to ``python -m prax`` which works when prax is importable
    but not on PATH (e.g. inside a venv without activation).
    """
    override = os.environ.get("PRAX_BIN")
    if override:
        return [override]

    from shutil import which
    found = which("prax")
    if found:
        return [found]

    return [sys.executable, "-m", "prax"]


def _run_agent(task_prompt: str, cwd: str, log_path: Path) -> tuple[int, str, str]:
    """Spawn ``prax prompt`` in *cwd* and capture its output.

    Returns ``(exit_code, stdout_tail, stderr_tail)``. stdout is also
    mirrored to ``log_path`` in full so operators can inspect the run
    history without having to keep the tail in the task JSON.
    """
    argv = _resolve_prax_bin() + [
        "prompt",
        task_prompt,
        "--permission-mode",
        "workspace-write",
    ]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []

    with log_path.open("w", encoding="utf-8") as log_f:
        log_f.write(f"# background task started at {_iso_now()}\n")
        log_f.write(f"# cwd: {cwd}\n")
        log_f.write(f"# argv: {argv}\n\n")
        log_f.flush()

        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        # Drain both streams to the log file; keep tails in memory for the
        # task JSON.
        assert proc.stdout is not None and proc.stderr is not None

        def _drain(stream, buf, mirror_prefix=""):
            for line in stream:
                log_f.write(mirror_prefix + line)
                log_f.flush()
                buf.append(line)

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_buf, ""))
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_buf, "[stderr] "))
        t_out.start()
        t_err.start()

        exit_code = proc.wait()
        t_out.join()
        t_err.join()

    stdout_text = "".join(stdout_buf)
    stderr_text = "".join(stderr_buf)
    return exit_code, stdout_text[-RESULT_TAIL_BYTES:], stderr_text[-ERROR_TAIL_BYTES:]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print(
            "usage: python -m prax._background_runner <task_id> <cwd>",
            file=sys.stderr,
        )
        return 2

    task_id = args[0]
    cwd = args[1]

    store = BackgroundTaskStore(cwd=cwd)
    task = store.get(task_id)
    if task is None:
        print(f"background_runner: task '{task_id}' not found in {cwd}", file=sys.stderr)
        return 1

    store.update_runtime(
        task_id,
        pid=os.getpid(),
        started_at=_iso_now(),
        heartbeat_at=_iso_now(),
    )

    stop_heartbeat = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(store, task_id, stop_heartbeat),
        daemon=True,
    )
    hb_thread.start()

    log_path = Path(cwd) / ".prax" / "logs" / "background" / f"{task_id}-{int(time.time())}.log"

    try:
        exit_code, stdout_tail, stderr_tail = _run_agent(task.prompt, cwd, log_path)
        if exit_code == 0:
            store.update_status(
                task_id,
                "success",
                result=stdout_tail or "(no output)",
                exit_code=exit_code,
            )
        else:
            store.update_status(
                task_id,
                "error",
                error=f"prax prompt exited {exit_code}\n{stderr_tail}",
                exit_code=exit_code,
            )
        return exit_code
    except Exception as e:
        store.update_status(
            task_id,
            "error",
            error=f"background_runner crash: {type(e).__name__}: {e}",
            exit_code=-1,
        )
        return 1
    finally:
        stop_heartbeat.set()
        # Best-effort final heartbeat; ignore errors.
        try:
            store.update_runtime(task_id, heartbeat_at=_iso_now())
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
