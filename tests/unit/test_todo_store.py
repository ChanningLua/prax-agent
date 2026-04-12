"""Unit tests for TodoStore — concurrent safety, locking, parse validation."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import pytest

from prax.core.todo_store import TodoItem, TodoStore, VALID_TODO_STATUSES


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _store(tmpdir: str) -> TodoStore:
    return TodoStore(tmpdir)


def _item(content="do something", active_form="Doing something", status="pending") -> TodoItem:
    return TodoItem(content=content, active_form=active_form, status=status)


# ── Basic round-trip ──────────────────────────────────────────────────────────

class TestTodoStoreBasic:
    def test_load_empty_when_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            assert _store(d).load() == []

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            items = [
                _item("write tests", "Writing tests", "in_progress"),
                _item("review PR", "Reviewing PR", "pending"),
            ]
            store.save(items)
            loaded = store.load()
            assert len(loaded) == 2
            assert loaded[0].content == "write tests"
            assert loaded[0].status == "in_progress"
            assert loaded[1].content == "review PR"

    def test_save_all_completed_clears_file(self):
        """All-completed list should produce an empty file (persisted=[])."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            items = [_item(status="completed"), _item(status="completed")]
            store.save(items)
            loaded = store.load()
            assert loaded == []

    def test_save_mixed_statuses_persists_all(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            items = [_item(status="completed"), _item(status="pending")]
            store.save(items)
            assert len(store.load()) == 2

    def test_todo_path_uses_prax_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            path = _store(d).todo_path
            assert path.parent.name == ".prax"
            assert path.name == "todos.json"

    def test_clear_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save([_item()])
            assert store.todo_path.exists()
            store.clear()
            assert not store.todo_path.exists()

    def test_clear_noop_when_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            _store(d).clear()   # must not raise


# ── to_dict / parse symmetry ─────────────────────────────────────────────────

class TestTodoItemSerialization:
    def test_to_dict_keys(self):
        item = _item("task", "Tasking", "pending")
        d = item.to_dict()
        assert d == {"content": "task", "activeForm": "Tasking", "status": "pending"}

    def test_parse_item_valid(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            raw = {"content": "x", "activeForm": "Xing", "status": "in_progress"}
            item = store._parse_item(raw)
            assert item.content == "x"
            assert item.active_form == "Xing"
            assert item.status == "in_progress"

    def test_parse_item_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            item = store._parse_item({"content": "  hello  ", "activeForm": " Hi ", "status": "pending"})
            assert item.content == "hello"
            assert item.active_form == "Hi"

    def test_parse_item_rejects_empty_content(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            with pytest.raises(ValueError, match="content"):
                store._parse_item({"content": "", "activeForm": "X", "status": "pending"})

    def test_parse_item_rejects_empty_active_form(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            with pytest.raises(ValueError, match="activeForm"):
                store._parse_item({"content": "x", "activeForm": "", "status": "pending"})

    def test_parse_item_rejects_invalid_status(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            with pytest.raises(ValueError, match="status"):
                store._parse_item({"content": "x", "activeForm": "X", "status": "done"})

    def test_all_valid_statuses_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            for s in VALID_TODO_STATUSES:
                item = store._parse_item({"content": "x", "activeForm": "X", "status": s})
                assert item.status == s

    def test_parse_item_rejects_non_dict(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            with pytest.raises(ValueError):
                store._parse_item("not a dict")  # type: ignore


# ── replace() ────────────────────────────────────────────────────────────────

class TestTodoStoreReplace:
    def _raw(self, content="task", status="pending"):
        return {"content": content, "activeForm": "Working", "status": status}

    def test_replace_returns_old_new(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save([_item("old task")])
            old, new, nudge = store.replace([self._raw("new task")])
            assert old[0].content == "old task"
            assert new[0].content == "new task"

    def test_replace_empty_raises(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            with pytest.raises(ValueError):
                store.replace([])

    def test_replace_all_completed_nudge_triggered(self):
        """≥3 completed todos without 'verif' in any content → nudge=True."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            items = [self._raw(f"task {i}", "completed") for i in range(3)]
            _, _, nudge = store.replace(items)
            assert nudge is True

    def test_replace_nudge_suppressed_when_verif_present(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            items = [
                self._raw("task 1", "completed"),
                self._raw("task 2", "completed"),
                self._raw("verification step", "completed"),
            ]
            _, _, nudge = store.replace(items)
            assert nudge is False

    def test_replace_nudge_suppressed_when_fewer_than_3(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            items = [self._raw("a", "completed"), self._raw("b", "completed")]
            _, _, nudge = store.replace(items)
            assert nudge is False


# ── Atomic write / tmp cleanup ────────────────────────────────────────────────

class TestTodoStoreAtomic:
    def test_no_stale_tmp_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save([_item()])
            tmp_files = list(Path(d, ".prax").glob("*.tmp"))
            assert tmp_files == [], f"Stale tmp files: {tmp_files}"

    def test_file_is_valid_json_after_save(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save([_item("alpha", "Alphaing", "pending")])
            raw = json.loads(store.todo_path.read_text())
            assert isinstance(raw, list)
            assert raw[0]["content"] == "alpha"


# ── Concurrent write safety ───────────────────────────────────────────────────

class TestTodoStoreConcurrent:
    def test_concurrent_writes_no_corruption(self):
        """20 threads each writing a different todo — final file must be valid JSON."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            errors: list[Exception] = []

            def worker(i: int):
                try:
                    store.save([_item(f"task-{i}", f"Working on {i}", "pending")])
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert errors == [], f"Worker errors: {errors}"
            # File must still be valid JSON
            raw = json.loads(store.todo_path.read_text())
            assert isinstance(raw, list)

    def test_concurrent_reads_do_not_block(self):
        """Multiple concurrent loads should all succeed."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save([_item("shared", "Sharing", "pending")])
            results: list[list] = []
            errors: list[Exception] = []

            def reader():
                try:
                    results.append(store.load())
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=reader) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert errors == []
            assert all(len(r) == 1 for r in results)
