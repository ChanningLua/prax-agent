"""Restricted verification command tool for workspace-local test/build checks."""

from __future__ import annotations

import asyncio
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from .base import PermissionLevel, Tool, ToolResult


_ALLOWED_PROGRAMS = {"pytest", "python", "python3", "npm", "pnpm", "cargo", "go"}
_DISALLOWED_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<"}


def parse_verify_command(command: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Invalid command syntax: {exc}") from exc

    if not argv:
        raise ValueError("Verification command must not be empty")

    if any(token in _DISALLOWED_TOKENS for token in argv):
        raise ValueError("Shell composition is not allowed in VerifyCommand")

    program = argv[0]
    if program not in _ALLOWED_PROGRAMS:
        raise ValueError(
            f"Unsupported verification command: {program!r}. "
            "Allowed commands are pytest, python -m pytest, npm test, pnpm test, cargo test, and go test."
        )

    if program in {"python", "python3"}:
        if len(argv) < 3 or argv[1] != "-m" or argv[2] != "pytest":
            raise ValueError("Only `python -m pytest ...` or `python3 -m pytest ...` are allowed")
        return argv

    if program in {"npm", "pnpm"}:
        if len(argv) < 2 or argv[1] != "test":
            raise ValueError("Only `npm test ...` and `pnpm test ...` are allowed")
        return argv

    if program == "cargo":
        if len(argv) < 2 or argv[1] != "test":
            raise ValueError("Only `cargo test ...` is allowed")
        return argv

    if program == "go":
        if len(argv) < 2 or argv[1] != "test":
            raise ValueError("Only `go test ...` is allowed")
        return argv

    # pytest
    return argv


def is_verify_command(command: str) -> bool:
    try:
        parse_verify_command(command)
        return True
    except ValueError:
        return False


class VerifyCommandTool(Tool):
    """Run a bounded verification command inside the active workspace.

    This tool is intentionally narrower than SandboxBash:
    - only repo-local verification commands are allowed
    - execution is performed without a shell
    - network and shell composition are not part of the interface
    """

    name = "VerifyCommand"
    description = (
        "Run a repository-local verification command such as `pytest -q`, "
        "`python -m pytest -q`, `npm test`, `pnpm test`, `cargo test`, or `go test ./...`. "
        "Use this for validating a fix before finishing. "
        "This is safer than SandboxBash and should be preferred for test/build verification."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Verification command to run inside the workspace root.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 300,
                "default": 60,
                "description": "Timeout in seconds for the verification command.",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(self, *, cwd: str):
        self._cwd = cwd

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        command = str(params.get("command", "")).strip()
        timeout = int(params.get("timeout", 60))

        try:
            argv = parse_verify_command(command)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)

        if argv[0] == "pytest" and shutil.which("pytest") is None:
            argv = [sys.executable, "-m", "pytest", *argv[1:]]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ToolResult(
                content=f"Error: verification command timed out after {timeout}s",
                is_error=True,
            )

        output = stdout.decode(errors="replace")
        err_output = stderr.decode(errors="replace")
        combined = output
        if err_output:
            combined = (combined + ("\n" if combined else "") + err_output).strip()
        combined = combined or "(no output)"
        is_error = proc.returncode != 0
        if is_error:
            if f"Exit code: {proc.returncode}" not in combined:
                combined = f"{combined}\nExit code: {proc.returncode}".strip()
            combined = (
                "Verification failed. Inspect the failure, fix the relevant source file, "
                "and rerun VerifyCommand.\n\n"
                f"{combined}"
            )
            return ToolResult(content=combined, is_error=True)

        return ToolResult(content=f"Verification passed.\n\n{combined}", is_error=False)
