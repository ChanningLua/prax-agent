"""Persistent todo storage for Prax task planning.

File locking strategy:
- Reads use a shared lock (LOCK_SH) to allow concurrent reads.
- Writes use an exclusive lock (LOCK_EX) + atomic tmp→rename to prevent
  torn writes when multiple Ralph iterations or background tasks run in
  parallel against the same .prax/todos.json.
- fcntl is Unix-only; on Windows we fall back to a no-op (no locking).
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

# fcntl is Unix-only
if sys.platform != "win32":
    import fcntl
    _HAVE_FCNTL = True
else:
    _HAVE_FCNTL = False


VALID_TODO_STATUSES = {"pending", "in_progress", "completed"}


@contextmanager
def _shared_lock(fh: Any) -> Generator[None, None, None]:
    """Acquire a shared (read) lock, release on exit."""
    if _HAVE_FCNTL:
        fcntl.flock(fh, fcntl.LOCK_SH)
    try:
        yield
    finally:
        if _HAVE_FCNTL:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def _exclusive_lock(fh: Any) -> Generator[None, None, None]:
    """Acquire an exclusive (write) lock, release on exit."""
    if _HAVE_FCNTL:
        fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        yield
    finally:
        if _HAVE_FCNTL:
            fcntl.flock(fh, fcntl.LOCK_UN)


@dataclass(frozen=True)
class TodoItem:
    content: str
    active_form: str
    status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "content": self.content,
            "activeForm": self.active_form,
            "status": self.status,
        }


class TodoStore:
    def __init__(self, root_dir: str):
        self._root_dir = Path(root_dir)

    @property
    def todo_path(self) -> Path:
        return self._root_dir / ".prax" / "todos.json"

    def load(self) -> list[TodoItem]:
        path = self.todo_path
        if not path.exists():
            return []
        # Open for reading and hold a shared lock while we read.
        with open(path, encoding="utf-8") as fh:
            with _shared_lock(fh):
                data = json.load(fh)
        return [self._parse_item(item) for item in data]

    def save(self, todos: list[TodoItem]) -> Path:
        path = self.todo_path
        path.parent.mkdir(parents=True, exist_ok=True)
        persisted = [] if all(todo.status == "completed" for todo in todos) else todos
        payload = json.dumps(
            [todo.to_dict() for todo in persisted], indent=2, ensure_ascii=False
        )
        # Each writer uses a unique tmp file so concurrent writers never race
        # on the same tmp path. The exclusive lock + atomic rename guarantees
        # readers see only complete writes.
        tmp_path = path.with_name(f"todos.{os.getpid()}.{id(payload)}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                with _exclusive_lock(fh):
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
            tmp_path.replace(path)
        except Exception:
            # Clean up stale tmp on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        return path

    def clear(self) -> None:
        path = self.todo_path
        if path.exists():
            path.unlink()

    def replace(self, items: list[dict[str, Any]]) -> tuple[list[TodoItem], list[TodoItem], bool]:
        new_todos = [self._parse_item(item) for item in items]
        if not new_todos:
            raise ValueError("todos must not be empty")
        old_todos = self.load()
        self.save(new_todos)
        verification_nudge_needed = (
            all(todo.status == "completed" for todo in new_todos)
            and len(new_todos) >= 3
            and not any("verif" in todo.content.lower() for todo in new_todos)
        )
        return old_todos, new_todos, verification_nudge_needed

    def _parse_item(self, item: dict[str, Any]) -> TodoItem:
        if not isinstance(item, dict):
            raise ValueError("todo item must be an object")
        content = str(item.get("content", "")).strip()
        active_form = str(item.get("activeForm", "")).strip()
        status = str(item.get("status", "")).strip()
        if not content:
            raise ValueError("todo content must not be empty")
        if not active_form:
            raise ValueError("todo activeForm must not be empty")
        if status not in VALID_TODO_STATUSES:
            raise ValueError("todo status must be one of pending|in_progress|completed")
        return TodoItem(content=content, active_form=active_form, status=status)
