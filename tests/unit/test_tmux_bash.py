"""Unit tests for TmuxBashTool.

All subprocess calls are mocked — no real tmux binary required.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.tools.tmux_bash import TmuxBashTool, BLOCKED_SUBCOMMANDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _tool() -> TmuxBashTool:
    return TmuxBashTool(cwd="/workspace")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_name():
    assert _tool().name == "InteractiveBash"


def test_description_non_empty():
    assert _tool().description.strip()


def test_description_mentions_tmux():
    assert "tmux" in _tool().description.lower()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def test_is_available_tmux_exists():
    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"):
        assert TmuxBashTool.is_available() is True


def test_is_available_tmux_missing():
    with patch("prax.tools.tmux_bash.shutil.which", return_value=None):
        assert TmuxBashTool.is_available() is False


# ---------------------------------------------------------------------------
# execute — tmux not found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_tmux_not_found():
    with patch("prax.tools.tmux_bash.shutil.which", return_value=None):
        result = await _tool().execute({"tmux_command": "new-session -d -s mydev"})
    assert result.is_error
    assert "tmux" in result.content.lower()


# ---------------------------------------------------------------------------
# execute — empty / missing command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_empty_command():
    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"):
        result = await _tool().execute({"tmux_command": "   "})
    assert result.is_error
    assert "required" in result.content.lower() or "empty" in result.content.lower()


# ---------------------------------------------------------------------------
# execute — blocked subcommands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_blocked_capture_pane():
    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"):
        result = await _tool().execute({"tmux_command": "capture-pane -p -t mydev"})
    assert result.is_error
    assert "blocked" in result.content.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("subcmd", list(BLOCKED_SUBCOMMANDS))
async def test_execute_all_blocked_subcommands(subcmd):
    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"):
        result = await _tool().execute({"tmux_command": f"{subcmd} -t sess"})
    assert result.is_error


# ---------------------------------------------------------------------------
# execute — sends command and reads output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_new_session():
    proc = _make_proc(stdout=b"", returncode=0)

    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        result = await _tool().execute({"tmux_command": "new-session -d -s mydev"})

    assert not result.is_error
    # Should have called tmux with the right subcommand tokens
    call_args = mock_exec.call_args[0]
    assert "new-session" in call_args


@pytest.mark.asyncio
async def test_execute_send_keys():
    proc = _make_proc(stdout=b"", returncode=0)

    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        result = await _tool().execute({"tmux_command": 'send-keys -t mydev "ls" Enter'})

    assert not result.is_error
    call_args = mock_exec.call_args[0]
    assert "send-keys" in call_args


@pytest.mark.asyncio
async def test_execute_reads_stdout():
    proc = _make_proc(stdout=b"session info here\n", returncode=0)

    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await _tool().execute({"tmux_command": "list-sessions"})

    assert not result.is_error
    assert "session info here" in result.content


@pytest.mark.asyncio
async def test_execute_no_output_returns_placeholder():
    proc = _make_proc(stdout=b"", returncode=0)

    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await _tool().execute({"tmux_command": "new-session -d -s s"})

    assert not result.is_error
    assert result.content  # "(no output)" or similar


@pytest.mark.asyncio
async def test_execute_nonzero_exit_is_error():
    proc = _make_proc(stdout=b"", stderr=b"session already exists", returncode=1)

    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await _tool().execute({"tmux_command": "new-session -d -s dup"})

    assert result.is_error
    assert "already exists" in result.content


@pytest.mark.asyncio
async def test_execute_timeout():
    async def _slow():
        await asyncio.sleep(10)
        return b"", b""

    proc = MagicMock()
    proc.returncode = -1
    proc.kill = MagicMock()
    proc.communicate = _slow

    with patch("prax.tools.tmux_bash.shutil.which", return_value="/usr/bin/tmux"), \
         patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await _tool().execute({"tmux_command": "new-session -d -s s"})

    assert result.is_error
    assert "timed out" in result.content.lower() or "timeout" in result.content.lower()
