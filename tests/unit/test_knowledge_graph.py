"""Tests for KnowledgeGraph — temporal entity-relationship graph."""

import tempfile

import pytest

from prax.core.memory.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraph(str(tmp_path))


class TestAddAndQuery:
    def test_add_entity(self, kg):
        eid = kg.add_entity("Alice", "person", {"role": "developer"})
        assert eid == "alice"

    def test_add_triple(self, kg):
        tid = kg.add_triple("Alice", "works_on", "Prax")
        assert tid.startswith("t_alice_works_on_prax_")

    def test_duplicate_triple_returns_existing_id(self, kg):
        tid1 = kg.add_triple("Alice", "works_on", "Prax")
        tid2 = kg.add_triple("Alice", "works_on", "Prax")
        assert tid1 == tid2

    def test_auto_creates_entities(self, kg):
        kg.add_triple("Bob", "uses", "Python")
        stats = kg.stats()
        assert stats["entities"] >= 2

    def test_query_entity_outgoing(self, kg):
        kg.add_triple("user", "prefers", "Chinese language")
        kg.add_triple("user", "uses", "Python")
        results = kg.query_entity("user", direction="outgoing")
        assert len(results) == 2
        predicates = {r["predicate"] for r in results}
        assert "prefers" in predicates
        assert "uses" in predicates

    def test_query_entity_incoming(self, kg):
        kg.add_triple("Alice", "works_on", "Harness")
        kg.add_triple("Bob", "works_on", "Harness")
        results = kg.query_entity("Harness", direction="incoming")
        assert len(results) == 2
        subjects = {r["subject"] for r in results}
        assert "Alice" in subjects
        assert "Bob" in subjects

    def test_query_entity_both(self, kg):
        kg.add_triple("Alice", "works_on", "Harness")
        kg.add_triple("Harness", "uses", "Python")
        results = kg.query_entity("Harness", direction="both")
        assert len(results) == 2


class TestTemporalQueries:
    def test_as_of_filters_expired(self, kg):
        kg.add_triple("Alice", "uses", "Flask", valid_from="2024-01-01", valid_to="2024-12-31")
        kg.add_triple("Alice", "uses", "FastAPI", valid_from="2025-01-01")

        # Query as of mid-2024: only Flask
        results = kg.query_entity("Alice", as_of="2024-06-15")
        assert len(results) == 1
        assert results[0]["object"] == "Flask"

        # Query as of 2025: only FastAPI
        results = kg.query_entity("Alice", as_of="2025-06-15")
        assert len(results) == 1
        assert results[0]["object"] == "FastAPI"

    def test_as_of_includes_no_date_triples(self, kg):
        kg.add_triple("user", "prefers", "dark mode")  # no date bounds
        results = kg.query_entity("user", as_of="2026-01-01")
        assert len(results) == 1

    def test_timeline_ordered_by_valid_from(self, kg):
        kg.add_triple("Alice", "joined", "TeamA", valid_from="2023-01-01")
        kg.add_triple("Alice", "joined", "TeamB", valid_from="2024-06-01")
        kg.add_triple("Alice", "joined", "TeamC", valid_from="2025-01-01")

        tl = kg.timeline("Alice")
        assert len(tl) == 3
        assert tl[0]["object"] == "TeamA"
        assert tl[2]["object"] == "TeamC"

    def test_timeline_without_entity(self, kg):
        kg.add_triple("A", "r1", "B", valid_from="2024-01-01")
        kg.add_triple("C", "r2", "D", valid_from="2025-01-01")
        tl = kg.timeline()
        assert len(tl) == 2


class TestInvalidate:
    def test_invalidate_sets_valid_to(self, kg):
        kg.add_triple("user", "uses", "old_tool")
        kg.invalidate("user", "uses", "old_tool", ended="2026-03-01")
        results = kg.query_entity("user")
        # Still returned without as_of filter
        assert len(results) == 1
        assert results[0]["valid_to"] == "2026-03-01"
        assert results[0]["current"] is False

    def test_invalidated_not_returned_with_as_of(self, kg):
        kg.add_triple("user", "uses", "old_tool", valid_from="2025-01-01")
        kg.invalidate("user", "uses", "old_tool", ended="2026-03-01")
        results = kg.query_entity("user", as_of="2026-04-01")
        assert len(results) == 0

    def test_invalidate_allows_re_add(self, kg):
        tid1 = kg.add_triple("user", "uses", "tool")
        kg.invalidate("user", "uses", "tool", ended="2026-01-01")
        tid2 = kg.add_triple("user", "uses", "tool")
        # After invalidation, re-adding creates a new triple
        assert tid1 != tid2


class TestQueryRelationship:
    def test_query_relationship(self, kg):
        kg.add_triple("Alice", "uses", "Python")
        kg.add_triple("Bob", "uses", "Rust")
        results = kg.query_relationship("uses")
        assert len(results) == 2


class TestGetTopTriples:
    def test_returns_high_confidence_only(self, kg):
        kg.add_triple("user", "prefers", "vim", confidence=0.95)
        kg.add_triple("user", "tried", "emacs", confidence=0.5)
        results = kg.get_top_triples(limit=10, min_confidence=0.9)
        assert len(results) == 1
        assert results[0]["object"] == "vim"


class TestStats:
    def test_stats_counts(self, kg):
        kg.add_triple("A", "r", "B")
        kg.add_triple("C", "r", "D")
        kg.invalidate("A", "r", "B")
        s = kg.stats()
        assert s["entities"] == 4
        assert s["triples"] == 2
        assert s["current_facts"] == 1
        assert s["expired_facts"] == 1
        assert "r" in s["relationship_types"]


class TestMigration:
    def test_migrate_facts_to_kg(self, tmp_path):
        import json
        from prax.core.memory.migration import migrate_facts_to_kg

        memory_dir = tmp_path / ".prax"
        memory_dir.mkdir()
        memory_file = memory_dir / "memory.json"
        memory_file.write_text(json.dumps({
            "workContext": "Test project",
            "topOfMind": "",
            "facts": [
                {"content": "User prefers dark mode", "category": "preference", "confidence": 0.9},
                {"content": "Project uses Python for backend", "category": "knowledge", "confidence": 0.85},
                {"content": "The database is PostgreSQL", "category": "knowledge", "confidence": 0.9},
            ]
        }))

        count = migrate_facts_to_kg(str(tmp_path))
        assert count >= 3

        # Verify KG has data
        kg = KnowledgeGraph(str(tmp_path))
        stats = kg.stats()
        assert stats["triples"] >= 3
