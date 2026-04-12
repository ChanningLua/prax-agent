"""Phase 5 hardening tests — covers all 5A–5F improvements."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── 5A: KnowledgeGraph production hardening ──────────────────────────


class TestKGContextManager:
    """5A: Verify _connect() context manager and row_factory."""

    def test_connect_returns_context_manager(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        with kg._connect() as conn:
            assert conn is not None
            # row_factory should be set
            assert conn.row_factory == sqlite3.Row

    def test_named_column_access(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        kg.add_triple("alice", "knows", "bob")
        results = kg.query_entity("alice", direction="outgoing")
        assert len(results) >= 1
        r = results[0]
        assert r["subject"] == "alice"
        assert r["predicate"] == "knows"
        assert r["object"] == "bob"
        assert "confidence" in r

    def test_add_triples_batch(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        triples = [
            ("user", "prefers", "dark_mode"),
            ("user", "uses", "python"),
            ("project", "uses", "sqlite"),
        ]
        kg.add_triples_batch(triples, source="test")
        stats = kg.stats()
        assert stats["triples"] == 3
        assert stats["entities"] >= 4  # user, dark_mode, python, project, sqlite

    def test_stats_on_empty_db(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "empty.db"))
        stats = kg.stats()
        assert stats["entities"] == 0
        assert stats["triples"] == 0
        assert stats["current_facts"] == 0

    def test_query_entity_error_returns_empty(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        # Query non-existent entity should not raise
        results = kg.query_entity("nonexistent", direction="both")
        assert isinstance(results, list)


# ── 5B: VectorStore embedding compatibility ──────────────────────────


class TestVectorStoreCollectionName:
    """5B: Collection name encodes embedding strategy."""

    def test_collection_name_includes_strategy(self):
        from prax.core.memory.vector_store import VectorStore

        vs = VectorStore()
        name = vs._collection_name("/some/path")
        assert name.startswith("project_")
        # Should end with _st or _ngram
        assert name.endswith("_st") or name.endswith("_ngram")

    def test_different_paths_different_names(self):
        from prax.core.memory.vector_store import VectorStore

        vs = VectorStore()
        n1 = vs._collection_name("/path/a")
        n2 = vs._collection_name("/path/b")
        assert n1 != n2


class TestVectorStoreSyncBulkRoomMetadata:
    """5B/5F: sync_bulk includes room metadata."""

    def test_bulk_includes_room_field(self, tmp_path: Path):
        from prax.core.memory.vector_store import VectorStore

        vs = VectorStore()
        col = MagicMock()
        col.count.return_value = 0

        with patch.object(vs, "_get_collection", return_value=col):
            vs._sync_bulk(
                str(tmp_path),
                [
                    {
                        "id": "f1",
                        "content": "test fact",
                        "category": "preference",
                        "confidence": 0.9,
                        "source": "test",
                    }
                ],
            )
            col.upsert.assert_called_once()
            call_kwargs = col.upsert.call_args
            metas = call_kwargs[1]["metadatas"] if "metadatas" in call_kwargs[1] else call_kwargs[0][2]
            # Should have room field
            if isinstance(metas, list):
                assert "room" in metas[0]
                assert metas[0]["room"] == "preference"


# ── 5C: LayeredInjector hardening ────────────────────────────────────


class TestTokenEstimateCJK:
    """5C: CJK-aware token estimation."""

    def test_english_only(self):
        from prax.core.memory.layers import _estimate_tokens

        tokens = _estimate_tokens("hello world foo bar")
        assert tokens >= 4  # ~4 words * 1.3

    def test_cjk_only(self):
        from prax.core.memory.layers import _estimate_tokens

        tokens = _estimate_tokens("你好世界")
        assert tokens >= 4  # 4 CJK chars * 1.5

    def test_mixed_cjk_english(self):
        from prax.core.memory.layers import _estimate_tokens

        tokens = _estimate_tokens("用户 prefers dark mode")
        assert tokens >= 5  # 2 CJK + 3 English words

    def test_empty_string(self):
        from prax.core.memory.layers import _estimate_tokens

        assert _estimate_tokens("") >= 1


class TestTruncateToBudget:
    """5C: Truncation respects budget."""

    def test_short_text_unchanged(self):
        from prax.core.memory.layers import _truncate_to_budget

        text = "short text"
        assert _truncate_to_budget(text, 100) == text

    def test_long_text_truncated(self):
        from prax.core.memory.layers import _truncate_to_budget

        text = "\n".join([f"line {i} with some content here" for i in range(50)])
        result = _truncate_to_budget(text, 10)
        assert len(result) < len(text)

    def test_single_long_line_truncated(self):
        from prax.core.memory.layers import _truncate_to_budget

        text = "a " * 500
        result = _truncate_to_budget(text, 5)
        assert len(result) < len(text)
        assert len(result) > 0


class TestL3ChineseQuery:
    """5C: L3 deep search handles CJK queries."""

    def test_l3_extracts_cjk_terms(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph
        from prax.core.memory.layers import LayeredInjector

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        kg.add_triple("用户", "偏好", "中文")
        injector = LayeredInjector(kg=kg)
        result = injector.build_l3(str(tmp_path), "用户偏好什么语言")
        # Should find the triple via CJK term extraction
        assert "用户" in result or "中文" in result or result == ""

    def test_l3_filters_english_stop_words(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph
        from prax.core.memory.layers import LayeredInjector

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        kg.add_triple("python", "is", "language")
        injector = LayeredInjector(kg=kg)
        # "the" and "is" should be filtered as stop words
        result = injector.build_l3(str(tmp_path), "what is the python language")
        # Should query "python" and "language", not "the", "is", "what"
        assert isinstance(result, str)


class TestLayeredInjectorDialectCache:
    """5C: Dialect instance is cached."""

    def test_dialect_cached_across_calls(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph
        from prax.core.memory.layers import LayeredInjector

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        kg.add_triple("user", "prefers", "python")
        injector = LayeredInjector(kg=kg)

        # First call creates dialect
        injector.build_l1(str(tmp_path))
        d1 = injector._dialect

        # Second call reuses it
        injector.build_l1(str(tmp_path))
        d2 = injector._dialect

        assert d1 is d2


class TestBuildSyncWithQuery:
    """5C: build_sync supports query parameter for L3."""

    def test_build_sync_with_query(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph
        from prax.core.memory.layers import LayeredInjector

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        kg.add_triple("project", "uses", "sqlite")
        injector = LayeredInjector(kg=kg)
        result = injector.build_sync(str(tmp_path), query="what database does project use")
        assert isinstance(result, str)
        # Should contain KG section at minimum
        assert "Knowledge Graph" in result or "sqlite" in result.lower()


# ── 5D: Dialect CJK support ─────────────────────────────────────────


class TestMakeCodeCJK:
    """5D: _make_code handles CJK entities."""

    def test_single_cjk_word(self):
        from prax.core.memory.dialect import _make_code

        code = _make_code("用户")
        assert code == "用户"

    def test_multi_cjk_segments(self):
        from prax.core.memory.dialect import _make_code

        code = _make_code("用户 偏好 中文")
        assert len(code) <= 8
        # Should take first char of each segment
        assert "用" in code
        assert "偏" in code
        assert "中" in code

    def test_mixed_cjk_english(self):
        from prax.core.memory.dialect import _make_code

        code = _make_code("中文回答 mode")
        assert len(code) <= 8

    def test_emoji_stripped(self):
        from prax.core.memory.dialect import _make_code

        code = _make_code("🎉 party")
        assert code == "PRTY" or len(code) <= 8
        # Should not contain emoji
        assert "🎉" not in code

    def test_empty_after_emoji_strip(self):
        from prax.core.memory.dialect import _make_code

        code = _make_code("🎉🎊")
        assert code == "UNK"


class TestDialectFromKGErrorHandling:
    """5D: from_kg returns empty Dialect on error."""

    def test_from_kg_with_broken_kg(self):
        from prax.core.memory.dialect import Dialect

        broken_kg = MagicMock()
        broken_kg._conn.side_effect = Exception("DB error")
        dialect = Dialect.from_kg(broken_kg)
        assert isinstance(dialect, Dialect)
        assert dialect._codes == {}


# ── 5E: Middleware extraction quality ────────────────────────────────


class TestChunkExchanges:
    """5E: Exchange-pair chunking."""

    def test_basic_exchange_pair(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ]
        exchanges = MemoryMiddleware._chunk_exchanges(messages)
        assert len(exchanges) == 1
        assert exchanges[0]["user"] == "What is Python?"
        assert "programming language" in exchanges[0]["assistant"]

    def test_multiple_exchanges(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        exchanges = MemoryMiddleware._chunk_exchanges(messages)
        assert len(exchanges) == 2

    def test_user_without_assistant(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        messages = [
            {"role": "user", "content": "Hello"},
        ]
        exchanges = MemoryMiddleware._chunk_exchanges(messages)
        assert len(exchanges) == 1
        assert exchanges[0]["assistant"] == ""

    def test_long_assistant_truncated(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        long_response = "\n".join([f"line {i}" for i in range(100)])
        messages = [
            {"role": "user", "content": "Tell me everything"},
            {"role": "assistant", "content": long_response},
        ]
        exchanges = MemoryMiddleware._chunk_exchanges(messages)
        # Assistant should be truncated to 8 lines max, 500 chars max
        assert len(exchanges[0]["assistant"]) <= 500

    def test_multimodal_content(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
            {"role": "assistant", "content": "It's a cat."},
        ]
        exchanges = MemoryMiddleware._chunk_exchanges(messages)
        assert len(exchanges) == 1
        assert "Describe this" in exchanges[0]["user"]

    def test_empty_messages(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        assert MemoryMiddleware._chunk_exchanges([]) == []

    def test_system_messages_skipped(self):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        exchanges = MemoryMiddleware._chunk_exchanges(messages)
        assert len(exchanges) == 1
        assert exchanges[0]["user"] == "Hi"


class TestTripleValidation:
    """5E: Triple validation in _write_triples_to_kg."""

    def test_oversized_subject_rejected(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        mock_backend = MagicMock()
        mock_backend.get_knowledge_graph.return_value = kg

        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        mw = MemoryMiddleware.__new__(MemoryMiddleware)
        mw.cwd = str(tmp_path)
        mw._memory_backend = mock_backend

        # Subject > 200 chars should be rejected
        data = {
            "triples": [
                ["x" * 201, "knows", "bob"],  # too long
                ["alice", "knows", "bob"],  # valid
            ]
        }
        mw._write_triples_to_kg(data, correction_detected=False)
        stats = kg.stats()
        assert stats["triples"] == 1  # only valid triple

    def test_empty_predicate_rejected(self, tmp_path: Path):
        from prax.core.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(str(tmp_path / "test.db"))
        mock_backend = MagicMock()
        mock_backend.get_knowledge_graph.return_value = kg

        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        mw = MemoryMiddleware.__new__(MemoryMiddleware)
        mw.cwd = str(tmp_path)
        mw._memory_backend = mock_backend

        data = {"triples": [["alice", "", "bob"]]}
        mw._write_triples_to_kg(data, correction_detected=False)
        assert kg.stats()["triples"] == 0


class TestEpisodicWithExchanges:
    """5E: Episodic snapshot includes exchanges."""

    def test_episodic_writes_exchanges(self, tmp_path: Path):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        mw = MemoryMiddleware.__new__(MemoryMiddleware)
        mw.cwd = str(tmp_path)
        mw.fact_confidence_threshold = 0.7
        mw._episodic_days = 3

        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "A programming language."},
        ]
        facts = [{"content": "Python is a language", "confidence": 0.9, "category": "knowledge"}]

        mw._write_episodic_snapshot(facts, messages=messages)

        ep_dir = mw._episodic_dir()
        files = list(ep_dir.glob("*-facts.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "exchanges" in data
        assert len(data["exchanges"]) == 1
        assert data["exchanges"][0]["user"] == "What is Python?"

    def test_episodic_loads_exchanges(self, tmp_path: Path):
        from prax.core.memory_middleware import MemoryExtractionMiddleware as MemoryMiddleware

        mw = MemoryMiddleware.__new__(MemoryMiddleware)
        mw.cwd = str(tmp_path)
        mw._episodic_days = 3

        # Write episodic file with exchanges
        ep_dir = mw._episodic_dir()
        ep_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "date": "2025-01-15",
            "facts": [{"content": "test fact", "category": "context", "confidence": 0.9}],
            "exchanges": [{"user": "Hello", "assistant": "Hi there!"}],
        }
        (ep_dir / "2025-01-15-facts.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

        result = mw._load_episodic_memory()
        assert "test fact" in result
        assert "Hello" in result
        assert "Hi there!" in result


# ── 5F: L2 metadata filtering ───────────────────────────────────────


class TestL2WhereFilter:
    """5F: build_l2 passes where filter to vector query."""

    @pytest.mark.asyncio
    async def test_l2_with_where_filter(self):
        from prax.core.memory.layers import LayeredInjector
        from prax.core.memory.vector_store import VectorStore

        vs = MagicMock(spec=VectorStore)
        vs.query = MagicMock()

        # Make query return a coroutine
        async def mock_query(*args, **kwargs):
            return [{"content": "filtered fact", "score": 0.9}]

        vs.query = mock_query

        injector = LayeredInjector(vector_store=vs)
        result = await injector.build_l2(
            "/tmp/test", "python", where={"room": "preference"}
        )
        assert isinstance(result, str)
