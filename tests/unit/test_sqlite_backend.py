"""Unit tests for SQLiteMemoryBackend (prax/core/memory/sqlite_backend.py).

Uses real SQLite in tmp_path — no network, no Docker, no real I/O outside tmp_path.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from prax.core.memory.backend import Experience, Fact, MemoryContext
from prax.core.memory.sqlite_backend import SQLiteMemoryBackend, migrate_from_json


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_experiences_db(tmp_path, monkeypatch):
    """Redirect global experiences DB to tmp_path so tests don't touch $HOME."""
    exp_dir = tmp_path / ".prax_global"
    exp_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "prax.core.memory.sqlite_backend._global_experiences_db_path",
        lambda: exp_dir / "experiences.db",
    )

@pytest.fixture
def cwd(tmp_path: Path) -> str:
    (tmp_path / ".prax").mkdir(exist_ok=True)
    return str(tmp_path)


@pytest.fixture
def backend() -> SQLiteMemoryBackend:
    # Use low threshold so test facts with confidence >= 0.5 are returned
    return SQLiteMemoryBackend(
        max_facts=100,
        fact_confidence_threshold=0.0,
        max_experiences=500,
    )


def make_fact(
    content: str = "test fact",
    category: str = "context",
    confidence: float = 0.8,
    source: str = "test",
) -> Fact:
    return Fact(
        id=str(uuid.uuid4()),
        content=content,
        category=category,
        confidence=confidence,
        created_at="2026-01-01T00:00:00+00:00",
        source=source,
        source_error="",
    )


def make_experience(task_type: str = "refactor", insight: str = "use abc") -> Experience:
    return Experience(
        id=str(uuid.uuid4()),
        task_type=task_type,
        context="project context",
        insight=insight,
        outcome="completed",
        tags=["python", "refactor"],
        timestamp="2026-01-01T00:00:00+00:00",
        project="prax",
    )


# ── store_fact + get_facts roundtrip ────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_and_get_facts_roundtrip(backend: SQLiteMemoryBackend, cwd: str) -> None:
    fact = make_fact("Python prefers explicit over implicit")
    await backend.store_fact(cwd, fact)

    facts = await backend.get_facts(cwd)
    assert len(facts) == 1
    assert facts[0].content == "Python prefers explicit over implicit"
    assert facts[0].id == fact.id


@pytest.mark.asyncio
async def test_store_multiple_facts(backend: SQLiteMemoryBackend, cwd: str) -> None:
    for i in range(5):
        await backend.store_fact(cwd, make_fact(f"fact {i}", confidence=0.5 + i * 0.1))

    facts = await backend.get_facts(cwd)
    assert len(facts) == 5


@pytest.mark.asyncio
async def test_get_facts_ordered_by_confidence(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("low confidence", confidence=0.3))
    await backend.store_fact(cwd, make_fact("high confidence", confidence=0.9))
    await backend.store_fact(cwd, make_fact("medium confidence", confidence=0.6))

    facts = await backend.get_facts(cwd)
    confidences = [f.confidence for f in facts]
    assert confidences == sorted(confidences, reverse=True)


@pytest.mark.asyncio
async def test_get_facts_respects_confidence_threshold(cwd: str) -> None:
    high_threshold_backend = SQLiteMemoryBackend(
        max_facts=100,
        fact_confidence_threshold=0.8,
    )
    await high_threshold_backend.store_fact(cwd, make_fact("low", confidence=0.5))
    await high_threshold_backend.store_fact(cwd, make_fact("high", confidence=0.9))

    facts = await high_threshold_backend.get_facts(cwd)
    assert len(facts) == 1
    assert facts[0].content == "high"


@pytest.mark.asyncio
async def test_get_facts_respects_limit(backend: SQLiteMemoryBackend, cwd: str) -> None:
    for i in range(10):
        await backend.store_fact(cwd, make_fact(f"fact {i}"))

    facts = await backend.get_facts(cwd, limit=3)
    assert len(facts) <= 3


# ── search_facts (FTS5) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_facts_by_keyword(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("Python uses indentation for blocks"))
    await backend.store_fact(cwd, make_fact("JavaScript uses curly braces"))

    results = await backend.search_facts(cwd, "Python")
    assert len(results) >= 1
    assert any("Python" in f.content for f in results)


@pytest.mark.asyncio
async def test_search_facts_no_match(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("Python is great"))

    results = await backend.search_facts(cwd, "Haskell")
    assert results == []


@pytest.mark.asyncio
async def test_search_facts_min_confidence_filter(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("Rust memory safety low", confidence=0.3))
    await backend.store_fact(cwd, make_fact("Rust memory safety high", confidence=0.9))

    results = await backend.search_facts(cwd, "Rust", min_confidence=0.7)
    assert all(f.confidence >= 0.7 for f in results)


# ── delete_fact ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_fact(backend: SQLiteMemoryBackend, cwd: str) -> None:
    fact = make_fact("to be deleted")
    await backend.store_fact(cwd, fact)

    facts = await backend.get_facts(cwd)
    assert len(facts) == 1

    await backend.delete_fact(cwd, fact.id)

    facts = await backend.get_facts(cwd)
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_fact_is_noop(backend: SQLiteMemoryBackend, cwd: str) -> None:
    # Should not raise
    await backend.delete_fact(cwd, "nonexistent-id")


