"""Edit tool — exact string replacement with optional checksum guard."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult


class EditTool(Tool):
    name = "Edit"
    description = "Replace an exact string in a file."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "expected_sha256": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    permission_level = PermissionLevel.REVIEW

    def file_accesses(self, params: dict[str, Any]) -> list[ToolFileAccess]:
        file_path = params.get("file_path")
        if not isinstance(file_path, str):
            return []
        return [ToolFileAccess(path=file_path, write=True)]

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        fp = Path(params["file_path"])
        if not fp.exists():
            return ToolResult(content=f"File not found: {fp}", is_error=True)
        content = fp.read_text()
        expected = params.get("expected_sha256")
        if expected:
            actual = hashlib.sha256(content.encode()).hexdigest()
            if actual != expected:
                return ToolResult(content="checksum changed since last read", is_error=True)
        old = params["old_string"]
        count = content.count(old)
        if count == 0:
            return ToolResult(content=f"old_string not found in {fp}", is_error=True)
        if count > 1:
            return ToolResult(content=f"old_string appears multiple times ({count})", is_error=True)
        fp.write_text(content.replace(old, params["new_string"], 1))
        return ToolResult(content=f"Edited {fp}")
