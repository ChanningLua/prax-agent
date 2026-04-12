"""LocalMemoryBackend — file-based memory backend.

Storage layout::

  {cwd}/.prax/memory.json        ← project facts + context
                                       (same schema as existing MemoryStore)
  ~/.prax/experiences.json       ← global cross-project experiences
                                       (written atomically with tmp→rename)

Both files use mtime-based in-process caching so repeated reads within one
session avoid unnecessary disk I/O.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backend import Experience, Fact, MemoryBackend, MemoryContext

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()  # process-wide guard for the experiences file


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_memory_path(cwd: str) -> Path:
    return Path(cwd) / ".prax" / "memory.json"


def _global_experiences_path() -> Path:
    return Path.home() / ".prax" / "experiences.json"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as JSON to *path* via tmp→rename (atomic on POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return default


# ── Empty structures ─────────────────────────────────────────────────────────


def _empty_project_memory() -> dict[str, Any]:
    return {
        "version": "1.0",
        "lastUpdated": _now_iso(),
        "workContext": "",
        "topOfMind": "",
        "facts": [],
    }


def _empty_experiences() -> dict[str, Any]:
    return {
        "version": "1.0",
        "lastUpdated": _now_iso(),
        "experiences": [],
    }


# ── Cache helper (mtime-keyed) ───────────────────────────────────────────────


class _MtimeCache:
    """Single-value cache invalidated by file mtime."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[Any, float | None]] = {}

    def get(self, path: Path) -> Any | None:
        try:
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            mtime = None
        cached = self._cache.get(path)
        if cached is None:
            return None
        data, cached_mtime = cached
        return data if cached_mtime == mtime else None

    def set(self, path: Path, data: Any) -> None:
        try:
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            mtime = None
        self._cache[path] = (data, mtime)

    def invalidate(self, path: Path) -> None:
        self._cache.pop(path, None)


# ── Backend implementation ────────────────────────────────────────────────────


class LocalMemoryBackend(MemoryBackend):
    """Pure file-based implementation of MemoryBackend.

    No external services required.  Compatible with existing MemoryStore
    format so old .prax/memory.json files work without migration.
    """

    def __init__(
        self,
        *,
        max_facts: int = 100,
        fact_confidence_threshold: float = 0.7,
        max_experiences: int = 500,
    ) -> None:
        self._max_facts = max_facts
        self._fact_confidence_threshold = fact_confidence_threshold
        self._max_experiences = max_experiences
        self._cache = _MtimeCache()

    # ── Internal: project memory ──────────────────────────────────────────

    def _load_project(self, cwd: str) -> dict[str, Any]:
        path = _project_memory_path(cwd)
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        raw = _load_json(path, None)
        if raw is None:
            data = _empty_project_memory()
        else:
            data = self._migrate_project(raw)
        self._cache.set(path, data)
        return data

    def _save_project(self, cwd: str, data: dict[str, Any]) -> None:
        path = _project_memory_path(cwd)
        data["lastUpdated"] = _now_iso()
        _atomic_write(path, data)
        self._cache.invalidate(path)

    @staticmethod
    def _migrate_project(raw: dict[str, Any]) -> dict[str, Any]:
        """Upgrade old MemoryStore format to the current schema."""
        # Old format had "workContext"/"topOfMind" as top-level strings — keep.
        # Old format had facts as list[str] — convert to list[dict].
        facts = raw.get("facts", [])
        if facts and isinstance(facts[0], str):
            now = _now_iso()
            facts = [
                {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": f,
                    "category": "context",
                    "confidence": 0.8,
                    "createdAt": now,
                    "source": "migration",
                }
                for f in facts
            ]
            raw["facts"] = facts
        raw.setdefault("version", "1.0")
        raw.setdefault("workContext", "")
        raw.setdefault("topOfMind", "")
        return raw

    # ── Internal: global experiences ──────────────────────────────────────

    def _load_experiences(self) -> dict[str, Any]:
        path = _global_experiences_path()
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        data = _load_json(path, None) or _empty_experiences()
        self._cache.set(path, data)
        return data

    def _save_experiences(self, data: dict[str, Any]) -> None:
        path = _global_experiences_path()
        data["lastUpdated"] = _now_iso()
        with _LOCK:
            _atomic_write(path, data)
        self._cache.invalidate(path)

    # ── MemoryBackend: facts ──────────────────────────────────────────────

    async def get_facts(self, cwd: str, limit: int = 100) -> list[Fact]:
        data = self._load_project(cwd)
        raw_facts: list[dict[str, Any]] = data.get("facts", [])
        # Sort by confidence desc, cap at limit
        sorted_facts = sorted(
            raw_facts,
            key=lambda f: f.get("confidence", 0.5),
            reverse=True,
        )[:limit]
        return [Fact.from_dict(f) for f in sorted_facts]

    async def store_fact(self, cwd: str, fact: Fact) -> None:
        if fact.confidence < self._fact_confidence_threshold:
            return
        data = self._load_project(cwd)
        facts: list[dict[str, Any]] = data.get("facts", [])

        # Deduplicate by normalised content
        existing_keys = {f.get("content", "").strip().lower() for f in facts}
        key = fact.content.strip().lower()
        if key in existing_keys:
            return

        facts.append(fact.to_dict())

        # Enforce max_facts (keep highest confidence)
        if len(facts) > self._max_facts:
            facts = sorted(
                facts, key=lambda f: f.get("confidence", 0.5), reverse=True
            )[: self._max_facts]

        data["facts"] = facts
        self._save_project(cwd, data)

    async def delete_fact(self, cwd: str, fact_id: str) -> None:
        data = self._load_project(cwd)
        facts = [f for f in data.get("facts", []) if f.get("id") != fact_id]
        data["facts"] = facts
        self._save_project(cwd, data)

    # ── MemoryBackend: context ─────────────────────────────────────────────

    async def get_context(self, cwd: str) -> MemoryContext:
        data = self._load_project(cwd)
        return MemoryContext(
            work_context=data.get("workContext", ""),
            top_of_mind=data.get("topOfMind", ""),
            updated_at=data.get("lastUpdated", ""),
        )

    async def save_context(self, cwd: str, ctx: MemoryContext) -> None:
        data = self._load_project(cwd)
        if ctx.work_context:
            data["workContext"] = ctx.work_context
        if ctx.top_of_mind:
            data["topOfMind"] = ctx.top_of_mind
        self._save_project(cwd, data)

    # ── MemoryBackend: global experiences ─────────────────────────────────

    async def get_experiences(
        self, task_type: str, limit: int = 10
    ) -> list[Experience]:
        data = self._load_experiences()
        exps: list[dict[str, Any]] = data.get("experiences", [])

        # Filter by task_type or matching tags, then return most recent
        matched = [
            e for e in exps
            if e.get("task_type") == task_type
            or task_type in e.get("tags", [])
            or task_type == "general"
        ]
        # Most recent first (ISO timestamps sort lexicographically)
        matched.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return [Experience.from_dict(e) for e in matched[:limit]]

    async def store_experience(self, exp: Experience) -> None:
        if not exp.insight:
            return
        data = self._load_experiences()
        exps: list[dict[str, Any]] = data.get("experiences", [])

        # Deduplicate by insight content
        existing_insights = {e.get("insight", "").strip().lower() for e in exps}
        if exp.insight.strip().lower() in existing_insights:
            return

        exps.append(exp.to_dict())

        # Enforce max_experiences (keep most recent)
        if len(exps) > self._max_experiences:
            exps.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            exps = exps[: self._max_experiences]

        data["experiences"] = exps
        self._save_experiences(data)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        # No persistent connections to close
        pass
