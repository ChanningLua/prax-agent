"""Glob tool — find files by pattern."""

from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolResult


class GlobTool(Tool):
    name = "Glob"
    description = "Find files matching a glob pattern."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern"},
            "path": {"type": "string", "description": "Directory to search in"},
        },
        "required": ["pattern"],
    }
    permission_level = PermissionLevel.SAFE
    is_concurrency_safe = True

    def __init__(self, cwd: str | None = None):
        self._cwd = cwd or "."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        pattern = params.get("pattern")
        if not pattern:
            return ToolResult(content="Missing required parameter: pattern", is_error=True)
        path = params.get("path", self._cwd)
        if not Path(path).is_absolute():
            return ToolResult(content="Path must be absolute", is_error=True)
        full = Path(path) / pattern
        matches = sorted(_glob.glob(str(full), recursive=True))
        if not matches:
            return ToolResult(content="No files matched the pattern.")
        return ToolResult(content="\n".join(matches))
