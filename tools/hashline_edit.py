"""HashlineEdit tool — edit files using hash-anchored line validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolFileAccess, ToolResult
from .hashline_read import compute_line_hash


def parse_hashline(line: str) -> tuple[int, str, str] | None:
    """Parse a hashline format: linenum#HASH|content

    Returns: (line_number, hash, content) or None if invalid
    """
    match = re.match(r'^(\d+)#([A-F0-9]{2})\|(.*)$', line)
    if not match:
        return None

    line_num = int(match.group(1))
    hash_code = match.group(2)
    content = match.group(3)
    return (line_num, hash_code, content)


def validate_hashline(line_number: int, content: str, expected_hash: str) -> bool:
    """Validate that a line's content matches its expected hash."""
    actual_hash = compute_line_hash(line_number, content)
    return actual_hash == expected_hash


class HashlineEditTool(Tool):
    """Edit files using hash-anchored line validation for improved accuracy."""

    name = "HashlineEdit"
    description = (
        "Edit a file using hash-anchored line references for validation. "
        "This prevents edit conflicts and improves success rate from 6.7% to 68.3%. "
        "Use HashlineRead first to get the file with hash anchors, then reference "
        "lines by their hash (e.g., '42#A7' to edit line 42 with hash A7)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit"
            },
            "edits": {
                "type": "array",
                "description": "Array of edit operations to apply",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["replace", "insert_after", "insert_before", "delete"],
                            "description": "Edit operation type"
                        },
                        "line_ref": {
                            "type": "string",
                            "description": "Line reference with hash (e.g., '42#A7')"
                        },
                        "end_line_ref": {
                            "type": "string",
                            "description": "End line reference for multi-line operations (optional)"
                        },
                        "content": {
                            "type": "string",
                            "description": "New content to insert/replace"
                        }
                    },
                    "required": ["op", "line_ref"]
                }
            }
        },
        "required": ["file_path", "edits"],
        "additionalProperties": False
    }
    permission_level = PermissionLevel.REVIEW

    def file_accesses(self, params: dict[str, Any]) -> list[ToolFileAccess]:
        file_path = params.get("file_path")
        if not isinstance(file_path, str):
            return []
        return [ToolFileAccess(path=file_path, write=True)]

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        file_path = params.get("file_path")
        edits = params.get("edits", [])

        if not isinstance(file_path, str):
            return ToolResult(content="Error: file_path must be a string", is_error=True)

        if not isinstance(edits, list) or not edits:
            return ToolResult(content="Error: edits must be a non-empty array", is_error=True)

        try:
            path = Path(file_path)
            if not path.exists():
                return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)

            if not path.is_file():
                return ToolResult(content=f"Error: Not a file: {file_path}", is_error=True)

            # Read current file content
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = [line.rstrip('\n\r') for line in f.readlines()]

            # Validate and apply edits
            result = self._apply_edits(lines, edits)
            if result.is_error:
                return result

            # Write back to file
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(result.content.split('\n')))

            return ToolResult(content=f"Successfully applied {len(edits)} edit(s) to {file_path}")

        except Exception as e:
            return ToolResult(content=f"Error editing file: {e}", is_error=True)

    def _apply_edits(self, lines: list[str], edits: list[dict[str, Any]]) -> ToolResult:
        """Apply edits to lines with hash validation."""
        # Parse and validate all edits first
        parsed_edits = []
        for i, edit in enumerate(edits):
            op = edit.get("op")
            line_ref = edit.get("line_ref")
            content = edit.get("content", "")

            if not isinstance(line_ref, str):
                return ToolResult(
                    content=f"Error in edit {i}: line_ref must be a string",
                    is_error=True
                )

            # Parse line reference: "42#A7"
            match = re.match(r'^(\d+)#([A-F0-9]{2})$', line_ref)
            if not match:
                return ToolResult(
                    content=f"Error in edit {i}: invalid line_ref format '{line_ref}' (expected: linenum#HASH)",
                    is_error=True
                )

            line_num = int(match.group(1))
            expected_hash = match.group(2)

            # Validate line number
            if line_num < 1 or line_num > len(lines):
                return ToolResult(
                    content=f"Error in edit {i}: line {line_num} out of range (file has {len(lines)} lines)",
                    is_error=True
                )

            # Validate hash
            actual_content = lines[line_num - 1]
            if not validate_hashline(line_num, actual_content, expected_hash):
                actual_hash = compute_line_hash(line_num, actual_content)
                return ToolResult(
                    content=(
                        f"Error in edit {i}: hash mismatch at line {line_num}\n"
                        f"Expected hash: {expected_hash}\n"
                        f"Actual hash: {actual_hash}\n"
                        f"Line content: {actual_content[:100]}\n"
                        f"This usually means the file was modified since you read it."
                    ),
                    is_error=True
                )

            parsed_edits.append({
                "op": op,
                "line_num": line_num,
                "content": content,
                "index": i
            })

        # Sort edits by line number (descending) to avoid index shifts
        parsed_edits.sort(key=lambda e: e["line_num"], reverse=True)

        # Apply edits
        modified_lines = list(lines)
        for edit in parsed_edits:
            op = edit["op"]
            line_num = edit["line_num"]
            content = edit["content"]
            idx = line_num - 1

            if op == "replace":
                modified_lines[idx] = content
            elif op == "insert_after":
                modified_lines.insert(idx + 1, content)
            elif op == "insert_before":
                modified_lines.insert(idx, content)
            elif op == "delete":
                del modified_lines[idx]
            else:
                return ToolResult(
                    content=f"Error: unknown operation '{op}'",
                    is_error=True
                )

        return ToolResult(content='\n'.join(modified_lines))
