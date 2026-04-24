"""TmuxBash tool — persistent shell session via tmux."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any

from .base import PermissionLevel, Tool, ToolResult

BLOCKED_SUBCOMMANDS = {
    "capture-pane", "capturep", "save-buffer", "saveb",
    "show-buffer", "showb", "pipe-pane", "pipep",
}

DESCRIPTION = """\
Execute tmux subcommands to manage persistent shell sessions (TUI apps, long-running processes).
Pass tmux subcommands WITHOUT the 'tmux' prefix.

Examples:
  new-session -d -s mydev
  send-keys -t mydev "python manage.py shell" Enter
  send-keys -t mydev "q" Enter

For one-shot commands, use the Bash tool instead.
For reading terminal output, use: tmux capture-pane -p -t <session> via Bash tool.\
"""


def _find_tmux() -> str | None:
    return shutil.which("tmux")


def _tokenize(cmd: str) -> list[str]:
    """Quote-aware tokenizer without external deps."""
    tokens: list[str] = []
    current = ""
    in_quote = False
    quote_char = ""
    escaped = False

    for ch in cmd:
        if escaped:
            current += ch
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch in ("'", '"') and not in_quote:
            in_quote = True
            quote_char = ch
        elif ch == quote_char and in_quote:
            in_quote = False
            quote_char = ""
        elif ch == " " and not in_quote:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += ch

    if current:
        tokens.append(current)
    return tokens


class TmuxBashTool(Tool):
    name = "InteractiveBash"
    description = DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "tmux_command": {
                "type": "string",
                "description": "tmux subcommand to execute (without 'tmux' prefix)",
            },
        },
        "required": ["tmux_command"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DANGEROUS

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    @staticmethod
    def is_available() -> bool:
        return _find_tmux() is not None

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        tmux = _find_tmux()
        if not tmux:
            return ToolResult(
                content="tmux not found. Install: brew install tmux  OR  apt-get install tmux",
                is_error=True,
            )

        cmd = params.get("tmux_command", "").strip()
        if not cmd:
            return ToolResult(content="Error: tmux_command is required", is_error=True)

        parts = _tokenize(cmd)
        if not parts:
            return ToolResult(content="Error: empty tmux command", is_error=True)

        subcommand = parts[0].lower()
        if subcommand in BLOCKED_SUBCOMMANDS:
            # Extract session name for helpful message
            session = "your-session"
            for i, p in enumerate(parts):
                if p in ("-t",) and i + 1 < len(parts):
                    session = parts[i + 1]
                    break
                elif p.startswith("-t") and len(p) > 2:
                    session = p[2:]
                    break
            return ToolResult(
                content=(
                    f"Error: '{parts[0]}' is blocked in InteractiveBash.\n\n"
                    f"Use the Bash tool instead:\n"
                    f"  tmux capture-pane -p -t {session}\n"
                    f"  tmux capture-pane -p -t {session} -S -1000"
                ),
                is_error=True,
            )

        proc = await asyncio.create_subprocess_exec(
            tmux, *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ToolResult(content="Error: tmux command timed out after 60s", is_error=True)

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        code = proc.returncode or 0

        if code != 0:
            msg = err.strip() or f"tmux exited with code {code}"
            return ToolResult(content=f"Error: {msg}", is_error=True)

        return ToolResult(content=out or "(no output)")
