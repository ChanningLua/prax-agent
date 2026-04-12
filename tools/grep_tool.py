"""Grep tool — search file contents by regex."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolResult


class GrepTool(Tool):
    name = "Grep"
    description = "Search file contents using regex."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "case_insensitive": {"type": "boolean", "default": False},
        },
        "required": ["pattern"],
    }
    permission_level = PermissionLevel.SAFE
    is_concurrency_safe = True

    def __init__(self, cwd: str | None = None):
        self._cwd = cwd or "."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        pattern = params.get("pattern", "")
        flags = re.IGNORECASE if params.get("case_insensitive") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult(content=f"invalid regex: {exc}", is_error=True)
        search_path = Path(params.get("path", self._cwd))
        results: list[str] = []
        for fp in sorted(search_path.rglob("*")):
            if not fp.is_file():
                continue
            try:
                for i, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{fp}:{i}:{line}")
            except OSError:
                continue
        if not results:
            return ToolResult(content="No matches found.")
        return ToolResult(content="\n".join(results))
