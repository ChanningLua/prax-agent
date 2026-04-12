"""Integration tests for CheckpointStore — save/load/clear/resume lifecycle."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prax.core.checkpoint import Checkpoint, CheckpointStore


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(tmpdir: str) -> CheckpointStore:
    return CheckpointStore(cwd=tmpdir)


def _cp(session_id: str = "sess_abc", iteration: int = 1) -> Checkpoint:
    return CheckpointStore.create_checkpoint(
        session_id=session_id,
        iteration=iteration,
        task="implement the thing",
        model="glm-4-flash",
        message_history=[{"role": "user", "content": "hello"}],
        todo_snapshot=[{"content": "step 1", "status": "pending"}],
    )


# ── save / load ───────────────────────────────────────────────────────────────

class TestCheckpointStoreSaveLoad:
    def test_load_returns_none_when_no_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            assert _store(d).load("no_such_session") is None

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            cp = _cp("sess_1", iteration=3)
            store.save(cp)
            loaded = store.load("sess_1")
            assert loaded is not None
            assert loaded.session_id == "sess_1"
            assert loaded.iteration == 3
            assert loaded.task == "implement the thing"
            assert loaded.model == "glm-4-flash"

    def test_message_history_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            history = [
                {"role": "user", "content": "do x"},
                {"role": "assistant", "content": "done"},
            ]
            cp = CheckpointStore.create_checkpoint(
                session_id="sess_hist",
                iteration=1,
                task="t",
                model="m",
                message_history=history,
            )
            store.save(cp)
            loaded = store.load("sess_hist")
            assert loaded.message_history == history

    def test_todo_snapshot_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            snapshot = [{"content": "task a", "status": "in_progress"}]
            cp = CheckpointStore.create_checkpoint(
                session_id="sess_snap",
                iteration=2,
                task="t",
                model="m",
                message_history=[],
                todo_snapshot=snapshot,
            )
            store.save(cp)
            loaded = store.load("sess_snap")
            assert loaded.todo_snapshot == snapshot

    def test_metadata_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            cp = CheckpointStore.create_checkpoint(
                session_id="sess_meta",
                iteration=1,
                task="t",
                model="m",
                message_history=[],
                metadata={"error": "oops", "recovery": "retry"},
            )
            store.save(cp)
            loaded = store.load("sess_meta")
            assert loaded.metadata["error"] == "oops"

    def test_overwrite_keeps_latest(self):
        """Second save must overwrite the first — only latest checkpoint kept."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_ow", iteration=1))
            store.save(_cp("sess_ow", iteration=5))
            loaded = store.load("sess_ow")
            assert loaded.iteration == 5

    def test_created_at_is_set(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            cp = _cp("sess_ts")
            store.save(cp)
            loaded = store.load("sess_ts")
            assert loaded.created_at != ""

    def test_checkpoint_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_json"))
            path = Path(d) / ".prax" / "checkpoints" / "sess_json" / "latest.json"
            import json
            data = json.loads(path.read_text())
            assert data["session_id"] == "sess_json"

    def test_no_stale_tmp_after_save(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_tmp"))
            cp_dir = Path(d) / ".prax" / "checkpoints" / "sess_tmp"
            assert not (cp_dir / "latest.tmp").exists()


# ── clear ─────────────────────────────────────────────────────────────────────

class TestCheckpointStoreClear:
    def test_clear_removes_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_clr"))
            store.clear("sess_clr")
            assert store.load("sess_clr") is None

    def test_clear_noop_when_no_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            _store(d).clear("nonexistent")   # must not raise

    def test_clear_removes_directory(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_rmdir"))
            store.clear("sess_rmdir")
            cp_dir = Path(d) / ".prax" / "checkpoints" / "sess_rmdir"
            assert not cp_dir.exists()


# ── list_sessions ─────────────────────────────────────────────────────────────

class TestCheckpointStoreListSessions:
    def test_list_empty_when_no_checkpoints(self):
        with tempfile.TemporaryDirectory() as d:
            assert _store(d).list_sessions() == []

    def test_list_returns_saved_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_a"))
            store.save(_cp("sess_b"))
            sessions = store.list_sessions()
            assert "sess_a" in sessions
            assert "sess_b" in sessions

    def test_list_does_not_include_cleared_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.save(_cp("sess_keep"))
            store.save(_cp("sess_gone"))
            store.clear("sess_gone")
            sessions = store.list_sessions()
            assert "sess_keep" in sessions
            assert "sess_gone" not in sessions

    def test_multiple_sessions_independent(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            for i in range(5):
                store.save(_cp(f"sess_{i}", iteration=i))
            for i in range(5):
                loaded = store.load(f"sess_{i}")
                assert loaded.iteration == i
