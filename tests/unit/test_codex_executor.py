"""Tests for the non-interactive Codex CLI adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.core.codex_cli_executor import CodexCliExecutor, is_available


def _process(*events: dict, returncode: int = 0, stderr: str = "") -> MagicMock:
    process = MagicMock()
    process.returncode = returncode
    stdout = "\n".join(json.dumps(event) for event in events)
    process.communicate = AsyncMock(
        return_value=(stdout.encode("utf-8"), stderr.encode("utf-8"))
    )
    return process


def test_is_available_uses_path_lookup():
    with patch("prax.core.codex_cli_executor.shutil.which", return_value="/bin/codex"):
        assert is_available() is True
    with patch("prax.core.codex_cli_executor.shutil.which", return_value=None):
        assert is_available() is False


@pytest.mark.asyncio
async def test_run_parses_real_codex_jsonl_protocol():
    process = _process(
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "done"},
        },
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "pytest -q"},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        },
    )
    callback = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
        result = await CodexCliExecutor().run(
            "make it pass",
            model="gpt-5.6-codex",
            cwd="/tmp/project",
            on_text=callback,
        )

    command = spawn.call_args.args
    assert command[:3] == ("codex", "exec", "--json")
    assert "--sandbox" in command
    assert "workspace-write" in command
    assert "--yolo" not in command
    assert ("--cd", "/tmp/project") == (
        command[command.index("--cd")],
        command[command.index("--cd") + 1],
    )
    process.communicate.assert_awaited_once_with(b"make it pass")
    assert result.text == "done"
    assert result.session_id == "thread-1"
    assert result.usage == {"input_tokens": 12, "output_tokens": 3}
    assert result.tool_calls[0]["type"] == "command_execution"
    callback.assert_called_once_with("done")


@pytest.mark.asyncio
async def test_run_removes_nested_codex_and_proxy_environment(monkeypatch):
    for key in (
        "CODEX_THREAD_ID",
        "CODEX_CI",
        "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
        "CODEX_PERMISSION_PROFILE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
    ):
        monkeypatch.setenv(key, "inherited")
    process = _process()

    with patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
        await CodexCliExecutor().run("inspect")

    child = spawn.call_args.kwargs["env"]
    assert "CODEX_THREAD_ID" not in child
    assert "CODEX_CI" not in child
    assert "CODEX_INTERNAL_ORIGINATOR_OVERRIDE" not in child
    assert "CODEX_PERMISSION_PROFILE" not in child
    assert "HTTP_PROXY" not in child
    assert "HTTPS_PROXY" not in child
    assert child["NO_PROXY"] == "*"


@pytest.mark.asyncio
async def test_resume_uses_documented_resume_form_and_sandbox_override():
    process = _process(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "continued"},
        }
    )

    with patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
        result = await CodexCliExecutor().run(
            "continue",
            session_id="thread-1",
            sandbox="read-only",
            cwd="/tmp/project",
        )

    command = spawn.call_args.args
    assert command[:4] == ("codex", "exec", "resume", "--json")
    assert 'sandbox_mode="read-only"' in command
    assert command[-2:] == ("thread-1", "-")
    assert result.session_id == "thread-1"
    assert result.text == "continued"


@pytest.mark.asyncio
async def test_nonzero_exit_raises_with_stderr():
    process = _process(returncode=7, stderr="authentication required")
    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        pytest.raises(RuntimeError, match="authentication required"),
    ):
        await CodexCliExecutor().run("work")


@pytest.mark.asyncio
async def test_rejects_unknown_sandbox():
    with pytest.raises(ValueError, match="Unsupported Codex sandbox"):
        await CodexCliExecutor().run("work", sandbox="yolo")


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    {"type": "turn.failed", "error": {"message": "usage limit reached"}},
    {"type": "error", "message": "usage limit reached"},
])
async def test_protocol_error_is_not_success_even_with_zero_exit(event):
    with patch("asyncio.create_subprocess_exec", return_value=_process(event)):
        with pytest.raises(RuntimeError, match="usage limit reached"):
            await CodexCliExecutor().run("work")
