"""Claude CLI executor — runs `claude -p` as a subprocess.

Replaces direct LLM API calls when the `claude` binary is available.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ExecutionResult:
    text: str
    usage: dict[str, Any] | None = None
    session_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def is_available() -> bool:
    return shutil.which("claude") is not None


class ClaudeCliExecutor:
    async def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        cwd: str | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        cmd = [
            "claude", "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", permission_mode,
        ]
        if session_id:
            import re
            if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", session_id or ""):
                cmd += ["--session-id", session_id]
        if model and model.startswith("claude-"):
            cmd += ["--model", model]

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None

        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        collected_text: list[str] = []
        usage: dict[str, Any] | None = None
        result_session_id: str | None = session_id
        tool_calls: list[dict[str, Any]] = []
        seen_tool_call_ids: set[str] = set()

        async for raw_line in proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "assistant":
                # stream-json: assistant message with content blocks
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        chunk = block["text"]
                        collected_text.append(chunk)
                        if on_text:
                            on_text(chunk)
                    elif block.get("type") == "tool_use":
                        tool_id = str(
                            block.get("id")
                            or f"{block.get('name', '')}:{json.dumps(block.get('input', {}), sort_keys=True, ensure_ascii=False)}"
                        )
                        if tool_id in seen_tool_call_ids:
                            continue
                        seen_tool_call_ids.add(tool_id)
                        tool_calls.append(
                            {
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": block.get("input", {}),
                            }
                        )

            elif etype == "text":
                # simpler text event
                chunk = event.get("text", "")
                if chunk:
                    collected_text.append(chunk)
                    if on_text:
                        on_text(chunk)

            elif etype == "result":
                # final result event
                result_text = event.get("result", "")
                if result_text and not collected_text:
                    collected_text.append(result_text)
                usage = event.get("usage")
                result_session_id = event.get("session_id", result_session_id)

            elif etype == "system" and event.get("subtype") == "init":
                result_session_id = event.get("session_id", result_session_id)

        await proc.wait()

        return ExecutionResult(
            text="".join(collected_text),
            usage=usage,
            session_id=result_session_id,
            tool_calls=tool_calls,
        )
