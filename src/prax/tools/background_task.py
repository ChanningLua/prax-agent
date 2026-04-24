"""Background agent tools — detached subprocess task management.

Five tools that let an agent start, monitor, update, cancel, and list
background tasks. Unlike the previous ``asyncio.create_task`` design,
these tasks run as **detached OS subprocesses** launched via
``python -m prax._background_runner``, so they survive the parent
``prax prompt`` process exit. This is what makes the "24/7 background
work" story honest — an agent can kick off a task, the user can close
the terminal, and the task keeps running (and its result keeps landing
in ``.prax/tasks/<task_id>.json``) independently.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .base import PermissionLevel, Tool, ToolResult
from ..core.background_store import BackgroundTask, BackgroundTaskStore

# Retained for backwards compatibility with callers that still pass an
# ``executor``; the detached-subprocess design doesn't use it, but leaving
# the type alias prevents import breakage in downstream modules.
TaskExecutor = Callable[[str, str, str, int | None], Awaitable[str]]


def _pid_alive(pid: int | None) -> bool:
    """Best-effort check whether *pid* names a live process on this host.

    Uses ``os.kill(pid, 0)`` which raises ``ProcessLookupError`` if the
    process is gone and ``PermissionError`` if it exists but we can't
    signal it (which for our purposes still counts as 'alive').
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _spawn_background_runner(task_id: str, cwd: str) -> int:
    """Launch the detached runner subprocess and return its PID.

    POSIX: ``start_new_session=True`` puts the child in a new session so
    it becomes independent of the parent's controlling terminal.

    Windows: ``CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`` achieves the
    equivalent; Windows Task Scheduler support is milestone M4 but this
    branch at least doesn't blow up on Windows today.
    """
    argv = [sys.executable, "-m", "prax._background_runner", task_id, cwd]

    popen_kwargs: dict = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )

    proc = subprocess.Popen(argv, **popen_kwargs)
    return proc.pid


class StartTaskTool(Tool):
    """Start a background agent task and immediately return a task_id."""

    name = "StartTask"
    description = (
        "Start a background task that runs as a detached subprocess. "
        "Returns immediately with a task_id. The task keeps running even "
        "if this `prax prompt` session exits. Use CheckTask to poll for "
        "the result."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short description of the task"},
            "prompt": {"type": "string", "description": "Full prompt for the subagent"},
            "subagent_type": {
                "type": "string",
                "enum": ["general-purpose", "plan", "explore", "code"],
                "default": "general-purpose",
            },
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(
        self,
        *,
        store: BackgroundTaskStore,
        cwd: str,
        executor: TaskExecutor | None = None,
    ) -> None:
        self._store = store
        self._cwd = cwd
        # executor is accepted but unused — kept for backwards-compat with
        # older callers that still pass it. Detached subprocesses don't
        # need the in-process executor callable.
        self._executor = executor

    async def execute(self, params: dict) -> ToolResult:
        description = params.get("description", "")
        prompt = params.get("prompt", "")
        subagent_type = params.get("subagent_type", "general-purpose")

        if not isinstance(description, str) or not description.strip():
            return ToolResult(content="Error: description must be a non-empty string", is_error=True)
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(content="Error: prompt must be a non-empty string", is_error=True)

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = BackgroundTask(
            task_id=task_id,
            description=description,
            prompt=prompt,
            subagent_type=subagent_type,
            status="running",
            created_at=datetime.now(timezone.utc).isoformat(),
            cwd=self._cwd,
        )
        self._store.create(task)

        try:
            pid = _spawn_background_runner(task_id, self._cwd)
        except Exception as e:
            self._store.update_status(
                task_id,
                "error",
                error=f"failed to spawn background runner: {type(e).__name__}: {e}",
                exit_code=-1,
            )
            return ToolResult(
                content=json.dumps(
                    {"task_id": task_id, "status": "error",
                     "error": f"spawn failed: {e}"},
                    ensure_ascii=False,
                ),
                is_error=True,
            )

        # Runner will update its own pid/started_at once it's live, but
        # write a provisional pid now so CheckTask works even in the tiny
        # window before the runner boots.
        self._store.update_runtime(task_id, pid=pid)

        return ToolResult(
            content=json.dumps(
                {"task_id": task_id, "status": "running", "pid": pid},
                ensure_ascii=False,
            )
        )


class CheckTaskTool(Tool):
    """Check the status and result of a background task."""

    name = "CheckTask"
    description = (
        "Check the status of a background task started with StartTask. "
        "Returns status ('running'|'success'|'error'|'cancelled') and result if complete."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID returned by StartTask"},
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE

    def __init__(self, *, store: BackgroundTaskStore) -> None:
        self._store = store

    async def execute(self, params: dict) -> ToolResult:
        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(content="Error: task_id is required", is_error=True)

        task = self._store.get(task_id)
        if task is None:
            return ToolResult(
                content=json.dumps({"error": f"Task '{task_id}' not found"}, ensure_ascii=False),
                is_error=True,
            )

        payload: dict = {
            "task_id": task.task_id,
            "status": task.status,
            "created_at": task.created_at,
        }
        if task.pid is not None:
            payload["pid"] = task.pid
        if task.started_at is not None:
            payload["started_at"] = task.started_at
        if task.heartbeat_at is not None:
            payload["heartbeat_at"] = task.heartbeat_at
        if task.exit_code is not None:
            payload["exit_code"] = task.exit_code
        if task.result is not None:
            payload["result"] = task.result
        if task.error is not None:
            payload["error"] = task.error
        if task.finished_at is not None:
            payload["finished_at"] = task.finished_at

        # If the task JSON still says "running" but the subprocess is gone,
        # the runner died without reporting. Reconcile so the caller sees
        # a terminal state instead of a permanently-stale "running".
        if task.status == "running" and task.pid is not None and not _pid_alive(task.pid):
            self._store.update_status(
                task.task_id,
                "error",
                error="background runner exited without reporting a result (pid no longer alive)",
                exit_code=-1,
            )
            payload["status"] = "error"
            payload["error"] = "background runner exited without reporting a result (pid no longer alive)"
            payload["exit_code"] = -1

        return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))


