"""Checkpoint store — persist and restore agent loop state for crash recovery.

Saves message_history + iteration metadata to .prax/checkpoints/<session_id>/
at configurable intervals, enabling Ralph to resume from the last checkpoint
after crashes or restarts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """Snapshot of agent loop state at a given iteration."""
    session_id: str
    iteration: int
    task: str
    model: str
    message_history: list[dict[str, Any]]
    todo_snapshot: list[dict[str, Any]]
    created_at: str
    metadata: dict[str, Any] | None = None


class CheckpointStore:
    """Persist checkpoint snapshots to disk for crash recovery."""

    def __init__(self, cwd: str, session_id: str | None = None):
        self._base_dir = Path(cwd) / ".prax" / "checkpoints"
        self._session_id = session_id

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id

    def save(self, checkpoint: Checkpoint) -> Path:
        """Save a checkpoint to disk. Keeps only the latest per session."""
        session_dir = self._session_dir(checkpoint.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Write latest checkpoint (overwrite previous)
        path = session_dir / "latest.json"
        data = {
            "session_id": checkpoint.session_id,
            "iteration": checkpoint.iteration,
            "task": checkpoint.task,
            "model": checkpoint.model,
            "message_history": checkpoint.message_history,
            "todo_snapshot": checkpoint.todo_snapshot,
            "created_at": checkpoint.created_at,
            "metadata": checkpoint.metadata or {},
        }

        try:
            # Write atomically: write to tmp then rename
            tmp_path = session_dir / "latest.tmp"
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.rename(path)
            logger.debug(
                "Checkpoint saved: session=%s iter=%d msgs=%d",
                checkpoint.session_id,
                checkpoint.iteration,
                len(checkpoint.message_history),
            )
        except Exception as e:
            logger.warning("Failed to save checkpoint: %s", e)
            # Clean up tmp if rename failed
            tmp_path = session_dir / "latest.tmp"
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        return path

    def load(self, session_id: str) -> Checkpoint | None:
        """Load the latest checkpoint for a session."""
        path = self._session_dir(session_id) / "latest.json"
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint(
                session_id=data["session_id"],
                iteration=data["iteration"],
                task=data["task"],
                model=data["model"],
                message_history=data["message_history"],
                todo_snapshot=data.get("todo_snapshot", []),
                created_at=data["created_at"],
                metadata=data.get("metadata"),
            )
        except Exception as e:
            logger.warning("Failed to load checkpoint from %s: %s", path, e)
            return None

    def clear(self, session_id: str) -> None:
        """Remove checkpoint for a completed session."""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return
        for f in session_dir.iterdir():
            f.unlink()
        session_dir.rmdir()
        logger.debug("Checkpoint cleared: session=%s", session_id)

    def list_sessions(self) -> list[str]:
        """List all session IDs that have checkpoints."""
        if not self._base_dir.exists():
            return []
        return [
            d.name for d in sorted(self._base_dir.iterdir())
            if d.is_dir() and (d / "latest.json").exists()
        ]

    @staticmethod
    def create_checkpoint(
        *,
        session_id: str,
        iteration: int,
        task: str,
        model: str,
        message_history: list[dict[str, Any]],
        todo_snapshot: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Checkpoint:
        return Checkpoint(
            session_id=session_id,
            iteration=iteration,
            task=task,
            model=model,
            message_history=message_history,
            todo_snapshot=todo_snapshot or [],
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
        )
