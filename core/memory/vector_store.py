"""Semantic vector store for fact retrieval.

Uses ChromaDB with either:
  1. SentenceTransformer embedding (all-MiniLM-L6-v2) — high quality, lazy-loaded
  2. Lightweight n-gram hash embedding — no model download, zero dependencies

Storage layout::

    ~/.prax/chroma/           ← global persistent ChromaDB directory
        project_<hash>/          ← one collection per project

Set PRAX_EMBEDDING=ngram to force the n-gram fallback.
Default: tries SentenceTransformer first, falls back to n-gram.

Usage::

    store = get_vector_store()
    await store.add_fact("proj_path", fact_id, fact_content, metadata)
    results = await store.query(cwd, query_text, n_results=5)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Embedding ────────────────────────────────────────────────────────────────

_DIM = 256
_NGRAM_N = 3


def _ngram_embedding(text: str) -> list[float]:
    """Character n-gram hash embedding — no external dependencies.

    Produces a 256-dim unit-length vector from character trigrams.
    Works for both English and Chinese text.
    """
    text = text.lower()
    ngrams = [text[i : i + _NGRAM_N] for i in range(len(text) - _NGRAM_N + 1)]
    if not ngrams:
        # Fallback for very short text: use individual characters
        ngrams = list(text) or ["<empty>"]

    vec = [0.0] * _DIM
    for gram in ngrams:
        h = int(hashlib.sha256(gram.encode()).hexdigest(), 16)
        vec[h % _DIM] += 1.0

    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ── SentenceTransformer embedding (lazy-loaded) ─────────────────────────────

_st_model: Any = None
_st_lock = threading.Lock()
_ST_MODEL_NAME = "all-MiniLM-L6-v2"


def _get_st_model() -> Any:
    """Lazily load SentenceTransformer model. Returns None if unavailable."""
    global _st_model
    with _st_lock:
        if _st_model is not None:
            return _st_model
        try:
            from sentence_transformers import SentenceTransformer
            _st_model = SentenceTransformer(_ST_MODEL_NAME)
            logger.info("Loaded SentenceTransformer model: %s", _ST_MODEL_NAME)
        except ImportError:
            logger.debug("sentence-transformers not installed, using ngram fallback")
            _st_model = None
        except Exception as e:
            logger.warning("Failed to load SentenceTransformer: %s", e)
            _st_model = None
    return _st_model


def _st_embedding(texts: list[str]) -> list[list[float]]:
    """Encode texts using SentenceTransformer."""
    model = _get_st_model()
    if model is None:
        return [_ngram_embedding(t) for t in texts]
    embeddings = model.encode(texts, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


def _use_sentence_transformer() -> bool:
    """Check if SentenceTransformer should be used."""
    env = os.environ.get("PRAX_EMBEDDING", "").lower()
    if env == "ngram":
        return False
    return True


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using the best available strategy."""
    if _use_sentence_transformer():
        result = _st_embedding(texts)
        # If ST returned ngram fallback (model unavailable), log it
        if _get_st_model() is None:
            logger.debug("Embedding fallback: ST→ngram")
        return result
    return [_ngram_embedding(t) for t in texts]


def get_embedding_info() -> dict[str, Any]:
    """Return info about the active embedding strategy."""
    if _use_sentence_transformer() and _get_st_model() is not None:
        return {"strategy": "sentence_transformer", "model": _ST_MODEL_NAME, "dimensions": 384}
    return {"strategy": "ngram", "model": "ngram_hash_v1", "dimensions": _DIM}


def _build_ef() -> Any:
    """Build a ChromaDB-compatible embedding function.

    Uses SentenceTransformer when available, otherwise n-gram hash.
    Tries to use the proper EmbeddingFunction base class (chromadb >= 1.5).
    Falls back to a plain callable for older versions.
    """
    try:
        from chromadb import EmbeddingFunction, Embeddings, Documents
        from chromadb.utils.embedding_functions import register_embedding_function

        @register_embedding_function
        class _PraxEF(EmbeddingFunction[Documents]):
            def __init__(self) -> None:
                pass

            @classmethod
            def name(cls) -> str:
                return "prax_embedding_v2"

            def __call__(self, input: Documents) -> Embeddings:
                return _embed_texts(list(input))  # type: ignore[return-value]

            def build_from_config(self, config: dict = {}) -> "_PraxEF":
                return _PraxEF()

            def get_config(self) -> dict:
                return {}

        return _PraxEF()
    except Exception:
        # Fallback: plain callable — works with older chromadb versions
        class _PlainEF:
            def __call__(self, input: list[str]) -> list[list[float]]:
                return _embed_texts(list(input))

        return _PlainEF()


# ── ChromaDB store ────────────────────────────────────────────────────────────


