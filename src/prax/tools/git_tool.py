"""Git tool — safe git operations."""

from __future__ import annotations

import asyncio
from typing import Any

from .base import PermissionLevel, Tool, ToolResult

_READ_OPS = frozenset({"status", "log", "diff", "show", "branch", "remote", "stash"})
_WRITE_OPS = frozenset({"add", "commit", "push", "pull", "merge", "rebase", "checkout", "reset"})
_DANGEROUS_FLAGS = frozenset({"--force", "-f", "--hard"})


class GitTool(Tool):
    name = "Git"
    description = "Execute git operations."
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["operation"],
    }
    permission_level = PermissionLevel.SAFE

    def __init__(self, cwd: str | None = None):
        self._cwd = cwd or "."

    def required_permission(self, params: dict[str, Any]) -> PermissionLevel:
        op = params.get("operation", "")
        if op in _WRITE_OPS:
            return PermissionLevel.REVIEW
        return PermissionLevel.SAFE

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        op = params.get("operation", "")
        args = params.get("args", [])
        if op not in _READ_OPS and op not in _WRITE_OPS:
            return ToolResult(content=f"invalid operation: {op!r}", is_error=True)
        if op in _READ_OPS and any(f in _DANGEROUS_FLAGS for f in args):
            return ToolResult(content="dangerous flag not allowed for read operations", is_error=True)
        cmd = ["git", op, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self._cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace")
        if proc.returncode != 0:
            return ToolResult(content=stderr.decode(errors="replace") or output, is_error=True)
        return ToolResult(content=output or "(no output)")
