"""Read tool — read files with hashline-annotated output."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult
from .hashing import compute_line_hash


class ReadTool(Tool):
    name = "Read"
    description = "Read a file and return its contents with line numbers and hashes."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Start line (1-based)"},
            "limit": {"type": "integer", "description": "Number of lines to read"},
        },
        "required": ["file_path"],
    }
    permission_level = PermissionLevel.SAFE
    is_concurrency_safe = True

    def file_accesses(self, params: dict[str, Any]) -> list[ToolFileAccess]:
        file_path = params.get("file_path")
        if not isinstance(file_path, str):
            return []
        return [ToolFileAccess(path=file_path, write=False)]

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        fp = Path(params["file_path"])
        if not fp.exists():
            return ToolResult(content=f"File not found: {fp}", is_error=True)
        try:
            lines = fp.read_text(errors="replace").splitlines()
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)
        offset = params.get("offset", 1)
        limit = params.get("limit")
        start = max(offset - 1, 0)
        end = start + limit if limit else len(lines)
        out: list[str] = []
        for i, line in enumerate(lines[start:end], start=start + 1):
            h = compute_line_hash(i, line)
            out.append(f"{i}#{h}|{line}")
        return ToolResult(content="\n".join(out))