# ── store/get experiences ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_and_get_experience(backend: SQLiteMemoryBackend, tmp_path: Path) -> None:
    # Redirect global experiences DB to tmp_path
    import prax.core.memory.sqlite_backend as mod
    original = mod._global_experiences_db_path

    def _patched():
        return tmp_path / ".prax" / "experiences.db"

    mod._global_experiences_db_path = _patched  # type: ignore[assignment]
    try:
        exp = make_experience(task_type="debug", insight="check null pointers")
        await backend.store_experience(exp)

        experiences = await backend.get_experiences("debug")
        assert len(experiences) == 1
        assert experiences[0].insight == "check null pointers"
    finally:
        mod._global_experiences_db_path = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_get_experiences_empty(backend: SQLiteMemoryBackend, tmp_path: Path) -> None:
    import prax.core.memory.sqlite_backend as mod
    original = mod._global_experiences_db_path

    def _patched():
        return tmp_path / ".prax" / "experiences.db"

    mod._global_experiences_db_path = _patched  # type: ignore[assignment]
    try:
        experiences = await backend.get_experiences("unknown_type")
        assert experiences == []
    finally:
        mod._global_experiences_db_path = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_get_experiences_filters_by_task_type(backend: SQLiteMemoryBackend, tmp_path: Path) -> None:
    import prax.core.memory.sqlite_backend as mod
    original = mod._global_experiences_db_path

    def _patched():
        return tmp_path / ".prax" / "experiences.db"

    mod._global_experiences_db_path = _patched  # type: ignore[assignment]
    try:
        await backend.store_experience(make_experience("debug", "use breakpoints"))
        await backend.store_experience(make_experience("refactor", "extract method"))

        debug_exps = await backend.get_experiences("debug")
        assert all(e.task_type == "debug" for e in debug_exps)

        refactor_exps = await backend.get_experiences("refactor")
        assert all(e.task_type == "refactor" for e in refactor_exps)
    finally:
        mod._global_experiences_db_path = original  # type: ignore[assignment]


# ── deduplication ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_fact_deduplication_same_content(backend: SQLiteMemoryBackend, cwd: str) -> None:
    fact1 = make_fact("duplicate content", confidence=0.6)
    fact2 = make_fact("duplicate content", confidence=0.9)

    await backend.store_fact(cwd, fact1)
    await backend.store_fact(cwd, fact2)

    facts = await backend.get_facts(cwd)
    # Same content — should only have one entry
    assert len(facts) == 1
    # Confidence should be updated to max
    assert facts[0].confidence == 0.9


@pytest.mark.asyncio
async def test_store_fact_different_content_not_deduplicated(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("content A"))
    await backend.store_fact(cwd, make_fact("content B"))

    facts = await backend.get_facts(cwd)
    assert len(facts) == 2


# ── format_for_prompt ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_format_for_prompt_empty(backend: SQLiteMemoryBackend, cwd: str) -> None:
    result = await backend.format_for_prompt(cwd)
    assert result == ""


@pytest.mark.asyncio
async def test_format_for_prompt_with_facts(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("use type hints in Python", confidence=0.85))

    result = await backend.format_for_prompt(cwd)
    assert "## Memory" in result
    assert "use type hints in Python" in result


@pytest.mark.asyncio
async def test_format_for_prompt_high_confidence_marker(backend: SQLiteMemoryBackend, cwd: str) -> None:
    await backend.store_fact(cwd, make_fact("very reliable fact", confidence=0.95))

    result = await backend.format_for_prompt(cwd)
    assert "✓" in result


@pytest.mark.asyncio
async def test_format_for_prompt_with_context(backend: SQLiteMemoryBackend, cwd: str) -> None:
    ctx = MemoryContext(work_context="Working on prax tests", top_of_mind="fix bugs")
    await backend.save_context(cwd, ctx)

    result = await backend.format_for_prompt(cwd)
    assert "Work Context" in result
    assert "Working on prax tests" in result


# ── migrate_from_json ────────────────────────────────────────────────────────


def test_migrate_from_json_no_file(tmp_path: Path) -> None:
    count = migrate_from_json(str(tmp_path))
    assert count == 0


def test_migrate_from_json_with_facts(tmp_path: Path) -> None:
    prax_dir = tmp_path / ".prax"
    prax_dir.mkdir()
    memory_json = prax_dir / "memory.json"
    facts_data = [
        {
            "id": str(uuid.uuid4()),
            "content": "migrated fact 1",
            "category": "context",
            "confidence": 0.8,
            "createdAt": "2026-01-01T00:00:00+00:00",
            "source": "migration",
        },
        {
            "id": str(uuid.uuid4()),
            "content": "migrated fact 2",
            "category": "knowledge",
            "confidence": 0.7,
            "createdAt": "2026-01-01T00:00:00+00:00",
            "source": "migration",
        },
    ]
    memory_json.write_text(json.dumps({"facts": facts_data}), encoding="utf-8")

    count = migrate_from_json(str(tmp_path))
    assert count == 2
