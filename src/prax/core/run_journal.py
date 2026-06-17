"""Run journal — append-only event log for crash-surviving 7x24 orchestration.

Each orchestration run owns a directory ``.prax/runs/<run_id>/`` whose
``journal.jsonl`` holds one JSON event per line. On restart the orchestrator
replays the journal to skip already-completed steps instead of redoing them
(event-sourcing semantics, file-local — no server, single-writer per run).

Crash tolerance:
- appends are flushed + fsynced;
- a truncated trailing line left by a crash mid-write is skipped on read;
- the next append is newline-terminated first, so a fresh record never
  concatenates onto a partial one.

NOTE: ``append`` is O(n) in the current event count (it re-reads to find the
next seq). Run windows are bounded and events are coarse (one per step, not
per token), so this is fine for v1; optimize with a cached seq if a single run
ever accumulates a very large journal.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_JOURNAL_SCHEMA_VERSION = "prax.run_journal.v1"


class RunJournal:
    """Append-only ``.prax/runs/<run_id>/journal.jsonl`` with replay."""

    def __init__(self, cwd: str, run_id: str) -> None:
        self._run_id = run_id
        self._dir = Path(cwd) / ".prax" / "runs" / run_id
        self._path = self._dir / "journal.jsonl"

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def path(self) -> Path:
        return self._path

    def events(self) -> list[dict[str, Any]]:
        """All valid events in order; corrupt / truncated lines are skipped."""
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue  # truncated crash tail or corrupt line — skip it
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Stamp seq/ts/version, durably append one line, return the event."""
        existing = self.events()
        next_seq = 1 + max((e.get("seq", -1) for e in existing), default=-1)

        enriched = dict(event)
        enriched["seq"] = next_seq
        enriched.setdefault("ts", datetime.now(timezone.utc).isoformat())
        enriched["v"] = RUN_JOURNAL_SCHEMA_VERSION

        self._dir.mkdir(parents=True, exist_ok=True)
        self._ensure_newline_terminated()
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(enriched, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return enriched

    def record(self, step: str, output: Any = None, **extra: Any) -> dict[str, Any]:
        """Convenience over :meth:`append`. Extra kwargs become event fields."""
        return self.append({"step": step, "output": output, **extra})

    def result_for(self, step: str) -> Any:
        """Output of the LAST event recorded for *step*, or ``None`` if none."""
        result: Any = None
        found = False
        for e in self.events():
            if e.get("step") == step:
                result = e.get("output")
                found = True
        return result if found else None

    def done_steps(self) -> set[str]:
        """Set of step keys that have at least one recorded event."""
        return {e["step"] for e in self.events() if "step" in e}

    def _ensure_newline_terminated(self) -> None:
        """Terminate a crash-truncated final line so the next append starts
        on a fresh line instead of concatenating onto a partial record."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return
        with open(self._path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            last = fh.read(1)
        if last != b"\n":
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write("\n")