class VectorStore:
    """Semantic fact store backed by ChromaDB.

    Thread-safe; async methods run blocking ChromaDB calls in a thread pool.
    """

    def __init__(self, persist_dir: Path | None = None) -> None:
        self._persist_dir = persist_dir or Path.home() / ".prax" / "chroma"
        self._ef = _build_ef()
        self._client: Any = None
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        """Lazily initialize the ChromaDB client."""
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                import chromadb

                self._persist_dir.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self._persist_dir)
                )
                logger.debug("ChromaDB initialized at %s", self._persist_dir)
            except ImportError:
                logger.warning("chromadb not installed — vector store disabled")
                self._client = None
            except Exception as e:
                logger.warning("ChromaDB init failed: %s", e)
                self._client = None
        return self._client

    def _collection_name(self, cwd: str) -> str:
        """Stable collection name derived from project path + embedding strategy."""
        h = hashlib.sha256(cwd.encode()).hexdigest()[:12]
        strategy = "st" if _use_sentence_transformer() and _get_st_model() is not None else "ngram"
        return f"project_{h}_{strategy}"

    def _get_collection(self, cwd: str) -> Any | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            return client.get_or_create_collection(
                name=self._collection_name(cwd),
                embedding_function=self._ef,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            logger.warning("Failed to get/create collection for %s: %s", cwd, e)
            return None

    # ── Public async API ──────────────────────────────────────────────────────

    async def add_fact(
        self,
        cwd: str,
        fact_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add or update a fact in the vector store."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._sync_add_fact, cwd, fact_id, content, metadata or {}
        )

    def _sync_add_fact(
        self,
        cwd: str,
        fact_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        col = self._get_collection(cwd)
        if col is None:
            return
        try:
            # upsert: update if exists, add if not
            col.upsert(
                ids=[fact_id],
                documents=[content],
                metadatas=[metadata],
            )
        except Exception as e:
            logger.warning("VectorStore.add_fact failed: %s", e)

    async def query(
        self,
        cwd: str,
        query_text: str,
        n_results: int = 5,
        min_score: float = 0.15,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve semantically similar facts.

        Returns list of dicts with keys: id, content, metadata, score.
        Score is cosine similarity (0.0–1.0); higher is more similar.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._sync_query, cwd, query_text, n_results, min_score, where
        )

    def _sync_query(
        self,
        cwd: str,
        query_text: str,
        n_results: int,
        min_score: float,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        col = self._get_collection(cwd)
        if col is None:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            query_kwargs: dict[str, Any] = {
                "query_texts": [query_text],
                "n_results": actual_n,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                query_kwargs["where"] = where
            result = col.query(**query_kwargs)
            items = []
            docs = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            dists = result.get("distances", [[]])[0]
            ids = result.get("ids", [[]])[0]

            for doc, meta, dist, fid in zip(docs, metas, dists, ids):
                # ChromaDB cosine distance = 1 - similarity
                score = 1.0 - dist
                if score >= min_score:
                    items.append(
                        {
                            "id": fid,
                            "content": doc,
                            "metadata": meta or {},
                            "score": round(score, 4),
                        }
                    )
            return items
        except Exception as e:
            logger.warning("VectorStore.query failed: %s", e)
            return []

    async def delete_fact(self, cwd: str, fact_id: str) -> None:
        """Remove a fact from the vector store."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_delete, cwd, fact_id)

    def _sync_delete(self, cwd: str, fact_id: str) -> None:
        col = self._get_collection(cwd)
        if col is None:
            return
        try:
            col.delete(ids=[fact_id])
        except Exception as e:
            logger.warning("VectorStore.delete_fact failed: %s", e)

    async def sync_from_facts(
        self, cwd: str, facts: list[dict[str, Any]]
    ) -> None:
        """Bulk-sync a list of fact dicts into the vector store.

        Idempotent: facts already present are updated in-place (upsert).
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_bulk, cwd, facts)

    def _sync_bulk(self, cwd: str, facts: list[dict[str, Any]]) -> None:
        if not facts:
            return
        col = self._get_collection(cwd)
        if col is None:
            return
        try:
            ids = [f["id"] for f in facts]
            docs = [f.get("content", "") for f in facts]
            metas = [
                {
                    "category": f.get("category", "context"),
                    "confidence": f.get("confidence", 0.5),
                    "source": f.get("source", "unknown"),
                    "room": f.get("category", "context"),
                }
                for f in facts
            ]
            col.upsert(ids=ids, documents=docs, metadatas=metas)
            logger.debug("VectorStore synced %d facts for %s", len(facts), cwd)
        except Exception as e:
            logger.warning("VectorStore.sync_from_facts failed: %s", e)

    def close(self) -> None:
        """Release ChromaDB resources."""
        if self._client is not None:
            try:
                # chromadb PersistentClient has no explicit close in v0.4+
                self._client = None
            except Exception:
                pass


# ── Singleton ─────────────────────────────────────────────────────────────────

_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_vector_store(persist_dir: Path | None = None) -> VectorStore:
    """Return the process-wide VectorStore singleton."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            _store = VectorStore(persist_dir)
    return _store


def reset_vector_store() -> None:
    """Reset singleton (for tests)."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = None
