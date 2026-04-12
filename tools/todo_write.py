"""TodoWrite tool — persist and update the task list."""

from __future__ import annotations

from pathlib import Path

from ..core.todo_store import TodoStore
from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = "Update the structured task list for the current workspace session."
    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "activeForm": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "activeForm", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(self, *, cwd: str):
        self._cwd = cwd
        self._store = TodoStore(cwd)

    def file_accesses(self, _params: dict[str, object]) -> list[ToolFileAccess]:
        return [ToolFileAccess(path=str(self._store.todo_path), write=True)]

    async def execute(self, params: dict[str, object]) -> ToolResult:
        todos = params.get("todos")
        if not isinstance(todos, list):
            return ToolResult(content="Error: todos must be a list", is_error=True)
        try:
            old_todos, new_todos, verification_nudge_needed = self._store.replace(todos)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)

        payload = {
            "old_todos": [todo.to_dict() for todo in old_todos],
            "new_todos": [todo.to_dict() for todo in new_todos],
            "verification_nudge_needed": verification_nudge_needed,
            "path": str(self._store.todo_path),
        }
        import json

        return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))