class UpdateTaskTool(Tool):
    """Append a message to a running background task's context (best-effort)."""

    name = "UpdateTask"
    description = (
        "Send an update message to a running background task. "
        "The message is appended to the task's stored prompt for context. "
        "Has no effect on tasks that have already completed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "message": {"type": "string", "description": "Additional context to append to the task"},
        },
        "required": ["task_id", "message"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(self, *, store: BackgroundTaskStore) -> None:
        self._store = store

    async def execute(self, params: dict) -> ToolResult:
        task_id = params.get("task_id", "")
        message = params.get("message", "")

        if not task_id:
            return ToolResult(content="Error: task_id is required", is_error=True)
        if not message:
            return ToolResult(content="Error: message is required", is_error=True)

        task = self._store.get(task_id)
        if task is None:
            return ToolResult(
                content=json.dumps({"error": f"Task '{task_id}' not found"}, ensure_ascii=False),
                is_error=True,
            )

        if task.status != "running":
            return ToolResult(
                content=json.dumps(
                    {"task_id": task_id, "status": task.status, "updated": False,
                     "note": "Task is not running; message not appended"},
                    ensure_ascii=False,
                )
            )

        # Append message to prompt for visibility in the stored record
        task.prompt = task.prompt + f"\n\n[Update] {message}"
        self._store.create(task)

        return ToolResult(
            content=json.dumps({"task_id": task_id, "updated": True}, ensure_ascii=False)
        )


class CancelTaskTool(Tool):
    """Cancel a running background task."""

    name = "CancelTask"
    description = (
        "Cancel a background task. "
        "Only tasks in 'running' status can be cancelled. "
        "Sends SIGTERM to the detached subprocess and marks the task "
        "'cancelled'; if the process refuses to exit the user can send "
        "SIGKILL manually via the pid field returned by CheckTask."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(self, *, store: BackgroundTaskStore) -> None:
        self._store = store

    async def execute(self, params: dict) -> ToolResult:
        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(content="Error: task_id is required", is_error=True)

        task = self._store.get(task_id)
        if task is None:
            return ToolResult(
                content=json.dumps({"error": f"Task '{task_id}' not found"}, ensure_ascii=False),
                is_error=True,
            )

        if task.status != "running":
            return ToolResult(
                content=json.dumps(
                    {"task_id": task_id, "cancelled": False,
                     "note": f"Task is in status '{task.status}', cannot cancel"},
                    ensure_ascii=False,
                )
            )

        # Try to terminate the detached subprocess. If the pid is dead
        # already or unknown we still flip status to cancelled so the task
        # doesn't linger as "running" forever.
        signalled = False
        signal_error: str | None = None
        if task.pid is not None and _pid_alive(task.pid):
            try:
                os.kill(task.pid, signal.SIGTERM)
                signalled = True
            except Exception as e:
                signal_error = f"{type(e).__name__}: {e}"

        self._store.update_status(task_id, "cancelled")

        payload: dict = {"task_id": task_id, "cancelled": True, "signalled": signalled}
        if task.pid is not None:
            payload["pid"] = task.pid
        if signal_error is not None:
            payload["signal_error"] = signal_error
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class ListTasksTool(Tool):
    """List all background tasks, optionally filtered by status."""

    name = "ListTasks"
    description = (
        "List all background tasks. "
        "Optionally filter by status: 'running', 'success', 'error', or 'cancelled'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["running", "success", "error", "cancelled"],
                "description": "Filter by status (omit to list all)",
            },
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE

    def __init__(self, *, store: BackgroundTaskStore) -> None:
        self._store = store

    async def execute(self, params: dict) -> ToolResult:
        status_filter = params.get("status")
        tasks = self._store.list_all()

        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]

        result = [
            {
                "task_id": t.task_id,
                "description": t.description,
                "status": t.status,
                "created_at": t.created_at,
                **({"finished_at": t.finished_at} if t.finished_at else {}),
            }
            for t in tasks
        ]

        return ToolResult(
            content=json.dumps(result, ensure_ascii=False, indent=2)
        )
