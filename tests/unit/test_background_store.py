"""Unit tests for prax/core/background_store.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prax.core.background_store import BackgroundTask, BackgroundTaskStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str = "task_abc1", status: str = "running") -> BackgroundTask:
    return BackgroundTask(
        task_id=task_id,
        description="Do something",
        prompt="Run the thing",
        subagent_type="generic",
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBackgroundTaskStore:
    def test_create_and_get_roundtrip(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_001")
        store.create(task)

        result = store.get("task_001")
        assert result is not None
        assert result.task_id == "task_001"
        assert result.description == "Do something"

        payload = json.loads((tmp_path / ".prax" / "tasks" / "task_001.json").read_text(encoding="utf-8"))
        assert payload["schema_version"] == "prax.background_task.v1"

    def test_get_missing_returns_none(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        assert store.get("task_missing") is None

    def test_get_corrupt_file_returns_none(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        tasks_dir = tmp_path / ".prax" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "task_bad.json").write_text("{not valid json", encoding="utf-8")

        assert store.get("task_bad") is None

    def test_update_status_with_result(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_002")
        store.create(task)

        store.update_status("task_002", "success", result="done")
        updated = store.get("task_002")

        assert updated is not None
        assert updated.status == "success"
        assert updated.result == "done"

    def test_update_status_with_error(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_003")
        store.create(task)

        store.update_status("task_003", "error", error="something broke")
        updated = store.get("task_003")

        assert updated is not None
        assert updated.status == "error"
        assert updated.error == "something broke"

    def test_update_status_sets_finished_at_for_success(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_004")
        store.create(task)

        store.update_status("task_004", "success")
        updated = store.get("task_004")

        assert updated is not None
        assert updated.finished_at is not None

    def test_update_status_sets_finished_at_for_error(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_005")
        store.create(task)

        store.update_status("task_005", "error")
        updated = store.get("task_005")

        assert updated is not None
        assert updated.finished_at is not None

    def test_update_status_sets_finished_at_for_cancelled(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_006")
        store.create(task)

        store.update_status("task_006", "cancelled")
        updated = store.get("task_006")

        assert updated is not None
        assert updated.finished_at is not None

    def test_update_status_missing_task_is_noop(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        # Should not raise
        store.update_status("task_nonexistent", "success")

    def test_list_all_empty_dir(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        tasks_dir = tmp_path / ".prax" / "tasks"
        tasks_dir.mkdir(parents=True)

        assert store.list_all() == []

    def test_list_all_with_tasks(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        t1 = _make_task("task_a01")
        t2 = _make_task("task_a02")
        store.create(t1)
        store.create(t2)

        result = store.list_all()
        ids = [t.task_id for t in result]
        assert "task_a01" in ids
        assert "task_a02" in ids

    def test_list_all_no_dir_returns_empty(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        assert store.list_all() == []

    def test_cancel_running_task_returns_true(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_007", status="running")
        store.create(task)

        result = store.cancel("task_007")
        assert result is True

        updated = store.get("task_007")
        assert updated is not None
        assert updated.status == "cancelled"

    def test_cancel_non_running_task_returns_false(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        task = _make_task("task_008", status="success")
        store.create(task)

        result = store.cancel("task_008")
        assert result is False

    def test_cancel_missing_task_returns_false(self, tmp_path):
        store = BackgroundTaskStore(str(tmp_path))
        assert store.cancel("task_ghost") is False
