"""ApplyPatch tool — hash-guarded multi-hunk file patching."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult
from .hashing import compute_line_hash


class ApplyPatchTool(Tool):
    name = "ApplyPatch"
    description = "Apply hash-guarded hunks to a file."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "hunks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_line": {"type": "integer"},
                        "delete_count": {"type": "integer"},
                        "expected_start_hash": {"type": "string"},
                        "replacement_lines": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["start_line", "delete_count", "replacement_lines"],
                },
            },
        },
        "required": ["file_path", "hunks"],
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
        lines = content.splitlines()
        hunks = sorted(params["hunks"], key=lambda h: h["start_line"], reverse=True)
        for hunk in hunks:
            start = hunk["start_line"]
            delete_count = hunk["delete_count"]
            expected_hash = hunk.get("expected_start_hash")
            if expected_hash and start <= len(lines):
                actual_hash = compute_line_hash(start, lines[start - 1])
                if actual_hash != expected_hash:
                    return ToolResult(
                        content=f"line hash mismatch at line {start}: expected {expected_hash}, got {actual_hash}",
                        is_error=True,
                    )
            replacement = hunk.get("replacement_lines", [])
            lines[start - 1 : start - 1 + delete_count] = replacement
        fp.write_text("\n".join(lines) + "\n")
        return ToolResult(content=f"Patched {fp}")
