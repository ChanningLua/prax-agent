"""Unit tests for HashlineRead and HashlineEdit tools.

All tests use tmp_path for real file operations — no network, no external I/O.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from prax.tools.hashline_read import (
    HashlineReadTool,
    compute_line_hash,
    format_hashline,
)
from prax.tools.hashline_edit import HashlineEditTool, validate_hashline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(tmp_path, name: str, content: str):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _line_ref(line_num: int, content: str) -> str:
    """Build a valid '42#A7' reference for use in edits."""
    h = compute_line_hash(line_num, content)
    return f"{line_num}#{h}"


# ---------------------------------------------------------------------------
# HashlineReadTool — metadata
# ---------------------------------------------------------------------------

def test_hashline_read_name():
    tool = HashlineReadTool()
    assert tool.name == "HashlineRead"


def test_hashline_read_description():
    tool = HashlineReadTool()
    assert "HashlineRead" in tool.description or "hash" in tool.description.lower()


def test_hashline_read_is_available():
    # HashlineRead has no is_available, it's always usable
    tool = HashlineReadTool()
    assert tool.name == "HashlineRead"


# ---------------------------------------------------------------------------
# HashlineReadTool — execute: basic read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hashline_read_basic(tmp_path):
    path = _make_file(tmp_path, "f.txt", "alpha\nbeta\ngamma\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path})
    assert not result.is_error
    lines = result.content.splitlines()
    assert len(lines) == 3
    # Each line must follow format linenum#HASH|content
    assert lines[0].startswith("1#")
    assert "|alpha" in lines[0]
    assert lines[1].startswith("2#")
    assert "|beta" in lines[1]
    assert lines[2].startswith("3#")
    assert "|gamma" in lines[2]


@pytest.mark.asyncio
async def test_hashline_read_with_start_line(tmp_path):
    path = _make_file(tmp_path, "f.txt", "line1\nline2\nline3\nline4\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path, "start_line": 2})
    assert not result.is_error
    lines = result.content.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("2#")
    assert "|line2" in lines[0]


@pytest.mark.asyncio
async def test_hashline_read_with_end_line(tmp_path):
    path = _make_file(tmp_path, "f.txt", "a\nb\nc\nd\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path, "end_line": 2})
    assert not result.is_error
    lines = result.content.splitlines()
    assert len(lines) == 2
    assert lines[-1].startswith("2#")


@pytest.mark.asyncio
async def test_hashline_read_with_offset_and_limit(tmp_path):
    path = _make_file(tmp_path, "f.txt", "a\nb\nc\nd\ne\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path, "start_line": 2, "end_line": 4})
    assert not result.is_error
    lines = result.content.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("2#")
    assert lines[-1].startswith("4#")


@pytest.mark.asyncio
async def test_hashline_read_missing_file(tmp_path):
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": str(tmp_path / "no_such.txt")})
    assert result.is_error
    assert "not found" in result.content.lower() or "Error" in result.content


@pytest.mark.asyncio
async def test_hashline_read_invalid_file_path_type():
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": 123})
    assert result.is_error
    assert "file_path" in result.content


@pytest.mark.asyncio
async def test_hashline_read_start_line_beyond_eof(tmp_path):
    path = _make_file(tmp_path, "f.txt", "only one line\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path, "start_line": 99})
    assert result.is_error


@pytest.mark.asyncio
async def test_hashline_read_end_line_less_than_start(tmp_path):
    path = _make_file(tmp_path, "f.txt", "a\nb\nc\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path, "start_line": 3, "end_line": 1})
    assert result.is_error


@pytest.mark.asyncio
async def test_hashline_read_hash_format_correctness(tmp_path):
    content = "hello world"
    path = _make_file(tmp_path, "f.txt", content + "\n")
    tool = HashlineReadTool()
    result = await tool.execute({"file_path": path})
    assert not result.is_error
    # The hash must match what compute_line_hash produces
    expected_h = compute_line_hash(1, content)
    assert f"1#{expected_h}|{content}" == result.content.strip()


# ---------------------------------------------------------------------------
# HashlineEditTool — metadata
# ---------------------------------------------------------------------------

def test_hashline_edit_name():
    tool = HashlineEditTool()
    assert tool.name == "HashlineEdit"


def test_hashline_edit_description():
    tool = HashlineEditTool()
    assert tool.description  # non-empty


# ---------------------------------------------------------------------------
# HashlineEditTool — execute: replace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hashline_edit_replace_line(tmp_path):
    lines = ["alpha", "beta", "gamma"]
    path = _make_file(tmp_path, "f.txt", "\n".join(lines))
    tool = HashlineEditTool()
    ref = _line_ref(2, "beta")
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "replace", "line_ref": ref, "content": "REPLACED"}],
    })
    assert not result.is_error, result.content
    written = (tmp_path / "f.txt").read_text(encoding="utf-8")
    assert "REPLACED" in written
    assert "beta" not in written


@pytest.mark.asyncio
async def test_hashline_edit_hash_mismatch(tmp_path):
    path = _make_file(tmp_path, "f.txt", "alpha\nbeta\n")
    tool = HashlineEditTool()
    # Provide a deliberately wrong hash
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "replace", "line_ref": "1#ZZ", "content": "new"}],
    })
    assert result.is_error
    assert "mismatch" in result.content.lower() or "invalid" in result.content.lower()


@pytest.mark.asyncio
async def test_hashline_edit_missing_file(tmp_path):
    tool = HashlineEditTool()
    result = await tool.execute({
        "file_path": str(tmp_path / "ghost.txt"),
        "edits": [{"op": "replace", "line_ref": "1#AB", "content": "x"}],
    })
    assert result.is_error
    assert "not found" in result.content.lower() or "Error" in result.content


@pytest.mark.asyncio
async def test_hashline_edit_empty_edits():
    tool = HashlineEditTool()
    result = await tool.execute({
        "file_path": "/tmp/irrelevant.txt",
        "edits": [],
    })
    assert result.is_error
    assert "edits" in result.content.lower()


@pytest.mark.asyncio
async def test_hashline_edit_insert_after(tmp_path):
    lines = ["first", "second"]
    path = _make_file(tmp_path, "f.txt", "\n".join(lines))
    tool = HashlineEditTool()
    ref = _line_ref(1, "first")
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "insert_after", "line_ref": ref, "content": "INSERTED"}],
    })
    assert not result.is_error, result.content
    written_lines = (tmp_path / "f.txt").read_text(encoding="utf-8").splitlines()
    assert written_lines == ["first", "INSERTED", "second"]


@pytest.mark.asyncio
async def test_hashline_edit_insert_before(tmp_path):
    lines = ["first", "second"]
    path = _make_file(tmp_path, "f.txt", "\n".join(lines))
    tool = HashlineEditTool()
    ref = _line_ref(2, "second")
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "insert_before", "line_ref": ref, "content": "BEFORE"}],
    })
    assert not result.is_error, result.content
    written_lines = (tmp_path / "f.txt").read_text(encoding="utf-8").splitlines()
    assert written_lines == ["first", "BEFORE", "second"]


@pytest.mark.asyncio
async def test_hashline_edit_delete(tmp_path):
    lines = ["keep", "remove", "keep2"]
    path = _make_file(tmp_path, "f.txt", "\n".join(lines))
    tool = HashlineEditTool()
    ref = _line_ref(2, "remove")
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "delete", "line_ref": ref}],
    })
    assert not result.is_error, result.content
    written_lines = (tmp_path / "f.txt").read_text(encoding="utf-8").splitlines()
    assert "remove" not in written_lines
    assert written_lines == ["keep", "keep2"]


@pytest.mark.asyncio
async def test_hashline_edit_invalid_line_ref_format(tmp_path):
    path = _make_file(tmp_path, "f.txt", "hello\n")
    tool = HashlineEditTool()
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "replace", "line_ref": "bad-format", "content": "x"}],
    })
    assert result.is_error
    assert "invalid" in result.content.lower() or "line_ref" in result.content


@pytest.mark.asyncio
async def test_hashline_edit_line_out_of_range(tmp_path):
    path = _make_file(tmp_path, "f.txt", "one line\n")
    tool = HashlineEditTool()
    # Line 99 doesn't exist; hash won't matter since range check comes first
    result = await tool.execute({
        "file_path": path,
        "edits": [{"op": "replace", "line_ref": "99#AB", "content": "x"}],
    })
    assert result.is_error
    assert "out of range" in result.content.lower() or "range" in result.content.lower()
