"""Unit tests for SandboxBashTool.

All sandbox I/O is mocked — no real shell execution.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prax.core.sandbox.base import SandboxResult
from prax.tools.base import PermissionLevel
from prax.tools.sandbox_bash import SandboxBashTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sandbox(output: str = "", exit_code: int = 0, timed_out: bool = False):
    """Return a mock Sandbox whose execute_command_v2 returns a SandboxResult."""
    sandbox = MagicMock()
    sandbox.execute_command_v2 = MagicMock(
        return_value=SandboxResult(output=output, exit_code=exit_code, timed_out=timed_out)
    )
    return sandbox


def _make_provider(sandbox):
    """Return a mock SandboxProvider that yields the given sandbox."""
    provider = MagicMock()
    provider.acquire = MagicMock(return_value="sid-1")
    provider.get = MagicMock(return_value=sandbox)
    provider.release = MagicMock()
    return provider


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_sandbox_bash_name():
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=_make_provider(_make_sandbox())):
        tool = SandboxBashTool(cwd="/workspace")
    assert tool.name == "SandboxBash"


def test_sandbox_bash_description_non_empty():
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=_make_provider(_make_sandbox())):
        tool = SandboxBashTool(cwd="/workspace")
    assert tool.description.strip()


# ---------------------------------------------------------------------------
# execute — command validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_empty_command_is_error():
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=_make_provider(_make_sandbox())):
        tool = SandboxBashTool()
    result = await tool.execute({"command": "   "})
    assert result.is_error
    assert "command" in result.content.lower()


# ---------------------------------------------------------------------------
# execute — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_success():
    sandbox = _make_sandbox(output="hello world", exit_code=0)
    provider = _make_provider(sandbox)
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=provider):
        tool = SandboxBashTool(cwd="/workspace")
        result = await tool.execute({"command": "echo hello world"})
    assert not result.is_error
    assert "hello world" in result.content


# ---------------------------------------------------------------------------
# execute — non-zero exit code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_nonzero_exit_is_error():
    sandbox = _make_sandbox(output="command not found", exit_code=127)
    provider = _make_provider(sandbox)
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=provider):
        tool = SandboxBashTool(cwd="/workspace")
        result = await tool.execute({"command": "notacommand"})
    assert result.is_error
    assert "127" in result.content or "not found" in result.content


# ---------------------------------------------------------------------------
# execute — timed out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_timeout_is_error():
    sandbox = _make_sandbox(output="", exit_code=-1, timed_out=True)
    provider = _make_provider(sandbox)
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=provider):
        tool = SandboxBashTool(cwd="/workspace")
        result = await tool.execute({"command": "sleep 999"})
    assert result.is_error


def test_safe_verify_command_downgrades_permission():
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=_make_provider(_make_sandbox())):
        tool = SandboxBashTool(cwd="/workspace")
    assert tool.required_permission({"command": "pytest -q"}) == PermissionLevel.REVIEW


@pytest.mark.asyncio
async def test_safe_verify_command_delegates_to_verify_tool(tmp_path):
    provider = _make_provider(_make_sandbox())
    with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=provider):
        tool = SandboxBashTool(cwd=str(tmp_path))

    with patch("prax.tools.sandbox_bash.VerifyCommandTool.execute", return_value=MagicMock(content="Verification passed.", is_error=False)) as mock_execute:
        result = await tool.execute({"command": "pytest -q", "timeout": 30})

    assert not result.is_error
    mock_execute.assert_called_once()
