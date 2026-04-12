"""AST-Grep tool — semantic code search and replace via ast-grep CLI."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any

from .base import PermissionLevel, Tool, ToolResult

CLI_LANGUAGES = [
    "bash", "c", "cpp", "csharp", "css", "elixir", "go", "haskell",
    "html", "java", "javascript", "json", "kotlin", "lua", "nix",
    "php", "python", "ruby", "rust", "scala", "solidity", "swift",
    "typescript", "tsx", "yaml",
]

MAX_MATCHES = 500
MAX_OUTPUT_BYTES = 1 * 1024 * 1024
TIMEOUT_SECS = 60


def _find_sg() -> str | None:
    return shutil.which("sg") or shutil.which("ast-grep")


def _parse_output(stdout: str) -> dict[str, Any]:
    if not stdout.strip():
        return {"matches": [], "totalMatches": 0, "truncated": False}

    truncated_bytes = len(stdout.encode()) >= MAX_OUTPUT_BYTES
    raw = stdout[:MAX_OUTPUT_BYTES] if truncated_bytes else stdout

    try:
        matches = json.loads(raw)
    except json.JSONDecodeError:
        if truncated_bytes:
            # try to recover partial JSON array
            idx = raw.rfind("},")
            if idx > 0:
                try:
                    matches = json.loads(raw[:idx + 1] + "]")
                except json.JSONDecodeError:
                    return {"matches": [], "totalMatches": 0, "truncated": True,
                            "error": "Output too large and could not be parsed"}
            else:
                return {"matches": [], "totalMatches": 0, "truncated": True,
                        "error": "Output too large and could not be parsed"}
        else:
            return {"matches": [], "totalMatches": 0, "truncated": False}

    total = len(matches)
    truncated_count = total > MAX_MATCHES
    final = matches[:MAX_MATCHES] if truncated_count else matches
    return {
        "matches": final,
        "totalMatches": total,
        "truncated": truncated_bytes or truncated_count,
    }


def _format_search(result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    matches = result["matches"]
    if not matches:
        return "No matches found"
    lines = []
    if result.get("truncated"):
        lines.append(f"[TRUNCATED] Showing first {len(matches)} of {result['totalMatches']}\n")
    lines.append(f"Found {len(matches)} match(es):\n")
    for m in matches:
        loc = f"{m['file']}:{m['range']['start']['line'] + 1}:{m['range']['start']['column'] + 1}"
        lines.append(loc)
        lines.append(f"  {m['lines'].strip()}")
        lines.append("")
    return "\n".join(lines)


def _format_replace(result: dict[str, Any], dry_run: bool) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    matches = result["matches"]
    if not matches:
        return "No matches found to replace"
    prefix = "[DRY RUN] " if dry_run else ""
    lines = [f"{prefix}{len(matches)} replacement(s):\n"]
    for m in matches:
        loc = f"{m['file']}:{m['range']['start']['line'] + 1}"
        lines.append(loc)
        lines.append(f"  {m['text']}")
        lines.append("")
    if dry_run:
        lines.append("Use dryRun=false to apply changes")
    return "\n".join(lines)


async def _run_sg(args: list[str], cwd: str) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return "", f"Timeout after {TIMEOUT_SECS}s", -1
    return stdout.decode(), stderr.decode(), proc.returncode or 0


class AstGrepSearchTool(Tool):
    name = "AstGrepSearch"
    is_concurrency_safe = True
    description = (
        "Search code patterns using AST-aware matching (ast-grep). "
        "Supports 25 languages. Use meta-variables: $VAR (single node), $$$ (multiple nodes). "
        "Patterns must be complete AST nodes. "
        "Examples: 'console.log($MSG)', 'def $FUNC($$$):', 'async function $NAME($$$) { $$$ }'"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "AST pattern with meta-variables"},
            "lang": {"type": "string", "enum": CLI_LANGUAGES, "description": "Target language"},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to search (default: ['.'])"},
            "globs": {"type": "array", "items": {"type": "string"}, "description": "Include/exclude globs (prefix ! to exclude)"},
            "context": {"type": "integer", "description": "Context lines around match"},
        },
        "required": ["pattern", "lang"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    @staticmethod
    def is_available() -> bool:
        return _find_sg() is not None

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        sg = _find_sg()
        if not sg:
            return ToolResult(
                content="ast-grep (sg) not found. Install: cargo install ast-grep --locked  OR  brew install ast-grep",
                is_error=True,
            )

        pattern = params["pattern"]
        lang = params["lang"]
        paths = params.get("paths") or [self._cwd]
        globs = params.get("globs") or []
        ctx = params.get("context")

        args = [sg, "run", "-p", pattern, "--lang", lang, "--json=compact"]
        if ctx and ctx > 0:
            args += ["-C", str(ctx)]
        for g in globs:
            args += ["--globs", g]
        args += paths

        stdout, stderr, code = await _run_sg(args, self._cwd)
        if code != 0 and not stdout.strip():
            if "No files found" in stderr:
                return ToolResult(content="No matches found")
            if stderr.strip():
                return ToolResult(content=f"Error: {stderr.strip()}", is_error=True)

        result = _parse_output(stdout)
        return ToolResult(content=_format_search(result))


class AstGrepReplaceTool(Tool):
    name = "AstGrepReplace"
    description = (
        "Replace code patterns using AST-aware rewriting (ast-grep). "
        "Dry-run by default. Use meta-variables in rewrite to preserve matched content. "
        "Example: pattern='console.log($MSG)' rewrite='logger.info($MSG)'"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "AST pattern to match"},
            "rewrite": {"type": "string", "description": "Replacement pattern (can use $VAR from pattern)"},
            "lang": {"type": "string", "enum": CLI_LANGUAGES},
            "paths": {"type": "array", "items": {"type": "string"}},
            "globs": {"type": "array", "items": {"type": "string"}},
            "dryRun": {"type": "boolean", "description": "Preview without applying (default: true)"},
        },
        "required": ["pattern", "rewrite", "lang"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    @staticmethod
    def is_available() -> bool:
        return _find_sg() is not None

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        sg = _find_sg()
        if not sg:
            return ToolResult(
                content="ast-grep (sg) not found. Install: cargo install ast-grep --locked  OR  brew install ast-grep",
                is_error=True,
            )

        pattern = params["pattern"]
        rewrite = params["rewrite"]
        lang = params["lang"]
        paths = params.get("paths") or [self._cwd]
        globs = params.get("globs") or []
        dry_run = params.get("dryRun", True)

        # First pass: collect matches with --json=compact
        args = [sg, "run", "-p", pattern, "--lang", lang, "--json=compact", "-r", rewrite]
        for g in globs:
            args += ["--globs", g]
        args += paths

        stdout, stderr, code = await _run_sg(args, self._cwd)
        if code != 0 and not stdout.strip():
            if stderr.strip():
                return ToolResult(content=f"Error: {stderr.strip()}", is_error=True)

        result = _parse_output(stdout)

        # Second pass: apply if not dry run and there are matches
        if not dry_run and result["matches"]:
            write_args = [sg, "run", "-p", pattern, "--lang", lang, "-r", rewrite, "--update-all"]
            for g in globs:
                write_args += ["--globs", g]
            write_args += paths
            _, werr, wcode = await _run_sg(write_args, self._cwd)
            if wcode != 0 and werr.strip():
                result["error"] = f"Replace failed: {werr.strip()}"

        return ToolResult(content=_format_replace(result, dry_run))
