"""Full coverage tests for prax/core/memory/vector_store.py.

All ChromaDB and SentenceTransformer calls are mocked.  No real I/O.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.core.memory.vector_store import (
    VectorStore,
    _ngram_embedding,
    _embed_texts,
    get_embedding_info,
    get_vector_store,
    reset_vector_store,
)


# ── _ngram_embedding ──────────────────────────────────────────────────────────


def test_ngram_embedding_returns_256_dim_vector() -> None:
    vec = _ngram_embedding("hello world")
    assert len(vec) == 256


def test_ngram_embedding_is_unit_length() -> None:
    vec = _ngram_embedding("test text")
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6


def test_ngram_embedding_very_short_text() -> None:
    """Short text (< 3 chars) falls back to individual characters."""
    vec = _ngram_embedding("ab")
    assert len(vec) == 256


def test_ngram_embedding_empty_fallback() -> None:
    """Empty string uses '<empty>' sentinel."""
    vec = _ngram_embedding("")
    assert len(vec) == 256
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6


def test_ngram_embedding_different_texts_differ() -> None:
    vec1 = _ngram_embedding("hello world")
    vec2 = _ngram_embedding("completely different")
    assert vec1 != vec2


# ── _embed_texts with env override ───────────────────────────────────────────


def test_embed_texts_uses_ngram_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("PRAX_EMBEDDING", "ngram")
    vecs = _embed_texts(["alpha", "beta"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 256


def test_embed_texts_falls_back_to_ngram_when_st_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("PRAX_EMBEDDING", raising=False)
    with patch("prax.core.memory.vector_store._get_st_model", return_value=None):
        vecs = _embed_texts(["test"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 256


# ── get_embedding_info ────────────────────────────────────────────────────────


def test_get_embedding_info_ngram_strategy(monkeypatch) -> None:
    monkeypatch.setenv("PRAX_EMBEDDING", "ngram")
    info = get_embedding_info()
    assert info["strategy"] == "ngram"
    assert info["dimensions"] == 256


def test_get_embedding_info_st_strategy(monkeypatch) -> None:
    monkeypatch.delenv("PRAX_EMBEDDING", raising=False)
    mock_model = MagicMock()
    with (
        patch("prax.core.memory.vector_store._use_sentence_transformer", return_value=True),
        patch("prax.core.memory.vector_store._get_st_model", return_value=mock_model),
    ):
        info = get_embedding_info()
    assert info["strategy"] == "sentence_transformer"
    assert info["dimensions"] == 384


# ── VectorStore._get_client — chromadb not installed ─────────────────────────


def test_vector_store_get_client_chromadb_missing(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    with patch.dict("sys.modules", {"chromadb": None}):
        client = store._get_client()
    # Should return None gracefully
    assert client is None


# ── VectorStore._get_collection — returns None when client is None ────────────


def test_vector_store_get_collection_no_client(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    store._client = None
    with patch.object(store, "_get_client", return_value=None):
        col = store._get_collection("/some/cwd")
    assert col is None


# ── VectorStore.add_fact ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_fact_calls_upsert(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    with patch.object(store, "_get_collection", return_value=mock_col):
        await store.add_fact("/proj", "fact-1", "test content", {"tag": "x"})
    mock_col.upsert.assert_called_once_with(
        ids=["fact-1"],
        documents=["test content"],
        metadatas=[{"tag": "x"}],
    )


@pytest.mark.asyncio
async def test_add_fact_noop_when_no_collection(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    with patch.object(store, "_get_collection", return_value=None):
        # Should not raise
        await store.add_fact("/proj", "fact-1", "content", {})


# ── VectorStore.query ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_returns_empty_when_no_collection(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    with patch.object(store, "_get_collection", return_value=None):
        results = await store.query("/proj", "test query")
    assert results == []


@pytest.mark.asyncio
async def test_query_returns_empty_when_collection_empty(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    mock_col.count.return_value = 0
    with patch.object(store, "_get_collection", return_value=mock_col):
        results = await store.query("/proj", "test query")
    assert results == []


@pytest.mark.asyncio
async def test_query_returns_results_above_min_score(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    mock_col.count.return_value = 2
    mock_col.query.return_value = {
        "ids": [["fact-1", "fact-2"]],
        "documents": [["content one", "content two"]],
        "metadatas": [[{"category": "context"}, {"category": "preference"}]],
        "distances": [[0.1, 0.9]],  # scores = 0.9, 0.1
    }
    with patch.object(store, "_get_collection", return_value=mock_col):
        results = await store.query("/proj", "query", n_results=2, min_score=0.5)
    # Only fact-1 has score 0.9 >= 0.5; fact-2 has score 0.1 < 0.5
    assert len(results) == 1
    assert results[0]["id"] == "fact-1"
    assert results[0]["score"] == 0.9


@pytest.mark.asyncio
async def test_query_passes_where_filter(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    mock_col.count.return_value = 1
    mock_col.query.return_value = {
        "ids": [["f1"]],
        "documents": [["doc"]],
        "metadatas": [[{}]],
        "distances": [[0.0]],
    }
    where = {"category": "preference"}
    with patch.object(store, "_get_collection", return_value=mock_col):
        await store.query("/proj", "q", where=where)
    call_kwargs = mock_col.query.call_args[1]
    assert call_kwargs["where"] == where


# ── VectorStore.delete_fact ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_fact_calls_col_delete(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    with patch.object(store, "_get_collection", return_value=mock_col):
        await store.delete_fact("/proj", "fact-99")
    mock_col.delete.assert_called_once_with(ids=["fact-99"])


@pytest.mark.asyncio
async def test_delete_fact_noop_when_no_collection(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    with patch.object(store, "_get_collection", return_value=None):
        await store.delete_fact("/proj", "fact-99")  # should not raise


# ── VectorStore.sync_from_facts ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_from_facts_calls_upsert(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    facts = [
        {"id": "f1", "content": "fact one", "category": "context", "confidence": 0.9, "source": "test"},
        {"id": "f2", "content": "fact two", "category": "preference", "confidence": 0.8, "source": "test"},
    ]
    with patch.object(store, "_get_collection", return_value=mock_col):
        await store.sync_from_facts("/proj", facts)
    mock_col.upsert.assert_called_once()
    call_kwargs = mock_col.upsert.call_args[1]
    assert call_kwargs["ids"] == ["f1", "f2"]
    assert call_kwargs["documents"] == ["fact one", "fact two"]


@pytest.mark.asyncio
async def test_sync_from_facts_empty_list_is_noop(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    mock_col = MagicMock()
    with patch.object(store, "_get_collection", return_value=mock_col):
        await store.sync_from_facts("/proj", [])
    mock_col.upsert.assert_not_called()


# ── VectorStore.close ─────────────────────────────────────────────────────────


def test_vector_store_close_resets_client(tmp_path) -> None:
    store = VectorStore(persist_dir=tmp_path)
    store._client = MagicMock()
    store.close()
    assert store._client is None


# ── get_vector_store singleton ────────────────────────────────────────────────


def test_get_vector_store_returns_singleton(tmp_path) -> None:
    reset_vector_store()
    s1 = get_vector_store(tmp_path)
    s2 = get_vector_store(tmp_path)
    assert s1 is s2
    reset_vector_store()


def test_reset_vector_store_allows_new_instance(tmp_path) -> None:
    reset_vector_store()
    s1 = get_vector_store(tmp_path)
    reset_vector_store()
    s2 = get_vector_store(tmp_path)
    assert s1 is not s2
    reset_vector_store()
