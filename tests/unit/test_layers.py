"""Tests for LayeredInjector — tiered memory injection L0-L3."""

import json
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from prax.core.memory.knowledge_graph import KnowledgeGraph
from prax.core.memory.layers import (
    LayeredInjector,
    L0_BUDGET,
    L1_BUDGET,
    L2_BUDGET,
    L3_BUDGET,
    _estimate_tokens,
)
from prax.core.memory_store import MemoryStore, MemoryEntry


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temp project with memory.json."""
    prax_dir = tmp_path / ".prax"
    prax_dir.mkdir()
    memory_file = prax_dir / "memory.json"
    memory_file.write_text(json.dumps({
        "workContext": "A Python CLI tool",
        "topOfMind": "Refactoring memory system",
        "facts": [
            {"content": "User prefers Chinese", "category": "preference", "confidence": 0.95},
            {"content": "Project uses SQLite", "category": "knowledge", "confidence": 0.9},
            {"content": "Temp note about debugging", "category": "context", "confidence": 0.5},
        ]
    }))
    return str(tmp_path)


@pytest.fixture
def kg(tmp_path):
    kg = KnowledgeGraph(str(tmp_path))
    kg.add_triple("user", "prefers", "Chinese language", confidence=0.95)
    kg.add_triple("project", "uses", "SQLite", confidence=0.92)
    kg.add_triple("project", "uses", "Python", confidence=0.91)
    kg.add_triple("user", "tried", "emacs", confidence=0.5)
    return kg


@pytest.fixture
def memory_store(tmp_project):
    return MemoryStore(tmp_project)


class TestL0Identity:
    def test_l0_includes_preferences(self, tmp_project, memory_store):
        injector = LayeredInjector(memory_store=memory_store)
        l0 = injector.build_l0(tmp_project)
        assert "Chinese" in l0
        assert "Python CLI" in l0 or "A Python CLI tool" in l0

    def test_l0_within_budget(self, tmp_project, memory_store):
        injector = LayeredInjector(memory_store=memory_store)
        l0 = injector.build_l0(tmp_project)
        assert _estimate_tokens(l0) <= L0_BUDGET + 10  # small margin

    def test_l0_empty_without_store(self, tmp_project):
        injector = LayeredInjector()
        assert injector.build_l0(tmp_project) == ""


class TestL1Essential:
    def test_l1_returns_high_confidence_triples(self, tmp_project, kg):
        injector = LayeredInjector(kg=kg)
        l1 = injector.build_l1(tmp_project)
        # With dialect compression, entity names appear in codebook
        assert "chinese language" in l1.lower()
        assert "sqlite" in l1.lower()
        # Low confidence triple should not appear
        assert "emacs" not in l1.lower()

    def test_l1_within_budget(self, tmp_project, kg):
        injector = LayeredInjector(kg=kg)
        l1 = injector.build_l1(tmp_project)
        assert _estimate_tokens(l1) <= L1_BUDGET + 10

    def test_l1_empty_without_kg(self, tmp_project):
        injector = LayeredInjector()
        assert injector.build_l1(tmp_project) == ""


class TestL2OnDemand:
    @pytest.mark.asyncio
    async def test_l2_returns_vector_results(self, tmp_project):
        mock_vs = AsyncMock()
        mock_vs.query = AsyncMock(return_value=[
            {"content": "SQLite is the DB", "metadata": {"category": "knowledge"}, "score": 0.85},
        ])
        injector = LayeredInjector(vector_store=mock_vs)
        l2 = await injector.build_l2(tmp_project, "database")
        assert "SQLite" in l2

    @pytest.mark.asyncio
    async def test_l2_empty_without_query(self, tmp_project):
        mock_vs = AsyncMock()
        injector = LayeredInjector(vector_store=mock_vs)
        l2 = await injector.build_l2(tmp_project, "")
        assert l2 == ""

    @pytest.mark.asyncio
    async def test_l2_within_budget(self, tmp_project):
        mock_vs = AsyncMock()
        mock_vs.query = AsyncMock(return_value=[
            {"content": f"Fact {i} about the project", "metadata": {"category": "context"}, "score": 0.8}
            for i in range(10)
        ])
        injector = LayeredInjector(vector_store=mock_vs)
        l2 = await injector.build_l2(tmp_project, "project info")
        assert _estimate_tokens(l2) <= L2_BUDGET + 10


class TestL3DeepSearch:
    def test_l3_queries_kg_by_words(self, tmp_project, kg):
        injector = LayeredInjector(kg=kg)
        l3 = injector.build_l3(tmp_project, "user preferences")
        assert "Chinese language" in l3

    def test_l3_empty_without_kg(self, tmp_project):
        injector = LayeredInjector()
        assert injector.build_l3(tmp_project, "anything") == ""


class TestBuildSync:
    def test_fallback_to_memory_store_without_kg(self, tmp_project, memory_store):
        injector = LayeredInjector(memory_store=memory_store)
        result = injector.build_sync(tmp_project)
        # Should fall back to MemoryStore.format_for_prompt()
        assert "Persistent Memory" in result

    def test_uses_kg_when_available(self, tmp_project, kg, memory_store):
        injector = LayeredInjector(kg=kg, memory_store=memory_store)
        result = injector.build_sync(tmp_project)
        assert "## Memory" in result
        assert "Knowledge Graph" in result

    def test_sync_includes_l0_and_l1(self, tmp_project, kg, memory_store):
        injector = LayeredInjector(kg=kg, memory_store=memory_store)
        result = injector.build_sync(tmp_project)
        assert "Identity" in result
        assert "Knowledge Graph" in result


class TestBuildAsync:
    @pytest.mark.asyncio
    async def test_async_with_l2(self, tmp_project, kg, memory_store):
        mock_vs = AsyncMock()
        mock_vs.query = AsyncMock(return_value=[
            {"content": "Relevant fact", "metadata": {"category": "context"}, "score": 0.9},
        ])
        injector = LayeredInjector(kg=kg, vector_store=mock_vs, memory_store=memory_store)
        result = await injector.build_async(tmp_project, query="test query")
        assert "Relevant Facts" in result

    @pytest.mark.asyncio
    async def test_async_l3_fallback_when_l2_empty(self, tmp_project, kg, memory_store):
        mock_vs = AsyncMock()
        mock_vs.query = AsyncMock(return_value=[])
        injector = LayeredInjector(kg=kg, vector_store=mock_vs, memory_store=memory_store)
        result = await injector.build_async(tmp_project, query="user preferences")
        # L2 empty → should try L3
        assert "Deep Search" in result or "Knowledge Graph" in result

    @pytest.mark.asyncio
    async def test_async_fallback_without_kg(self, tmp_project, memory_store):
        injector = LayeredInjector(memory_store=memory_store)
        result = await injector.build_async(tmp_project, query="anything")
        assert "Persistent Memory" in result


class TestTokenBudgetComparison:
    """Compare token usage: old flat injection vs new layered injection."""

    def test_layered_l0_l1_under_600_tokens(self, tmp_project, kg, memory_store):
        injector = LayeredInjector(kg=kg, memory_store=memory_store)
        result = injector.build_sync(tmp_project)
        tokens = _estimate_tokens(result)
        # Target: L0+L1 ≤ 600 tokens
        assert tokens <= 600, f"Layered sync output is {tokens} tokens, expected ≤ 600"
