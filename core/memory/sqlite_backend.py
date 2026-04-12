"""SQLiteMemoryBackend — SQLite FTS5 backed memory backend.

使用 SQLite FTS5 虚拟表实现精确关键词检索，解决 facts 规模化后 JSON 线性扫描慢的问题。

Storage layout::

  {cwd}/.prax/memory.db        ← project facts + context (SQLite WAL mode)
  ~/.prax/experiences.db       ← global cross-project experiences

迁移工具::

  from prax.core.memory.sqlite_backend import migrate_from_json
  migrate_from_json(cwd)          # 读取 memory.json 批量写入 SQLite
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from .backend import Experience, Fact, MemoryBackend, MemoryContext
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

# Lazy KG cache per project path
_kg_cache: dict[str, "KnowledgeGraph"] = {}
_kg_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_db_path(cwd: str) -> Path:
    return Path(cwd) / ".prax" / "memory.db"


def _global_experiences_db_path() -> Path:
    return Path.home() / ".prax" / "experiences.db"


# ── Schema ────────────────────────────────────────────────────────────────────

_FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'context',
    confidence  REAL NOT NULL DEFAULT 0.5,
    created_at  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'unknown',
    source_error TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    id UNINDEXED,
    content,
    category UNINDEXED,
    content='facts',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, id, content, category)
    VALUES (new.rowid, new.id, new.content, new.category);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, id, content, category)
    VALUES ('delete', old.rowid, old.id, old.content, old.category);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, id, content, category)
    VALUES ('delete', old.rowid, old.id, old.content, old.category);
    INSERT INTO facts_fts(rowid, id, content, category)
    VALUES (new.rowid, new.id, new.content, new.category);
END;

CREATE TABLE IF NOT EXISTS memory_context (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_EXPERIENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
    id          TEXT PRIMARY KEY,
    task_type   TEXT NOT NULL,
    context     TEXT NOT NULL,
    insight     TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',
    timestamp   TEXT NOT NULL,
    project     TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
    id UNINDEXED,
    task_type,
    context,
    insight,
    content='experiences',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS exp_ai AFTER INSERT ON experiences BEGIN
    INSERT INTO experiences_fts(rowid, id, task_type, context, insight)
    VALUES (new.rowid, new.id, new.task_type, new.context, new.insight);
END;

CREATE TRIGGER IF NOT EXISTS exp_ad AFTER DELETE ON experiences BEGIN
    INSERT INTO experiences_fts(experiences_fts, rowid, id, task_type, context, insight)
    VALUES ('delete', old.rowid, old.id, old.task_type, old.context, old.insight);
END;
"""


@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_project_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_FACTS_SCHEMA)


def _init_experiences_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_EXPERIENCES_SCHEMA)


# ── Backend ───────────────────────────────────────────────────────────────────


