"""Unit tests for AstGrepSearch and AstGrepReplace tools.

All subprocess calls are mocked — no real `sg` binary required.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.tools.ast_grep import AstGrepSearchTool, AstGrepReplaceTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Return a mock asyncio Process-like object."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _search_match(file: str = "src/foo.py", line: int = 0, col: int = 0,
                  text: str = "console.log(x)") -> dict:
    return {
        "file": file,
        "range": {
            "start": {"line": line, "column": col},
            "end": {"line": line, "column": col + len(text)},
        },
        "lines": text,
        "text": text,
    }


# ---------------------------------------------------------------------------
# AstGrepSearchTool — metadata
# ---------------------------------------------------------------------------

def test_search_tool_name():
    tool = AstGrepSearchTool(cwd="/tmp")
    assert tool.name == "AstGrepSearch"


def test_search_tool_description():
    tool = AstGrepSearchTool(cwd="/tmp")
    assert "AST" in tool.description or "ast" in tool.description.lower()


# ---------------------------------------------------------------------------
# AstGrepSearchTool.is_available
# ---------------------------------------------------------------------------

def test_search_is_available_when_sg_exists():
    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"):
        assert AstGrepSearchTool.is_available() is True


def test_search_is_available_when_sg_missing():
    with patch("prax.tools.ast_grep.shutil.which", return_value=None):
        assert AstGrepSearchTool.is_available() is False


# ---------------------------------------------------------------------------
# AstGrepSearchTool.execute — success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_execute_success():
    matches = [_search_match()]
    stdout = json.dumps(matches).encode()
    proc = _make_proc(stdout=stdout)

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "console.log($MSG)", "lang": "javascript"})

    assert not result.is_error
    assert "Found 1 match" in result.content
    assert "src/foo.py" in result.content


@pytest.mark.asyncio
async def test_search_execute_no_matches():
    proc = _make_proc(stdout=b"[]")

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "def $F():", "lang": "python"})

    assert not result.is_error
    assert "No matches" in result.content


@pytest.mark.asyncio
async def test_search_execute_empty_stdout_no_stderr():
    """Empty stdout with returncode 0 → no matches."""
    proc = _make_proc(stdout=b"", returncode=0)

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "x", "lang": "python"})

    assert not result.is_error


@pytest.mark.asyncio
async def test_search_execute_sg_not_found():
    with patch("prax.tools.ast_grep.shutil.which", return_value=None):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "x", "lang": "python"})

    assert result.is_error
    assert "not found" in result.content.lower() or "ast-grep" in result.content.lower()


@pytest.mark.asyncio
async def test_search_execute_timeout():
    async def _slow_communicate():
        await asyncio.sleep(10)
        return b"", b""

    proc = MagicMock()
    proc.returncode = -1
    proc.kill = MagicMock()
    proc.communicate = _slow_communicate

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "x", "lang": "python"})

    # Timeout results in error content with returncode -1 path
    assert "Timeout" in result.content or result.is_error


@pytest.mark.asyncio
async def test_search_execute_with_stderr_error():
    proc = _make_proc(stdout=b"", stderr=b"parse error: unknown language", returncode=1)

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "x", "lang": "python"})

    assert result.is_error
    assert "parse error" in result.content


@pytest.mark.asyncio
async def test_search_execute_multiple_matches():
    matches = [_search_match(file="a.py", line=0), _search_match(file="b.py", line=5)]
    proc = _make_proc(stdout=json.dumps(matches).encode())

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepSearchTool(cwd="/workspace")
        result = await tool.execute({"pattern": "x", "lang": "python"})

    assert not result.is_error
    assert "Found 2 match" in result.content


# ---------------------------------------------------------------------------
# AstGrepReplaceTool — metadata
# ---------------------------------------------------------------------------

def test_replace_tool_name():
    tool = AstGrepReplaceTool(cwd="/tmp")
    assert tool.name == "AstGrepReplace"


def test_replace_tool_description():
    tool = AstGrepReplaceTool(cwd="/tmp")
    assert tool.description  # non-empty


# ---------------------------------------------------------------------------
# AstGrepReplaceTool.is_available
# ---------------------------------------------------------------------------

def test_replace_is_available_when_sg_exists():
    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"):
        assert AstGrepReplaceTool.is_available() is True


def test_replace_is_available_when_missing():
    with patch("prax.tools.ast_grep.shutil.which", return_value=None):
        assert AstGrepReplaceTool.is_available() is False


# ---------------------------------------------------------------------------
# AstGrepReplaceTool.execute — dry run (default)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replace_execute_dry_run_success():
    matches = [_search_match()]
    proc = _make_proc(stdout=json.dumps(matches).encode())

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepReplaceTool(cwd="/workspace")
        result = await tool.execute({
            "pattern": "console.log($MSG)",
            "rewrite": "logger.info($MSG)",
            "lang": "javascript",
        })

    assert not result.is_error
    assert "DRY RUN" in result.content
    assert "1 replacement" in result.content


@pytest.mark.asyncio
async def test_replace_execute_no_matches():
    proc = _make_proc(stdout=b"[]")

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepReplaceTool(cwd="/workspace")
        result = await tool.execute({
            "pattern": "x",
            "rewrite": "y",
            "lang": "python",
        })

    assert not result.is_error
    assert "No matches" in result.content


@pytest.mark.asyncio
async def test_replace_execute_with_stderr_failure():
    proc = _make_proc(stdout=b"", stderr=b"fatal error", returncode=1)

    with patch("prax.tools.ast_grep.shutil.which", return_value="/usr/bin/sg"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        tool = AstGrepReplaceTool(cwd="/workspace")
        result = await tool.execute({
            "pattern": "x",
            "rewrite": "y",
            "lang": "python",
        })

    assert result.is_error
    assert "fatal error" in result.content


@pytest.mark.asyncio
async def test_replace_execute_sg_not_found():
    with patch("prax.tools.ast_grep.shutil.which", return_value=None):
        tool = AstGrepReplaceTool(cwd="/workspace")
        result = await tool.execute({
            "pattern": "x",
            "rewrite": "y",
            "lang": "python",
        })

    assert result.is_error
