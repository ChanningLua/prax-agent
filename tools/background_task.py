"""Background agent tools — async task management for the orchestration layer.

Five tools that let an agent start, monitor, update, cancel, and list
background tasks that run concurrently via asyncio.create_task().
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .base import PermissionLevel, Tool, ToolResult
from ..core.background_store import BackgroundTask, BackgroundTaskStore

TaskExecutor = Callable[[str, str, str, int | None], Awaitable[str]]


class StartTaskTool(Tool):
    """Start a background agent task and immediately return a task_id."""

    name = "StartTask"
    description = (
        "Start a background task that runs asynchronously. "
        "Returns immediately with a task_id. "
        "Use CheckTask to poll for the result."
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

    def __init__(self, *, store: BackgroundTaskStore, executor: TaskExecutor) -> None:
        self._store = store
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
        )
        self._store.create(task)

        async def _run() -> None:
            try:
                result = await self._executor(description, prompt, subagent_type, None)
                self._store.update_status(task_id, "success", result=result)
            except Exception as e:
                self._store.update_status(task_id, "error", error=str(e))

        asyncio.create_task(_run())

        return ToolResult(
            content=json.dumps({"task_id": task_id, "status": "running"}, ensure_ascii=False)
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
        if task.result is not None:
            payload["result"] = task.result
        if task.error is not None:
            payload["error"] = task.error
        if task.finished_at is not None:
            payload["finished_at"] = task.finished_at

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
        "Note: the underlying asyncio task may still complete, but its result will be discarded."
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

        cancelled = self._store.cancel(task_id)
        if not cancelled:
            task = self._store.get(task_id)
            if task is None:
                return ToolResult(
                    content=json.dumps({"error": f"Task '{task_id}' not found"}, ensure_ascii=False),
                    is_error=True,
                )
            return ToolResult(
                content=json.dumps(
                    {"task_id": task_id, "cancelled": False,
                     "note": f"Task is in status '{task.status}', cannot cancel"},
                    ensure_ascii=False,
                )
            )

        return ToolResult(
            content=json.dumps({"task_id": task_id, "cancelled": True}, ensure_ascii=False)
        )


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
