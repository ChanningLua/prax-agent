from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import patch

from prax.tools.base import PermissionLevel
from prax.tools.verify_command import VerifyCommandTool


@pytest.mark.asyncio
async def test_verify_command_runs_pytest_successfully(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    tool = VerifyCommandTool(cwd=str(tmp_path))

    result = await tool.execute({"command": "pytest -q"})

    assert not result.is_error
    assert "1 passed" in result.content


@pytest.mark.asyncio
async def test_verify_command_returns_failure_output(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_nope():\n    assert False\n",
        encoding="utf-8",
    )
    tool = VerifyCommandTool(cwd=str(tmp_path))

    result = await tool.execute({"command": "pytest -q"})

    assert result.is_error
    assert "Verification failed." in result.content
    assert "failed" in result.content.lower()
    assert "Exit code:" in result.content


@pytest.mark.asyncio
async def test_verify_command_blocks_shell_composition(tmp_path: Path):
    tool = VerifyCommandTool(cwd=str(tmp_path))

    result = await tool.execute({"command": "pytest -q && echo done"})

    assert result.is_error
    assert "Shell composition" in result.content


@pytest.mark.asyncio
async def test_verify_command_blocks_non_verification_program(tmp_path: Path):
    tool = VerifyCommandTool(cwd=str(tmp_path))

    result = await tool.execute({"command": "ls -la"})

    assert result.is_error
    assert "Unsupported verification command" in result.content


@pytest.mark.asyncio
async def test_verify_command_allows_python_m_pytest(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    tool = VerifyCommandTool(cwd=str(tmp_path))

    result = await tool.execute({"command": "python3 -m pytest -q"})

    assert not result.is_error
    assert "Verification passed." in result.content
    assert "1 passed" in result.content


@pytest.mark.asyncio
async def test_verify_command_falls_back_to_python_module_pytest(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    tool = VerifyCommandTool(cwd=str(tmp_path))

    with patch("prax.tools.verify_command.shutil.which", return_value=None):
        result = await tool.execute({"command": "pytest -q"})

    assert not result.is_error
    assert "Verification passed." in result.content
    assert "1 passed" in result.content


def test_verify_command_permission_level_is_review(tmp_path: Path):
    tool = VerifyCommandTool(cwd=str(tmp_path))
    assert tool.required_permission({"command": "pytest -q"}) == PermissionLevel.REVIEW
