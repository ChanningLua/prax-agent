"""Tests for Batch 1 tool extensions: Glob, Grep, Git."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from prax.tools.glob_tool import GlobTool
from prax.tools.grep_tool import GrepTool
from prax.tools.git_tool import GitTool
from prax.tools.base import PermissionLevel


class TestGlobTool:
    """Tests for GlobTool."""

    @pytest.mark.asyncio
    async def test_glob_finds_files(self):
        """Test that glob finds matching files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            (Path(tmpdir) / "test1.py").write_text("content")
            (Path(tmpdir) / "test2.py").write_text("content")
            (Path(tmpdir) / "test.txt").write_text("content")

            tool = GlobTool(cwd=tmpdir)
            result = await tool.execute({"pattern": "*.py", "path": tmpdir})

            assert not result.is_error
            assert "test1.py" in result.content
            assert "test2.py" in result.content
            assert "test.txt" not in result.content

    @pytest.mark.asyncio
    async def test_glob_no_matches(self):
        """Test glob with no matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = GlobTool(cwd=tmpdir)
            result = await tool.execute({"pattern": "*.nonexistent", "path": tmpdir})

            assert not result.is_error
            assert "No files matched" in result.content

    @pytest.mark.asyncio
    async def test_glob_requires_absolute_path(self):
        """Test that glob requires absolute paths."""
        tool = GlobTool()
        result = await tool.execute({"pattern": "*.py", "path": "relative/path"})

        assert result.is_error
        assert "must be absolute" in result.content

    @pytest.mark.asyncio
    async def test_glob_missing_pattern(self):
        """Test glob with missing pattern parameter."""
        tool = GlobTool()
        result = await tool.execute({})

        assert result.is_error
        assert "pattern" in result.content


class TestGrepTool:
    """Tests for GrepTool."""

    @pytest.mark.asyncio
    async def test_grep_finds_matches(self):
        """Test that grep finds matching content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            (Path(tmpdir) / "test1.py").write_text("def hello():\n    pass\n")
            (Path(tmpdir) / "test2.py").write_text("def world():\n    pass\n")

            tool = GrepTool(cwd=tmpdir)
            result = await tool.execute({"pattern": "def hello", "path": tmpdir})

            assert not result.is_error
            assert "test1.py" in result.content
            assert ":1:" in result.content  # line number
            assert "def hello" in result.content

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self):
        """Test case insensitive search."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.txt").write_text("Hello World\n")

            tool = GrepTool(cwd=tmpdir)
            result = await tool.execute({
                "pattern": "hello",
                "path": tmpdir,
                "case_insensitive": True
            })

            assert not result.is_error
            assert "Hello World" in result.content

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self):
        """Test grep with invalid regex pattern."""
        tool = GrepTool()
        result = await tool.execute({"pattern": "[invalid"})

        assert result.is_error
        assert "invalid regex" in result.content

    @pytest.mark.asyncio
    async def test_grep_no_matches(self):
        """Test grep with no matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.txt").write_text("content\n")

            tool = GrepTool(cwd=tmpdir)
            result = await tool.execute({"pattern": "nonexistent", "path": tmpdir})

            assert not result.is_error
            assert "No matches found" in result.content


class TestGitTool:
    """Tests for GitTool."""

    @pytest.mark.asyncio
    async def test_git_status_success(self):
        """Test git status command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            process = await asyncio.create_subprocess_exec(
                "git", "init",
                cwd=tmpdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            tool = GitTool(cwd=tmpdir)
            result = await tool.execute({"operation": "status"})

            assert not result.is_error

    @pytest.mark.asyncio
    async def test_git_dangerous_flag_blocked(self):
        """Test that dangerous flags are blocked for read operations."""
        tool = GitTool()
        result = await tool.execute({
            "operation": "status",
            "args": ["--force"]
        })

        assert result.is_error
        assert "dangerous flag" in result.content

    @pytest.mark.asyncio
    async def test_git_invalid_operation(self):
        """Test invalid git operation."""
        tool = GitTool()
        result = await tool.execute({"operation": "invalid"})

        assert result.is_error
        assert "invalid operation" in result.content

    @pytest.mark.asyncio
    async def test_git_write_operation_permission(self):
        """Test that write operations require REVIEW permission."""
        tool = GitTool()
        perm = tool.required_permission({"operation": "commit"})
        assert perm == PermissionLevel.REVIEW

    @pytest.mark.asyncio
    async def test_git_read_operation_permission(self):
        """Test that read operations require SAFE permission."""
        tool = GitTool()
        perm = tool.required_permission({"operation": "status"})
        assert perm == PermissionLevel.SAFE
