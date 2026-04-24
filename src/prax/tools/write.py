"""Write tool — create or overwrite files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult


class WriteTool(Tool):
    name = "Write"
    description = "Write content to a file, creating directories as needed."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["file_path", "content"],
    }
    permission_level = PermissionLevel.REVIEW

    def file_accesses(self, params: dict[str, Any]) -> list[ToolFileAccess]:
        file_path = params.get("file_path")
        if not isinstance(file_path, str):
            return []
        return [ToolFileAccess(path=file_path, write=True)]

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        fp = Path(params["file_path"])
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(params["content"])
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=f"Wrote {fp}")
