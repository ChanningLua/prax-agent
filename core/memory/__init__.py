"""Prax memory subsystem.

Five-layer memory model:

  project facts   — per-project structured facts extracted by LLM
                    stored in .prax/memory.json (同现有 MemoryStore 格式)

  work context    — workContext / topOfMind summary strings per project
                    stored alongside facts in memory.json

  global experiences — cross-project DeerFlow-style experience records
                    stored in ~/.prax/experiences.json

  knowledge graph — temporal entity-relationship graph (SQLite)
                    stored in .prax/knowledge_graph.db

  dialect compression — AAAK compact symbolic encoding for KG triples

Entry point::

    from prax.core.memory import get_memory_backend
    backend = get_memory_backend(config)
"""

from .backend import Experience, Fact, MemoryBackend, MemoryContext
from .factory import get_memory_backend, reset_memory_backend
from .sqlite_backend import SQLiteMemoryBackend, migrate_from_json
from .vector_store import VectorStore, get_vector_store, reset_vector_store, get_embedding_info
from .knowledge_graph import KnowledgeGraph
from .dialect import Dialect
from .layers import LayeredInjector
from .migration import migrate_facts_to_kg

__all__ = [
    "Experience",
    "Fact",
    "MemoryBackend",
    "MemoryContext",
    "get_memory_backend",
    "reset_memory_backend",
    "SQLiteMemoryBackend",
    "migrate_from_json",
    "VectorStore",
    "get_vector_store",
    "reset_vector_store",
    "get_embedding_info",
    "KnowledgeGraph",
    "Dialect",
    "LayeredInjector",
    "migrate_facts_to_kg",
]