class SQLiteMemoryBackend(MemoryBackend):
    """SQLite FTS5 backed memory backend.

    Args:
        max_facts: Maximum facts returned by get_facts().
        fact_confidence_threshold: Minimum confidence for facts returned.
        max_experiences: Maximum experiences stored globally.
    """

    def __init__(
        self,
        max_facts: int = 100,
        fact_confidence_threshold: float = 0.7,
        max_experiences: int = 500,
        vector_store: VectorStore | None = None,
    ) -> None:
        self._max_facts = max_facts
        self._threshold = fact_confidence_threshold
        self._max_experiences = max_experiences
        self._initialized_dbs: set[str] = set()
        self._vector_store = vector_store

    def _ensure_project_db(self, cwd: str) -> Path:
        db_path = _project_db_path(cwd)
        key = str(db_path)
        if key not in self._initialized_dbs:
            _init_project_db(db_path)
            self._initialized_dbs.add(key)
        return db_path

    def _ensure_experiences_db(self) -> Path:
        db_path = _global_experiences_db_path()
        key = str(db_path)
        if key not in self._initialized_dbs:
            _init_experiences_db(db_path)
            self._initialized_dbs.add(key)
        return db_path

    # ── Facts ─────────────────────────────────────────────────────────────

    async def get_facts(self, cwd: str, limit: int = 100) -> list[Fact]:
        db_path = self._ensure_project_db(cwd)
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, content, category, confidence, created_at, source, source_error
                FROM facts
                WHERE confidence >= ?
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (self._threshold, min(limit, self._max_facts)),
            ).fetchall()
        return [_row_to_fact(r) for r in rows]

    async def search_facts(self, cwd: str, query: str, limit: int = 20, min_confidence: float = 0.0) -> list[Fact]:
        """FTS5 精确关键词检索 facts，支持按置信度过滤。"""
        db_path = self._ensure_project_db(cwd)
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT f.id, f.content, f.category, f.confidence,
                       f.created_at, f.source, f.source_error
                FROM facts_fts
                JOIN facts f ON facts_fts.id = f.id
                WHERE facts_fts MATCH ?
                  AND f.confidence >= ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, min_confidence, limit),
            ).fetchall()
        return [_row_to_fact(r) for r in rows]

    async def hybrid_search_facts(self, cwd: str, query: str, limit: int = 10) -> list[Fact]:
        """FTS5 + 向量混合检索，按 fact id 去重，FTS5 结果优先，合并后按 confidence 降序。"""
        fts_facts = await self.search_facts(cwd, query, limit=limit)
        seen_ids: dict[str, Fact] = {f.id: f for f in fts_facts}

        if self._vector_store is not None:
            vec_results = await self._vector_store.query(cwd, query, n_results=limit)
            # 向量结果中没有完整 Fact，需按 id 回查 SQLite
            vec_ids = [r["id"] for r in vec_results if r["id"] not in seen_ids]
            if vec_ids:
                db_path = self._ensure_project_db(cwd)
                placeholders = ",".join("?" * len(vec_ids))
                with _connect(db_path) as conn:
                    rows = conn.execute(
                        f"SELECT id, content, category, confidence, created_at, source, source_error "
                        f"FROM facts WHERE id IN ({placeholders})",
                        vec_ids,
                    ).fetchall()
                for row in rows:
                    f = _row_to_fact(row)
                    if f.id not in seen_ids:
                        seen_ids[f.id] = f

        return sorted(seen_ids.values(), key=lambda f: f.confidence, reverse=True)

    async def format_for_prompt(
        self, cwd: str, task_type: str = "general", max_facts: int = 15
    ) -> str:
        """覆写基类实现：当 task_type 非空时用混合检索替换 get_facts。"""
        parts: list[str] = []

        ctx = await self.get_context(cwd)
        if ctx.work_context or ctx.top_of_mind:
            parts.append("## Memory")
            if ctx.work_context:
                parts.append(f"### Work Context\n{ctx.work_context}")
            if ctx.top_of_mind:
                parts.append(f"### Top of Mind\n{ctx.top_of_mind}")

        if task_type and task_type != "general":
            facts = await self.hybrid_search_facts(cwd, task_type, limit=max_facts)
        else:
            facts = await self.get_facts(cwd, limit=max_facts)

        if facts:
            if not parts:
                parts.append("## Memory")
            fact_lines = []
            for f in facts:
                line = f"- [{f.category}] {f.content}"
                if f.confidence >= 0.9:
                    line += " ✓"
                fact_lines.append(line)
            parts.append("### Facts\n" + "\n".join(fact_lines))

        experiences = await self.get_experiences(task_type, limit=5)
        if experiences:
            if not parts:
                parts.append("## Memory")
            exp_lines = [
                f"- [{e.task_type}] {e.insight}"
                for e in experiences
                if e.insight
            ]
            if exp_lines:
                parts.append("### Global Experiences\n" + "\n".join(exp_lines))

        return "\n\n".join(parts)

    async def store_fact(self, cwd: str, fact: Fact) -> None:
        db_path = self._ensure_project_db(cwd)
        with _connect(db_path) as conn:
            # 简单去重：相同 content 不重复插入
            existing = conn.execute(
                "SELECT id FROM facts WHERE content = ?", (fact.content,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE facts SET confidence = MAX(confidence, ?), category = ? WHERE id = ?",
                    (fact.confidence, fact.category, existing["id"]),
                )
                return
            conn.execute(
                """
                INSERT INTO facts (id, content, category, confidence, created_at, source, source_error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.id or str(uuid.uuid4()),
                    fact.content,
                    fact.category,
                    fact.confidence,
                    fact.created_at or _now_iso(),
                    fact.source,
                    fact.source_error,
                ),
            )

    async def delete_fact(self, cwd: str, fact_id: str) -> None:
        db_path = self._ensure_project_db(cwd)
        with _connect(db_path) as conn:
            conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))

    # ── Context ───────────────────────────────────────────────────────────

    async def get_context(self, cwd: str) -> MemoryContext:
        db_path = self._ensure_project_db(cwd)
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT key, value FROM memory_context WHERE key IN ('work_context', 'top_of_mind', 'updated_at')"
            ).fetchall()
        data = {r["key"]: r["value"] for r in rows}
        return MemoryContext(
            work_context=data.get("work_context", ""),
            top_of_mind=data.get("top_of_mind", ""),
            updated_at=data.get("updated_at", ""),
        )

    async def save_context(self, cwd: str, ctx: MemoryContext) -> None:
        db_path = self._ensure_project_db(cwd)
        now = _now_iso()
        with _connect(db_path) as conn:
            for key, value in [
                ("work_context", ctx.work_context),
                ("top_of_mind", ctx.top_of_mind),
                ("updated_at", now),
            ]:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_context (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, now),
                )

    # ── Experiences ───────────────────────────────────────────────────────

    async def get_experiences(self, task_type: str, limit: int = 10) -> list[Experience]:
        db_path = self._ensure_experiences_db()
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, task_type, context, insight, outcome, tags, timestamp, project
                FROM experiences
                WHERE task_type = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (task_type, limit),
            ).fetchall()
        return [_row_to_experience(r) for r in rows]

    async def store_experience(self, exp: Experience) -> None:
        db_path = self._ensure_experiences_db()
        with _lock_guard():
            with _connect(db_path) as conn:
                # 超出上限时删除最旧的
                count = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
                if count >= self._max_experiences:
                    conn.execute(
                        "DELETE FROM experiences WHERE id IN "
                        "(SELECT id FROM experiences ORDER BY timestamp ASC LIMIT ?)",
                        (count - self._max_experiences + 1,),
                    )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experiences
                    (id, task_type, context, insight, outcome, tags, timestamp, project)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exp.id or str(uuid.uuid4()),
                        exp.task_type,
                        exp.context,
                        exp.insight,
                        exp.outcome,
                        json.dumps(exp.tags, ensure_ascii=False),
                        exp.timestamp or _now_iso(),
                        exp.project,
                    ),
                )

    def get_knowledge_graph(self, cwd: str):
        from .knowledge_graph import KnowledgeGraph
        with _kg_lock:
            if cwd not in _kg_cache:
                _kg_cache[cwd] = KnowledgeGraph(cwd)
            return _kg_cache[cwd]

    async def close(self) -> None:
        pass  # SQLite connections are closed per-operation


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        content=row["content"],
        category=row["category"],
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
        source=row["source"],
        source_error=row["source_error"],
    )


def _row_to_experience(row: sqlite3.Row) -> Experience:
    tags: list[str] = []
    try:
        tags = json.loads(row["tags"])
    except Exception:
        pass
    return Experience(
        id=row["id"],
        task_type=row["task_type"],
        context=row["context"],
        insight=row["insight"],
        outcome=row["outcome"],
        tags=tags,
        timestamp=row["timestamp"],
        project=row["project"],
    )


from contextlib import contextmanager as _cm


@_cm
def _lock_guard() -> Generator[None, None, None]:
    with _LOCK:
        yield


# ── Migration tool ────────────────────────────────────────────────────────────


def migrate_from_json(cwd: str) -> int:
    """从 memory.json 批量迁移 facts 到 SQLite，返回迁移条数。

    Usage::

        from prax.core.memory.sqlite_backend import migrate_from_json
        count = migrate_from_json("/path/to/project")
        print(f"Migrated {count} facts")
    """
    import asyncio

    json_path = Path(cwd) / ".prax" / "memory.json"
    if not json_path.exists():
        logger.info("migrate_from_json: %s not found, nothing to migrate", json_path)
        return 0

    data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    facts_raw: list[dict[str, Any]] = data.get("facts", [])

    backend = SQLiteMemoryBackend()
    count = 0

    async def _do_migrate() -> int:
        nonlocal count
        for raw in facts_raw:
            fact = Fact.from_dict(raw)
            await backend.store_fact(cwd, fact)
            count += 1
        return count

    asyncio.run(_do_migrate())
    logger.info("migrate_from_json: migrated %d facts from %s", count, json_path)
    return count
