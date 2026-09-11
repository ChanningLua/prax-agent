"""Codex CLI executor — runs ``codex exec`` as a subprocess.

Prax owns the durable loop, verification, budgets, and approval boundaries.
Codex is deliberately treated as a replaceable black-box worker.  The adapter
uses Codex's non-interactive JSONL protocol and defaults to the
``workspace-write`` sandbox; it never opts into ``--yolo``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .subprocess_env import child_env

_NESTED_CODEX_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_CI",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE",
)


@dataclass
class CodexExecutionResult:
    text: str
    usage: dict[str, Any] | None = None
    session_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None


def is_available() -> bool:
    return shutil.which("codex") is not None


class CodexCliExecutor:
    async def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        model: str | None = None,
        sandbox: str = "workspace-write",
        cwd: str | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> CodexExecutionResult:
        if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError(f"Unsupported Codex sandbox: {sandbox!r}")

        if session_id:
            # ``exec resume`` has no --sandbox flag, so use the documented
            # config override while keeping the follow-up prompt on stdin.
            cmd = [
                "codex",
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "-c",
                f'sandbox_mode="{sandbox}"',
            ]
            if model:
                cmd += ["--model", model]
            cmd += [session_id, "-"]
        else:
            cmd = [
                "codex",
                "exec",
                "--json",
                "--color",
                "never",
                "--sandbox",
                sandbox,
                "--skip-git-repo-check",
            ]
            if cwd:
                cmd += ["--cd", cwd]
            if model:
                cmd += ["--model", model]
            cmd += ["-"]

        # A Prax run may itself be started from Codex.  Remove only the
        # per-thread markers that would make the child look recursively nested;
        # authentication remains available through Codex's normal local cache.
        env = child_env(extra_pop=_NESTED_CODEX_ENV)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        stdout_bytes, stderr_bytes = await proc.communicate(prompt.encode("utf-8"))
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if proc.returncode:
            detail = stderr.strip() or stdout.strip() or "no subprocess output"
            raise RuntimeError(f"codex exec exited {proc.returncode}: {detail[-4000:]}")

        collected_text: list[str] = []
        usage: dict[str, Any] | None = None
        result_session_id = session_id
        tool_calls: list[dict[str, Any]] = []

        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(event, dict):
                continue

            event_type = event.get("type")
            if event_type in {"turn.failed", "error"}:
                detail = event.get("error") or event.get("message") or event
                raise RuntimeError(f"codex exec reported {event_type}: {str(detail)[-4000:]}")
            if event_type == "thread.started":
                result_session_id = event.get("thread_id", result_session_id)
                continue
            if event_type == "turn.completed":
                usage = event.get("usage")
                continue
            if event_type != "item.completed":
                continue

            item = event.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text", "")
                if text:
                    collected_text.append(text)
                    if on_text:
                        on_text(text)
            elif item_type in {
                "command_execution",
                "mcp_tool_call",
                "web_search",
                "file_change",
            }:
                tool_calls.append(dict(item))

        return CodexExecutionResult(
            text="\n".join(collected_text),
            usage=usage,
            session_id=result_session_id,
            tool_calls=tool_calls,
            # Codex JSONL currently reports tokens, not a stable USD total.
            cost_usd=None,
        )
