"""Temporal Knowledge Graph for Prax memory system.

Entity-relationship graph with temporal validity, adapted from MemPalace.
Provides structured knowledge on top of flat facts.

Storage: {cwd}/.prax/knowledge.db (SQLite WAL mode)

Usage::

    kg = KnowledgeGraph("/path/to/project")
    kg.add_triple("user", "prefers", "Chinese language")
    kg.query_entity("user")
    kg.query_entity("user", as_of="2026-01-15")
    kg.invalidate("user", "uses", "old_framework", ended="2026-03-01")
    kg.timeline("user")
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_MAX_SUBJECT_LEN = 500
_MAX_PREDICATE_LEN = 100
_MAX_OBJECT_LEN = 500


def _kg_db_path(cwd: str) -> Path:
    return Path(cwd) / ".prax" / "knowledge.db"


def _validate_triple_input(
    subject: str, predicate: str, obj: str
) -> None:
    """Validate triple fields: non-empty and within length limits."""
    if not subject or not subject.strip():
        raise ValueError("subject must be a non-empty string")
    if not predicate or not predicate.strip():
        raise ValueError("predicate must be a non-empty string")
    if not obj or not obj.strip():
        raise ValueError("object must be a non-empty string")
    if len(subject) > _MAX_SUBJECT_LEN:
        raise ValueError(f"subject exceeds {_MAX_SUBJECT_LEN} chars")
    if len(predicate) > _MAX_PREDICATE_LEN:
        raise ValueError(f"predicate exceeds {_MAX_PREDICATE_LEN} chars")
    if len(obj) > _MAX_OBJECT_LEN:
        raise ValueError(f"object exceeds {_MAX_OBJECT_LEN} chars")


class KnowledgeGraph:
    """Time-aware entity-relationship graph backed by SQLite."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.db_path = _kg_db_path(cwd)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistent_conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT DEFAULT 'unknown',
                    properties TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS triples (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    confidence REAL DEFAULT 1.0,
                    source TEXT,
                    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (subject) REFERENCES entities(id),
                    FOREIGN KEY (object) REFERENCES entities(id)
                );

                CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
                CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_valid ON triples(valid_from, valid_to);
            """)

    @contextlib.contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that yields a connection with auto-commit/rollback."""
        if self._persistent_conn is None:
            self._persistent_conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                check_same_thread=False,
            )
            self._persistent_conn.execute("PRAGMA journal_mode=WAL")
            self._persistent_conn.row_factory = sqlite3.Row
        conn = self._persistent_conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _conn(self) -> sqlite3.Connection:
        """Legacy accessor — returns the persistent connection.

        Prefer ``_connect()`` context manager for new code.
        """
        if self._persistent_conn is None:
            self._persistent_conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                check_same_thread=False,
            )
            self._persistent_conn.execute("PRAGMA journal_mode=WAL")
            self._persistent_conn.row_factory = sqlite3.Row
        return self._persistent_conn

    @staticmethod
    def _entity_id(name: str) -> str:
        return name.lower().replace(" ", "_").replace("'", "")

    # ── Write operations ────────────────────────────────────────────────

    def add_entity(
        self, name: str, entity_type: str = "unknown", properties: dict | None = None
    ) -> str:
        if not name or not name.strip():
            raise ValueError("entity name must be a non-empty string")
        eid = self._entity_id(name)
        props = json.dumps(properties or {})
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entities (id, name, type, properties) VALUES (?, ?, ?, ?)",
                    (eid, name, entity_type, props),
                )
        except ValueError:
            raise
        except Exception as e:
            logger.warning("add_entity failed for %r: %s", name, e)
            raise
        return eid

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> str:
        _validate_triple_input(subject, predicate, obj)
        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(obj)
        pred = predicate.lower().replace(" ", "_")

        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                    (sub_id, subject),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                    (obj_id, obj),
                )

                existing = conn.execute(
                    "SELECT id FROM triples WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                    (sub_id, pred, obj_id),
                ).fetchone()

                if existing:
                    return existing["id"]

                triple_id = (
                    f"t_{sub_id}_{pred}_{obj_id}_"
                    f"{hashlib.md5(f'{valid_from}{datetime.now().isoformat()}'.encode()).hexdigest()[:8]}"
                )

                conn.execute(
                    """INSERT INTO triples (id, subject, predicate, object, valid_from, valid_to, confidence, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (triple_id, sub_id, pred, obj_id, valid_from, valid_to, confidence, source),
                )
        except ValueError:
            raise
        except Exception as e:
            logger.warning("add_triple failed for (%r, %r, %r): %s", subject, predicate, obj, e)
            raise
        return triple_id

    def add_triples_batch(
        self,
        triples: list[tuple[str, str, str]],
        source: str | None = None,
        confidence: float = 1.0,
    ) -> int:
        """Atomically add multiple triples in a single transaction.

        Returns the number of triples actually inserted (skips duplicates).
        Rolls back entirely on any failure.
        """
        if not triples:
            return 0
        count = 0
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for subject, predicate, obj in triples:
                    _validate_triple_input(subject, predicate, obj)
                    sub_id = self._entity_id(subject)
                    obj_id = self._entity_id(obj)
                    pred = predicate.lower().replace(" ", "_")

                    conn.execute(
                        "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                        (sub_id, subject),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                        (obj_id, obj),
                    )

                    existing = conn.execute(
                        "SELECT id FROM triples WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                        (sub_id, pred, obj_id),
                    ).fetchone()
                    if existing:
                        continue

                    triple_id = (
                        f"t_{sub_id}_{pred}_{obj_id}_"
                        f"{hashlib.md5(f'{datetime.now().isoformat()}{count}'.encode()).hexdigest()[:8]}"
                    )
                    conn.execute(
                        """INSERT INTO triples (id, subject, predicate, object, confidence, source)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (triple_id, sub_id, pred, obj_id, confidence, source),
                    )
                    count += 1
        except ValueError:
            raise
        except Exception as e:
            logger.warning("add_triples_batch failed: %s", e)
            raise
        return count

    def invalidate(
        self, subject: str, predicate: str, obj: str, ended: str | None = None
    ) -> None:
        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(obj)
        pred = predicate.lower().replace(" ", "_")
        ended = ended or date.today().isoformat()

        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE triples SET valid_to=? WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                    (ended, sub_id, pred, obj_id),
                )
        except Exception as e:
            logger.warning("invalidate failed: %s", e)

    # ── Query operations ────────────────────────────────────────────────

    def query_entity(
        self, name: str, as_of: str | None = None, direction: str = "outgoing"
    ) -> list[dict[str, Any]]:
        eid = self._entity_id(name)
        results: list[dict[str, Any]] = []

        try:
            with self._connect() as conn:
                if direction in ("outgoing", "both"):
                    query = (
                        "SELECT t.*, e.name as obj_name FROM triples t "
                        "JOIN entities e ON t.object = e.id WHERE t.subject = ?"
                    )
                    params: list[Any] = [eid]
                    if as_of:
                        query += (
                            " AND (t.valid_from IS NULL OR t.valid_from <= ?)"
                            " AND (t.valid_to IS NULL OR t.valid_to >= ?)"
                        )
                        params.extend([as_of, as_of])
                    for row in conn.execute(query, params).fetchall():
                        results.append({
                            "direction": "outgoing",
                            "subject": name,
                            "predicate": row["predicate"],
                            "object": row["obj_name"],
                            "valid_from": row["valid_from"],
                            "valid_to": row["valid_to"],
                            "confidence": row["confidence"],
                            "current": row["valid_to"] is None,
                        })

                if direction in ("incoming", "both"):
                    query = (
                        "SELECT t.*, e.name as sub_name FROM triples t "
                        "JOIN entities e ON t.subject = e.id WHERE t.object = ?"
                    )
                    params = [eid]
                    if as_of:
                        query += (
                            " AND (t.valid_from IS NULL OR t.valid_from <= ?)"
                            " AND (t.valid_to IS NULL OR t.valid_to >= ?)"
                        )
                        params.extend([as_of, as_of])
                    for row in conn.execute(query, params).fetchall():
                        results.append({
                            "direction": "incoming",
                            "subject": row["sub_name"],
                            "predicate": row["predicate"],
                            "object": name,
                            "valid_from": row["valid_from"],
                            "valid_to": row["valid_to"],
                            "confidence": row["confidence"],
                            "current": row["valid_to"] is None,
                        })
        except Exception as e:
            logger.warning("query_entity failed for %r: %s", name, e)

        return results

    def query_relationship(
        self, predicate: str, as_of: str | None = None
    ) -> list[dict[str, Any]]:
        pred = predicate.lower().replace(" ", "_")
        results = []

        try:
            with self._connect() as conn:
                query = """
                    SELECT t.*, s.name as sub_name, o.name as obj_name
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    JOIN entities o ON t.object = o.id
                    WHERE t.predicate = ?
                """
                params: list[Any] = [pred]
                if as_of:
                    query += (
                        " AND (t.valid_from IS NULL OR t.valid_from <= ?)"
                        " AND (t.valid_to IS NULL OR t.valid_to >= ?)"
                    )
                    params.extend([as_of, as_of])

                for row in conn.execute(query, params).fetchall():
                    results.append({
                        "subject": row["sub_name"],
                        "predicate": pred,
                        "object": row["obj_name"],
                        "valid_from": row["valid_from"],
                        "valid_to": row["valid_to"],
                        "current": row["valid_to"] is None,
                    })
        except Exception as e:
            logger.warning("query_relationship failed for %r: %s", predicate, e)

        return results

    def timeline(self, entity_name: str | None = None) -> list[dict[str, Any]]:
        rows = []
        try:
            with self._connect() as conn:
                if entity_name:
                    eid = self._entity_id(entity_name)
                    rows = conn.execute(
                        """
                        SELECT t.*, s.name as sub_name, o.name as obj_name
                        FROM triples t
                        JOIN entities s ON t.subject = s.id
                        JOIN entities o ON t.object = o.id
                        WHERE (t.subject = ? OR t.object = ?)
                        ORDER BY t.valid_from ASC NULLS LAST
                        LIMIT 100
                    """,
                        (eid, eid),
                    ).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT t.*, s.name as sub_name, o.name as obj_name
                        FROM triples t
                        JOIN entities s ON t.subject = s.id
                        JOIN entities o ON t.object = o.id
                        ORDER BY t.valid_from ASC NULLS LAST
                        LIMIT 100
                    """).fetchall()
        except Exception as e:
            logger.warning("timeline failed: %s", e)

        return [
            {
                "subject": r["sub_name"],
                "predicate": r["predicate"],
                "object": r["obj_name"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                "current": r["valid_to"] is None,
            }
            for r in rows
        ]

    def get_top_triples(
        self, limit: int = 15, min_confidence: float = 0.9
    ) -> list[dict[str, Any]]:
        """Return top-confidence current triples for L1 injection."""
        rows = []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT t.*, s.name as sub_name, o.name as obj_name
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    JOIN entities o ON t.object = o.id
                    WHERE t.valid_to IS NULL AND t.confidence >= ?
                    ORDER BY t.confidence DESC
                    LIMIT ?
                """,
                    (min_confidence, limit),
                ).fetchall()
        except Exception as e:
            logger.warning("get_top_triples failed: %s", e)

        return [
            {
                "subject": r["sub_name"],
                "predicate": r["predicate"],
                "object": r["obj_name"],
                "confidence": r["confidence"],
                "valid_from": r["valid_from"],
            }
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                triples = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
                current = conn.execute(
                    "SELECT COUNT(*) FROM triples WHERE valid_to IS NULL"
                ).fetchone()[0]
                expired = triples - current
                predicates = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT predicate FROM triples ORDER BY predicate"
                    ).fetchall()
                ]
        except Exception as e:
            logger.warning("stats failed: %s", e)
            return {"entities": 0, "triples": 0, "current_facts": 0, "expired_facts": 0, "relationship_types": []}

        return {
            "entities": entities,
            "triples": triples,
            "current_facts": current,
            "expired_facts": expired,
            "relationship_types": predicates,
        }
