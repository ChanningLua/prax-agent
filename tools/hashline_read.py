"""HashlineRead tool — read files with hash-anchored line numbers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult


def compute_line_hash(line_number: int, content: str) -> str:
    """Compute a short hash for a line based on its number and content.

    Hash is based on trimmed content (trailing whitespace removed).
    """
    # Normalize: remove \r and trim trailing whitespace
    normalized = content.replace('\r', '').rstrip()

    # Use line number as seed for empty/whitespace-only lines
    has_significant = any(c.isalnum() for c in normalized)
    seed = 0 if has_significant else line_number

    # Compute hash
    payload = f"{seed}:{normalized}".encode('utf-8')
    full_hash = hashlib.sha256(payload).hexdigest()

    # Return first 2 characters (256 possible values)
    return full_hash[:2].upper()


def format_hashline(line_number: int, content: str) -> str:
    """Format a single line with hash anchor: linenum#HASH|content"""
    hash_code = compute_line_hash(line_number, content)
    return f"{line_number}#{hash_code}|{content}"


class HashlineReadTool(Tool):
    """Read files with hash-anchored line numbers for stable editing."""

    name = "HashlineRead"
    is_concurrency_safe = True
    description = (
        "Read a file with hash-anchored line numbers (format: linenum#HASH|content). "
        "Use this when you need to edit files with HashlineEdit for improved accuracy. "
        "The hash anchors prevent edit conflicts and provide validation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read"
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Starting line number (default: 1)"
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Ending line number (default: read all)"
            }
        },
        "required": ["file_path"],
        "additionalProperties": False
    }
    permission_level = PermissionLevel.SAFE

    def file_accesses(self, params: dict[str, object]) -> list[ToolFileAccess]:
        file_path = params.get("file_path")
        if not isinstance(file_path, str):
            return []
        return [ToolFileAccess(path=file_path, write=False)]

    async def execute(self, params: dict[str, object]) -> ToolResult:
        file_path = params.get("file_path")
        start_line = params.get("start_line", 1)
        end_line = params.get("end_line")

        if not isinstance(file_path, str):
            return ToolResult(content="Error: file_path must be a string", is_error=True)

        if not isinstance(start_line, int) or start_line < 1:
            return ToolResult(content="Error: start_line must be >= 1", is_error=True)

        if end_line is not None and (not isinstance(end_line, int) or end_line < start_line):
            return ToolResult(content="Error: end_line must be >= start_line", is_error=True)

        try:
            path = Path(file_path)
            if not path.exists():
                return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)

            if not path.is_file():
                return ToolResult(content=f"Error: Not a file: {file_path}", is_error=True)

            # Read file
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()

            # Apply line range
            total_lines = len(lines)
            actual_start = max(1, start_line)
            actual_end = min(total_lines, end_line) if end_line else total_lines

            if actual_start > total_lines:
                return ToolResult(
                    content=f"Error: start_line {actual_start} exceeds file length {total_lines}",
                    is_error=True
                )

            # Format with hash anchors
            result_lines = []
            for i in range(actual_start - 1, actual_end):
                line_num = i + 1
                content = lines[i].rstrip('\n\r')
                result_lines.append(format_hashline(line_num, content))

            result = '\n'.join(result_lines)
            return ToolResult(content=result)

        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)
