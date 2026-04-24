"""Unit tests for remaining tool coverage gaps.

All tools are exercised via their execute() coroutines.
No real I/O, subprocess calls, or network calls are made.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ── helpers ──────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── prax/tools/base.py  lines 77, 81 ─────────────────────────────────────────

from prax.tools.base import PermissionLevel, Tool, ToolCall, ToolFileAccess, ToolInputValidationError, ToolResult


class _MinimalTool(Tool):
    name = "Minimal"
    description = "test"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, params):
        return ToolResult(content="ok")


class _RequiredFieldTool(Tool):
    name = "RequiredField"
    description = "requires name"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    async def execute(self, params):
        return ToolResult(content=str(params["name"]))


class TestToolBase:
    def test_required_permission_returns_class_level(self) -> None:
        t = _MinimalTool()
        assert t.required_permission({}) == PermissionLevel.SAFE

    def test_file_accesses_returns_empty(self) -> None:
        t = _MinimalTool()
        assert t.file_accesses({}) == []

    def test_to_claude_format(self) -> None:
        t = _MinimalTool()
        fmt = t.to_claude_format()
        assert fmt["name"] == "Minimal"
        assert "description" in fmt
        assert "input_schema" in fmt

    def test_to_openai_format(self) -> None:
        t = _MinimalTool()
        fmt = t.to_openai_format()
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "Minimal"

    def test_validate_params_accepts_valid_input(self) -> None:
        tool = _RequiredFieldTool()
        tool.validate_params({"name": "prax"})

    def test_validate_params_rejects_missing_required_field(self) -> None:
        tool = _RequiredFieldTool()
        with pytest.raises(ToolInputValidationError, match="Invalid input for RequiredField"):
            tool.validate_params({})


# ── prax/tools/edit.py  lines 30, 40 ─────────────────────────────────────────

from prax.tools.edit import EditTool


class TestEditTool:
    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        tool = EditTool()
        result = await tool.execute({
            "file_path": str(tmp_path / "missing.txt"),
            "old_string": "x",
            "new_string": "y",
        })
        assert result.is_error
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_old_string_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        tool = EditTool()
        result = await tool.execute({
            "file_path": str(f),
            "old_string": "NOTHERE",
            "new_string": "x",
        })
        assert result.is_error
        assert "not found" in result.content

    @pytest.mark.asyncio
    async def test_old_string_appears_multiple_times(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("aa aa aa")
        tool = EditTool()
        result = await tool.execute({
            "file_path": str(f),
            "old_string": "aa",
            "new_string": "bb",
        })
        assert result.is_error
        assert "multiple" in result.content

    @pytest.mark.asyncio
    async def test_checksum_mismatch(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("content")
        tool = EditTool()
        result = await tool.execute({
            "file_path": str(f),
            "old_string": "content",
            "new_string": "new",
            "expected_sha256": "deadbeef" * 8,
        })
        assert result.is_error
        assert "checksum" in result.content

    @pytest.mark.asyncio
    async def test_successful_edit(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        tool = EditTool()
        result = await tool.execute({
            "file_path": str(f),
            "old_string": "world",
            "new_string": "prax",
        })
        assert not result.is_error
        assert f.read_text() == "hello prax"


# ── prax/tools/read.py  lines 34-35 ──────────────────────────────────────────

from prax.tools.read import ReadTool


class TestReadTool:
    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        tool = ReadTool()
        result = await tool.execute({"file_path": str(tmp_path / "nope.txt")})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_oserror_returned(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("data")
        tool = ReadTool()
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = await tool.execute({"file_path": str(f)})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_reads_with_offset_and_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\nline3\nline4\n")
        tool = ReadTool()
        result = await tool.execute({"file_path": str(f), "offset": 2, "limit": 2})
        assert not result.is_error
        assert "line2" in result.content
        assert "line3" in result.content
        assert "line4" not in result.content


# ── prax/tools/write.py  lines 29-30 ─────────────────────────────────────────

from prax.tools.write import WriteTool


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_creates_file(self, tmp_path: Path) -> None:
        tool = WriteTool()
        fp = tmp_path / "new.txt"
        result = await tool.execute({"file_path": str(fp), "content": "hello"})
        assert not result.is_error
        assert fp.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_oserror_is_returned(self, tmp_path: Path) -> None:
        tool = WriteTool()
        with patch.object(Path, "write_text", side_effect=OSError("read-only")):
            result = await tool.execute({
                "file_path": str(tmp_path / "f.txt"),
                "content": "x",
            })
        assert result.is_error


# ── prax/tools/hashline_read.py ───────────────────────────────────────────────

from prax.tools.hashline_read import HashlineReadTool, compute_line_hash, format_hashline


class TestHashlineReadTool:
    @pytest.mark.asyncio
    async def test_non_string_file_path(self) -> None:
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": 42})
        assert result.is_error
        assert "file_path must be a string" in result.content

    @pytest.mark.asyncio
    async def test_invalid_start_line(self) -> None:
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": "/tmp/x.txt", "start_line": 0})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_end_line_less_than_start(self) -> None:
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": "/tmp/x.txt", "start_line": 5, "end_line": 3})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_missing_file(self, tmp_path: Path) -> None:
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": str(tmp_path / "nope.txt")})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_path_is_directory(self, tmp_path: Path) -> None:
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": str(tmp_path)})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_start_line_beyond_file(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("one\ntwo\n")
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": str(f), "start_line": 100})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_reads_with_range(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\ngamma\n")
        tool = HashlineReadTool()
        result = await tool.execute({"file_path": str(f), "start_line": 2, "end_line": 2})
        assert not result.is_error
        assert "beta" in result.content

    def test_compute_line_hash_empty_line(self) -> None:
        h = compute_line_hash(5, "")
        assert len(h) == 2
        assert h.isupper() or h.isdigit() or all(c in "0123456789ABCDEF" for c in h)

    def test_format_hashline(self) -> None:
        line = format_hashline(1, "hello")
        assert line.startswith("1#")
        assert "|hello" in line


# ── prax/tools/hashline_edit.py ──────────────────────────────────────────────

from prax.tools.hashline_edit import HashlineEditTool, parse_hashline, validate_hashline


class TestHashlineEditTool:
    def _make_file(self, tmp_path: Path, content: str = "line one\nline two\nline three") -> Path:
        f = tmp_path / "edit_me.txt"
        f.write_text(content)
        return f

    @pytest.mark.asyncio
    async def test_non_string_file_path(self) -> None:
        tool = HashlineEditTool()
        result = await tool.execute({"file_path": 99, "edits": []})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_edits(self, tmp_path: Path) -> None:
        f = self._make_file(tmp_path)
        tool = HashlineEditTool()
        result = await tool.execute({"file_path": str(f), "edits": []})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        tool = HashlineEditTool()
        result = await tool.execute({
            "file_path": str(tmp_path / "nope.txt"),
            "edits": [{"op": "replace", "line_ref": "1#AA", "content": "new"}],
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_invalid_line_ref_format(self, tmp_path: Path) -> None:
        f = self._make_file(tmp_path)
        tool = HashlineEditTool()
        result = await tool.execute({
            "file_path": str(f),
            "edits": [{"op": "replace", "line_ref": "bad-ref", "content": "x"}],
        })
        assert result.is_error
        assert "invalid line_ref" in result.content

    @pytest.mark.asyncio
    async def test_line_out_of_range(self, tmp_path: Path) -> None:
        f = self._make_file(tmp_path)
        tool = HashlineEditTool()
        result = await tool.execute({
            "file_path": str(f),
            "edits": [{"op": "replace", "line_ref": "999#AA", "content": "x"}],
        })
        assert result.is_error
        assert "out of range" in result.content

    @pytest.mark.asyncio
    async def test_replace_op(self, tmp_path: Path) -> None:
        f = self._make_file(tmp_path, "original\n")
        from prax.tools.hashline_read import compute_line_hash
        h = compute_line_hash(1, "original")
        tool = HashlineEditTool()
        result = await tool.execute({
            "file_path": str(f),
            "edits": [{"op": "replace", "line_ref": f"1#{h}", "content": "replaced"}],
        })
        assert not result.is_error
        assert "replaced" in f.read_text()

    @pytest.mark.asyncio
    async def test_delete_op(self, tmp_path: Path) -> None:
        f = self._make_file(tmp_path, "keep\ndelete_me\nkeep2")
        from prax.tools.hashline_read import compute_line_hash
        h = compute_line_hash(2, "delete_me")
        tool = HashlineEditTool()
        result = await tool.execute({
            "file_path": str(f),
            "edits": [{"op": "delete", "line_ref": f"2#{h}"}],
        })
        assert not result.is_error
        assert "delete_me" not in f.read_text()

    @pytest.mark.asyncio
    async def test_unknown_op(self, tmp_path: Path) -> None:
        f = self._make_file(tmp_path, "data")
        from prax.tools.hashline_read import compute_line_hash
        h = compute_line_hash(1, "data")
        tool = HashlineEditTool()
        result = await tool.execute({
            "file_path": str(f),
            "edits": [{"op": "teleport", "line_ref": f"1#{h}", "content": "x"}],
        })
        assert result.is_error

    def test_parse_hashline_valid(self) -> None:
        parsed = parse_hashline("42#AB|some content")
        assert parsed == (42, "AB", "some content")

    def test_parse_hashline_invalid(self) -> None:
        assert parse_hashline("not-a-hashline") is None

    def test_validate_hashline(self) -> None:
        from prax.tools.hashline_read import compute_line_hash
        h = compute_line_hash(3, "hello")
        assert validate_hashline(3, "hello", h) is True
        assert validate_hashline(3, "hello", "ZZ") is False


# ── prax/tools/web_search.py ──────────────────────────────────────────────────

from prax.tools.web_search import WebSearchTool, WebCrawlerTool


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_no_httpx_returns_error(self) -> None:
        import prax.tools.web_search as ws_mod
        original = ws_mod._HTTPX_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = False
            tool = WebSearchTool(api_key="key")
            result = await tool.execute({"query": "test"})
            assert result.is_error
            assert "httpx" in result.content
        finally:
            ws_mod._HTTPX_AVAILABLE = original

    @pytest.mark.asyncio
    async def test_no_api_key_returns_error(self) -> None:
        import prax.tools.web_search as ws_mod
        original = ws_mod._HTTPX_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = True
            tool = WebSearchTool(api_key=None)
            with patch("os.getenv", return_value=None):
                result = await tool.execute({"query": "test"})
            assert result.is_error
            assert "TAVILY_API_KEY" in result.content
        finally:
            ws_mod._HTTPX_AVAILABLE = original

    @pytest.mark.asyncio
    async def test_is_available_false_without_key(self) -> None:
        with patch("os.getenv", return_value=None):
            assert WebSearchTool.is_available() is False

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        import prax.tools.web_search as ws_mod
        original = ws_mod._HTTPX_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = True
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "answer": "42",
                "results": [{"title": "Source", "url": "http://x.com", "content": "snippet"}],
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            with patch("httpx.AsyncClient", return_value=mock_client):
                tool = WebSearchTool(api_key="test-key")
                result = await tool.execute({"query": "meaning of life", "max_results": 1})
            assert not result.is_error
            assert "42" in result.content
        finally:
            ws_mod._HTTPX_AVAILABLE = original

    @pytest.mark.asyncio
    async def test_search_exception_returns_error(self) -> None:
        import prax.tools.web_search as ws_mod
        original = ws_mod._HTTPX_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = True
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("network error"))
            mock_client.__aexit__ = AsyncMock(return_value=False)
            with patch("httpx.AsyncClient", return_value=mock_client):
                tool = WebSearchTool(api_key="key")
                result = await tool.execute({"query": "test"})
            assert result.is_error
        finally:
            ws_mod._HTTPX_AVAILABLE = original


class TestWebCrawlerTool:
    @pytest.mark.asyncio
    async def test_no_httpx_returns_error(self) -> None:
        import prax.tools.web_search as ws_mod
        orig_h, orig_b = ws_mod._HTTPX_AVAILABLE, ws_mod._BS4_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = False
            ws_mod._BS4_AVAILABLE = True
            tool = WebCrawlerTool()
            result = await tool.execute({"url": "http://example.com"})
            assert result.is_error
        finally:
            ws_mod._HTTPX_AVAILABLE = orig_h
            ws_mod._BS4_AVAILABLE = orig_b

    @pytest.mark.asyncio
    async def test_no_bs4_returns_error(self) -> None:
        import prax.tools.web_search as ws_mod
        orig_h, orig_b = ws_mod._HTTPX_AVAILABLE, ws_mod._BS4_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = True
            ws_mod._BS4_AVAILABLE = False
            tool = WebCrawlerTool()
            result = await tool.execute({"url": "http://example.com"})
            assert result.is_error
        finally:
            ws_mod._HTTPX_AVAILABLE = orig_h
            ws_mod._BS4_AVAILABLE = orig_b

    def test_is_available_requires_both(self) -> None:
        import prax.tools.web_search as ws_mod
        orig_h, orig_b = ws_mod._HTTPX_AVAILABLE, ws_mod._BS4_AVAILABLE
        try:
            ws_mod._HTTPX_AVAILABLE = True
            ws_mod._BS4_AVAILABLE = False
            assert WebCrawlerTool.is_available() is False
        finally:
            ws_mod._HTTPX_AVAILABLE = orig_h
            ws_mod._BS4_AVAILABLE = orig_b


# ── prax/tools/background_task.py ────────────────────────────────────────────

from prax.tools.background_task import (
    CancelTaskTool,
    CheckTaskTool,
    ListTasksTool,
    StartTaskTool,
    UpdateTaskTool,
)
from prax.core.background_store import BackgroundTask, BackgroundTaskStore
from datetime import datetime, timezone


def _make_store(tmp_path: Path) -> BackgroundTaskStore:
    return BackgroundTaskStore(str(tmp_path))


def _make_task(task_id: str = "task_abc", status: str = "running") -> BackgroundTask:
    return BackgroundTask(
        task_id=task_id,
        description="Test task",
        prompt="do stuff",
        subagent_type="general-purpose",
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class TestStartTaskTool:
    # Never let these tests actually fork a background runner subprocess.
    @pytest.fixture(autouse=True)
    def _no_spawn(self):
        from unittest.mock import patch as _patch
        with _patch(
            "prax.tools.background_task._spawn_background_runner",
            return_value=99999,
        ):
            yield

    @pytest.mark.asyncio
    async def test_empty_description_is_error(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=AsyncMock(return_value="done"))
        result = await tool.execute({"description": "   ", "prompt": "ok"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_prompt_is_error(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=AsyncMock(return_value="done"))
        result = await tool.execute({"description": "test", "prompt": ""})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_starts_task_returns_task_id(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=AsyncMock(return_value="done"))
        result = await tool.execute({"description": "desc", "prompt": "do something"})
        assert not result.is_error
        payload = json.loads(result.content)
        assert "task_id" in payload
        assert payload["status"] == "running"


class TestCheckTaskTool:
    @pytest.mark.asyncio
    async def test_missing_task_id(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = CheckTaskTool(store=store)
        result = await tool.execute({"task_id": ""})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = CheckTaskTool(store=store)
        result = await tool.execute({"task_id": "task_ghost"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_known_task_returns_status(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task = _make_task()
        store.create(task)
        tool = CheckTaskTool(store=store)
        result = await tool.execute({"task_id": task.task_id})
        payload = json.loads(result.content)
        assert payload["status"] == "running"


class TestUpdateTaskTool:
    @pytest.mark.asyncio
    async def test_empty_task_id(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = UpdateTaskTool(store=store)
        result = await tool.execute({"task_id": "", "message": "hi"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_message(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = UpdateTaskTool(store=store)
        result = await tool.execute({"task_id": "task_abc", "message": ""})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_update_nonrunning_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task = _make_task(status="success")
        store.create(task)
        tool = UpdateTaskTool(store=store)
        result = await tool.execute({"task_id": task.task_id, "message": "hi"})
        payload = json.loads(result.content)
        assert payload["updated"] is False

    @pytest.mark.asyncio
    async def test_update_running_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task = _make_task(status="running")
        store.create(task)
        tool = UpdateTaskTool(store=store)
        result = await tool.execute({"task_id": task.task_id, "message": "extra context"})
        payload = json.loads(result.content)
        assert payload["updated"] is True


class TestCancelTaskTool:
    @pytest.mark.asyncio
    async def test_empty_task_id(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = CancelTaskTool(store=store)
        result = await tool.execute({"task_id": ""})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tool = CancelTaskTool(store=store)
        result = await tool.execute({"task_id": "ghost"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_cancel_non_running_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task = _make_task(status="success")
        store.create(task)
        tool = CancelTaskTool(store=store)
        result = await tool.execute({"task_id": task.task_id})
        payload = json.loads(result.content)
        assert payload["cancelled"] is False

    @pytest.mark.asyncio
    async def test_cancel_running_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task = _make_task(status="running")
        store.create(task)
        tool = CancelTaskTool(store=store)
        result = await tool.execute({"task_id": task.task_id})
        payload = json.loads(result.content)
        assert payload["cancelled"] is True


class TestListTasksTool:
    @pytest.mark.asyncio
    async def test_lists_all_tasks(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.create(_make_task("task_a1", "running"))
        store.create(_make_task("task_a2", "success"))
        tool = ListTasksTool(store=store)
        result = await tool.execute({})
        payload = json.loads(result.content)
        assert len(payload) == 2

    @pytest.mark.asyncio
    async def test_filters_by_status(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.create(_make_task("task_b1", "running"))
        store.create(_make_task("task_b2", "success"))
        tool = ListTasksTool(store=store)
        result = await tool.execute({"status": "running"})
        payload = json.loads(result.content)
        assert all(t["status"] == "running" for t in payload)


# ── prax/tools/ast_grep.py ────────────────────────────────────────────────────

from prax.tools.ast_grep import (
    AstGrepReplaceTool,
    AstGrepSearchTool,
    _format_replace,
    _format_search,
    _parse_output,
)


class TestAstGrepParseOutput:
    def test_empty_stdout(self) -> None:
        result = _parse_output("")
        assert result["totalMatches"] == 0
        assert result["matches"] == []

    def test_valid_json(self) -> None:
        matches = [{"file": "a.py", "range": {"start": {"line": 0, "column": 0}}, "lines": "x", "text": "x"}]
        result = _parse_output(json.dumps(matches))
        assert result["totalMatches"] == 1

    def test_invalid_json_non_truncated(self) -> None:
        result = _parse_output("not json at all")
        assert result["matches"] == []
        assert result["truncated"] is False


class TestFormatSearch:
    def test_no_matches(self) -> None:
        result = _format_search({"matches": [], "totalMatches": 0, "truncated": False})
        assert "No matches" in result

    def test_with_error(self) -> None:
        result = _format_search({"error": "boom", "matches": [], "totalMatches": 0})
        assert "Error: boom" in result

    def test_with_matches(self) -> None:
        matches = [{
            "file": "foo.py",
            "range": {"start": {"line": 4, "column": 2}},
            "lines": "console.log('hi')",
            "text": "t",
        }]
        result = _format_search({"matches": matches, "totalMatches": 1, "truncated": False})
        assert "foo.py:5:3" in result

    def test_truncated_notice(self) -> None:
        matches = [{"file": "f.py", "range": {"start": {"line": 0, "column": 0}}, "lines": "x", "text": "x"}]
        result = _format_search({"matches": matches, "totalMatches": 999, "truncated": True})
        assert "TRUNCATED" in result


class TestFormatReplace:
    def test_no_matches(self) -> None:
        result = _format_replace({"matches": [], "totalMatches": 0, "truncated": False}, dry_run=True)
        assert "No matches" in result

    def test_dry_run_prefix(self) -> None:
        matches = [{"file": "a.py", "range": {"start": {"line": 0}}, "text": "old"}]
        result = _format_replace({"matches": matches, "totalMatches": 1, "truncated": False}, dry_run=True)
        assert "DRY RUN" in result

    def test_non_dry_run(self) -> None:
        matches = [{"file": "a.py", "range": {"start": {"line": 0}}, "text": "old"}]
        result = _format_replace({"matches": matches, "totalMatches": 1, "truncated": False}, dry_run=False)
        assert "DRY RUN" not in result


class TestAstGrepSearchTool:
    @pytest.mark.asyncio
    async def test_sg_not_found(self) -> None:
        with patch("shutil.which", return_value=None):
            tool = AstGrepSearchTool(cwd="/tmp")
            result = await tool.execute({"pattern": "x", "lang": "python"})
        assert result.is_error
        assert "ast-grep" in result.content

    @pytest.mark.asyncio
    async def test_no_files_found_stderr(self) -> None:
        async def _fake_run(args, cwd):
            return ("", "No files found matching", 1)

        with patch("shutil.which", return_value="/usr/bin/sg"):
            with patch("prax.tools.ast_grep._run_sg", _fake_run):
                tool = AstGrepSearchTool(cwd="/tmp")
                result = await tool.execute({"pattern": "x", "lang": "python"})
        assert "No matches" in result.content

    @pytest.mark.asyncio
    async def test_stderr_error_returned(self) -> None:
        async def _fake_run(args, cwd):
            return ("", "parse error", 1)

        with patch("shutil.which", return_value="/usr/bin/sg"):
            with patch("prax.tools.ast_grep._run_sg", _fake_run):
                tool = AstGrepSearchTool(cwd="/tmp")
                result = await tool.execute({"pattern": "x", "lang": "python"})
        assert result.is_error


class TestAstGrepReplaceTool:
    @pytest.mark.asyncio
    async def test_sg_not_found(self) -> None:
        with patch("shutil.which", return_value=None):
            tool = AstGrepReplaceTool(cwd="/tmp")
            result = await tool.execute({"pattern": "x", "rewrite": "y", "lang": "python"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_dry_run_default(self) -> None:
        matches = [{"file": "f.py", "range": {"start": {"line": 0}}, "text": "x"}]

        async def _fake_run(args, cwd):
            return (json.dumps(matches), "", 0)

        with patch("shutil.which", return_value="/usr/bin/sg"):
            with patch("prax.tools.ast_grep._run_sg", _fake_run):
                tool = AstGrepReplaceTool(cwd="/tmp")
                result = await tool.execute({"pattern": "x", "rewrite": "y", "lang": "python"})
        assert "DRY RUN" in result.content

    @pytest.mark.asyncio
    async def test_stderr_error_returned(self) -> None:
        async def _fake_run(args, cwd):
            return ("", "compile error", 1)

        with patch("shutil.which", return_value="/usr/bin/sg"):
            with patch("prax.tools.ast_grep._run_sg", _fake_run):
                tool = AstGrepReplaceTool(cwd="/tmp")
                result = await tool.execute({"pattern": "x", "rewrite": "y", "lang": "python"})
        assert result.is_error


# ── prax/tools/tmux_bash.py ───────────────────────────────────────────────────

from prax.tools.tmux_bash import TmuxBashTool, _tokenize


class TestTokenize:
    def test_simple(self) -> None:
        assert _tokenize("new-session -d -s mydev") == ["new-session", "-d", "-s", "mydev"]

    def test_quoted_string(self) -> None:
        tokens = _tokenize('send-keys -t dev "python script.py" Enter')
        assert "python script.py" in tokens

    def test_escaped_char(self) -> None:
        tokens = _tokenize(r"cmd\ with\ spaces")
        assert len(tokens) == 1
        assert "cmd with spaces" == tokens[0]


class TestTmuxBashTool:
    @pytest.mark.asyncio
    async def test_tmux_not_found(self) -> None:
        with patch("shutil.which", return_value=None):
            tool = TmuxBashTool(cwd="/tmp")
            result = await tool.execute({"tmux_command": "new-session -d"})
        assert result.is_error
        assert "tmux not found" in result.content

    @pytest.mark.asyncio
    async def test_empty_command(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/tmux"):
            tool = TmuxBashTool(cwd="/tmp")
            result = await tool.execute({"tmux_command": "   "})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_blocked_subcommand(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/tmux"):
            tool = TmuxBashTool(cwd="/tmp")
            result = await tool.execute({"tmux_command": "capture-pane -t dev"})
        assert result.is_error
        assert "blocked" in result.content.lower()

    @pytest.mark.asyncio
    async def test_blocked_subcommand_session_extracted_from_flag(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/tmux"):
            tool = TmuxBashTool(cwd="/tmp")
            result = await tool.execute({"tmux_command": "capture-pane -t mysession"})
        assert "mysession" in result.content

    @pytest.mark.asyncio
    async def test_successful_command(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output\n", b""))
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("shutil.which", return_value="/usr/bin/tmux"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                tool = TmuxBashTool(cwd="/tmp")
                result = await tool.execute({"tmux_command": "new-session -d -s test"})
        assert not result.is_error
        assert "output" in result.content

    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_error(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"session failed"))
        mock_proc.returncode = 1
        mock_proc.kill = MagicMock()

        with patch("shutil.which", return_value="/usr/bin/tmux"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                tool = TmuxBashTool(cwd="/tmp")
                result = await tool.execute({"tmux_command": "new-session -d -s fail"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_is_available(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/tmux"):
            assert TmuxBashTool.is_available() is True
        with patch("shutil.which", return_value=None):
            assert TmuxBashTool.is_available() is False


# ── prax/tools/get_skill.py ───────────────────────────────────────────────────

from prax.tools.get_skill import GetSkillTool
from prax.core.skills_loader import Skill, SkillIndex


def _make_skill_index(*names: str) -> SkillIndex:
    skills = [
        Skill(name=n, description=f"{n} desc", content=f"# {n}", path=f"/path/{n}")
        for n in names
    ]
    return SkillIndex(skills)


class TestGetSkillTool:
    @pytest.mark.asyncio
    async def test_known_skill(self) -> None:
        index = _make_skill_index("commit", "review")
        tool = GetSkillTool(index=index)
        result = await tool.execute({"skill_name": "commit"})
        assert not result.is_error
        assert "commit" in result.content

    @pytest.mark.asyncio
    async def test_unknown_skill(self) -> None:
        index = _make_skill_index("commit")
        tool = GetSkillTool(index=index)
        result = await tool.execute({"skill_name": "nonexistent"})
        assert result.is_error
        assert "not found" in result.content
        assert "commit" in result.content  # available list shown


# ── prax/tools/grep_tool.py ───────────────────────────────────────────────────

from prax.tools.grep_tool import GrepTool


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_invalid_regex(self) -> None:
        tool = GrepTool(cwd="/tmp")
        result = await tool.execute({"pattern": "[invalid"})
        assert result.is_error
        assert "invalid regex" in result.content

    @pytest.mark.asyncio
    async def test_no_matches(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("hello world")
        tool = GrepTool(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "NOTHERE"})
        assert "No matches" in result.content

    @pytest.mark.asyncio
    async def test_finds_match(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\n")
        tool = GrepTool(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "def foo"})
        assert "def foo" in result.content

    @pytest.mark.asyncio
    async def test_case_insensitive(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("Hello World")
        tool = GrepTool(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "hello world", "case_insensitive": True})
        assert not result.is_error
        assert "Hello World" in result.content


# ── prax/tools/git_tool.py  line 54 ──────────────────────────────────────────

from prax.tools.git_tool import GitTool


class TestGitTool:
    @pytest.mark.asyncio
    async def test_invalid_operation(self) -> None:
        tool = GitTool(cwd="/tmp")
        result = await tool.execute({"operation": "nuke", "args": []})
        assert result.is_error
        assert "invalid operation" in result.content

    @pytest.mark.asyncio
    async def test_dangerous_flag_on_read_op(self) -> None:
        tool = GitTool(cwd="/tmp")
        result = await tool.execute({"operation": "diff", "args": ["--force"]})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_error(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repo"))
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            tool = GitTool(cwd="/tmp")
            result = await tool.execute({"operation": "status", "args": []})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_success_with_output(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"On branch main\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            tool = GitTool(cwd="/tmp")
            result = await tool.execute({"operation": "status", "args": []})
        assert not result.is_error
        assert "On branch main" in result.content

    def test_required_permission_write_op(self) -> None:
        tool = GitTool()
        from prax.tools.base import PermissionLevel
        perm = tool.required_permission({"operation": "commit"})
        assert perm == PermissionLevel.REVIEW

    def test_required_permission_read_op(self) -> None:
        tool = GitTool()
        from prax.tools.base import PermissionLevel
        perm = tool.required_permission({"operation": "status"})
        assert perm == PermissionLevel.SAFE


# ── prax/tools/sandbox_bash.py ────────────────────────────────────────────────

from prax.tools.sandbox_bash import SandboxBashTool
from prax.core.sandbox.base import SandboxResult


class TestSandboxBashTool:
    def _make_tool(self, tmp_path: Path) -> tuple[SandboxBashTool, MagicMock]:
        mock_sb = MagicMock()
        mock_sb.execute_command_v2.return_value = SandboxResult(output="done", exit_code=0)
        mock_provider = MagicMock()
        mock_provider.get.return_value = mock_sb
        mock_provider.acquire.return_value = "default-sid"

        with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=mock_provider):
            tool = SandboxBashTool(cwd=str(tmp_path))
        tool._provider = mock_provider
        return tool, mock_sb

    @pytest.mark.asyncio
    async def test_empty_command_returns_error(self, tmp_path: Path) -> None:
        tool, _ = self._make_tool(tmp_path)
        result = await tool.execute({"command": "   "})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_success_path(self, tmp_path: Path) -> None:
        tool, mock_sb = self._make_tool(tmp_path)
        tool._default_sid = "default-sid"
        result = await tool.execute({"command": "echo hi", "timeout": 30})
        assert not result.is_error
        assert "done" in result.content

    def test_safe_verify_command_uses_review_permission(self, tmp_path: Path) -> None:
        tool, _ = self._make_tool(tmp_path)
        perm = tool.required_permission({"command": "pytest -q"})
        assert perm == PermissionLevel.REVIEW

    @pytest.mark.asyncio
    async def test_nonzero_exit_appends_code(self, tmp_path: Path) -> None:
        tool, mock_sb = self._make_tool(tmp_path)
        mock_sb.execute_command_v2.return_value = SandboxResult(output="fail", exit_code=2)
        tool._default_sid = "default-sid"
        result = await tool.execute({"command": "false"})
        assert result.is_error
        assert "Exit code: 2" in result.content

    def test_release_clears_default_sid(self, tmp_path: Path) -> None:
        tool, _ = self._make_tool(tmp_path)
        tool._default_sid = "default-sid"
        tool.release()
        assert tool._default_sid is None


# ── prax/tools/task.py ────────────────────────────────────────────────────────

from prax.tools.task import TaskTool, VALID_SUBAGENT_TYPES


class TestTaskTool:
    @pytest.mark.asyncio
    async def test_empty_description(self) -> None:
        tool = TaskTool(executor=AsyncMock(return_value="result"))
        result = await tool.execute({"description": "", "prompt": "do stuff"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_prompt(self) -> None:
        tool = TaskTool(executor=AsyncMock(return_value="result"))
        result = await tool.execute({"description": "desc", "prompt": "  "})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_invalid_subagent_type(self) -> None:
        tool = TaskTool(executor=AsyncMock(return_value="result"))
        result = await tool.execute({
            "description": "desc",
            "prompt": "do stuff",
            "subagent_type": "invalid-type",
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_invalid_max_turns(self) -> None:
        tool = TaskTool(executor=AsyncMock(return_value="result"))
        result = await tool.execute({
            "description": "desc",
            "prompt": "do stuff",
            "max_turns": 0,
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        executor = AsyncMock(return_value="subagent result")
        tool = TaskTool(executor=executor)
        result = await tool.execute({
            "description": "desc",
            "prompt": "do stuff",
            "subagent_type": "code",
        })
        assert not result.is_error
        assert "subagent result" in result.content

    @pytest.mark.asyncio
    async def test_non_list_load_skills_normalised(self) -> None:
        executor = AsyncMock(return_value="ok")
        tool = TaskTool(executor=executor)
        await tool.execute({
            "description": "desc",
            "prompt": "do stuff",
            "load_skills": "not-a-list",
        })
        # executor called with load_skills=None
        _, kwargs = executor.call_args
        assert kwargs.get("load_skills") is None or executor.call_args[0][4] is None


# ── prax/tools/todo_write.py ─────────────────────────────────────────────────

from prax.tools.todo_write import TodoWriteTool


class TestTodoWriteTool:
    def _tool(self, tmp_path: Path) -> TodoWriteTool:
        return TodoWriteTool(cwd=str(tmp_path))

    @pytest.mark.asyncio
    async def test_non_list_todos(self, tmp_path: Path) -> None:
        tool = self._tool(tmp_path)
        result = await tool.execute({"todos": "not-a-list"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_todos_raises(self, tmp_path: Path) -> None:
        tool = self._tool(tmp_path)
        result = await tool.execute({"todos": []})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_invalid_todo_item(self, tmp_path: Path) -> None:
        tool = self._tool(tmp_path)
        result = await tool.execute({"todos": [{"content": "", "activeForm": "form", "status": "pending"}]})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_valid_todos_persisted(self, tmp_path: Path) -> None:
        tool = self._tool(tmp_path)
        result = await tool.execute({
            "todos": [{"content": "Write tests", "activeForm": "write-tests", "status": "pending"}]
        })
        assert not result.is_error
        payload = json.loads(result.content)
        assert len(payload["new_todos"]) == 1

    def test_file_accesses(self, tmp_path: Path) -> None:
        tool = self._tool(tmp_path)
        accesses = tool.file_accesses({})
        assert any(a.write for a in accesses)


# ── prax/tools/apply_patch.py  line 42 ───────────────────────────────────────

from prax.tools.apply_patch import ApplyPatchTool


class TestApplyPatchTool:
    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        tool = ApplyPatchTool()
        result = await tool.execute({
            "file_path": str(tmp_path / "missing.txt"),
            "hunks": [{"start_line": 1, "delete_count": 1, "replacement_lines": ["new"]}],
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_checksum_mismatch(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello\n")
        tool = ApplyPatchTool()
        result = await tool.execute({
            "file_path": str(f),
            "expected_sha256": "deadbeef" * 8,
            "hunks": [{"start_line": 1, "delete_count": 1, "replacement_lines": ["new"]}],
        })
        assert result.is_error
        assert "checksum" in result.content

    @pytest.mark.asyncio
    async def test_hash_mismatch_on_hunk(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\n")
        tool = ApplyPatchTool()
        result = await tool.execute({
            "file_path": str(f),
            "hunks": [{
                "start_line": 1,
                "delete_count": 1,
                "expected_start_hash": "ZZ",  # wrong hash
                "replacement_lines": ["new"],
            }],
        })
        assert result.is_error
        assert "hash mismatch" in result.content

    @pytest.mark.asyncio
    async def test_successful_patch(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\nline3\n")
        from prax.tools.hashing import compute_line_hash
        h = compute_line_hash(2, "line2")
        tool = ApplyPatchTool()
        result = await tool.execute({
            "file_path": str(f),
            "hunks": [{
                "start_line": 2,
                "delete_count": 1,
                "expected_start_hash": h,
                "replacement_lines": ["replaced"],
            }],
        })
        assert not result.is_error
        assert "replaced" in f.read_text()
