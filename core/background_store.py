"""Background task store — persistent task tracking for async agent execution.

Persists BackgroundTask records to .prax/tasks/<task_id>.json,
analogous to ralph-main's prd.json pattern for cross-iteration state.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .persistence import atomic_write_json


BACKGROUND_TASK_SCHEMA_VERSION = "prax.background_task.v1"


@dataclass
class BackgroundTask:
    task_id: str          # "task_a3f2" format
    description: str
    prompt: str
    subagent_type: str
    status: str           # "running"|"success"|"error"|"cancelled"
    created_at: str       # ISO-8601
    result: str | None = None
    error: str | None = None
    finished_at: str | None = None
    schema_version: str = BACKGROUND_TASK_SCHEMA_VERSION


class BackgroundTaskStore:
    """Persist BackgroundTask records to .prax/tasks/<task_id>.json."""

    def __init__(self, cwd: str) -> None:
        self._tasks_dir = Path(cwd) / ".prax" / "tasks"

    def _ensure_dir(self) -> None:
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        return self._tasks_dir / f"{task_id}.json"

    def create(self, task: BackgroundTask) -> None:
        self._ensure_dir()
        atomic_write_json(self._path(task.task_id), asdict(task))

    def get(self, task_id: str) -> BackgroundTask | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BackgroundTask(**data)
        except Exception:
            return None

    def update_status(
        self,
        task_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        task = self.get(task_id)
        if task is None:
            return
        task.status = status
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if status in ("success", "error", "cancelled"):
            task.finished_at = datetime.now(timezone.utc).isoformat()
        self.create(task)

    def list_all(self) -> list[BackgroundTask]:
        if not self._tasks_dir.exists():
            return []
        tasks: list[BackgroundTask] = []
        for path in sorted(self._tasks_dir.glob("task_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tasks.append(BackgroundTask(**data))
            except Exception:
                continue
        return tasks

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        if task.status != "running":
            return False
        self.update_status(task_id, "cancelled")
        return True
