"""Unit tests for LocalMemoryBackend — facts, experiences, context, caching."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from prax.core.memory.backend import Experience, Fact, MemoryContext
from prax.core.memory.local_backend import LocalMemoryBackend, _global_experiences_path


# ── helpers ───────────────────────────────────────────────────────────────────

def _backend() -> LocalMemoryBackend:
    return LocalMemoryBackend()


def _fact(content="use pytest", confidence=0.9) -> Fact:
    return Fact(
        id=f"fact_{abs(hash(content))}",
        content=content,
        confidence=confidence,
        source="unit_test",
    )


def _exp(task_type="test", cwd="/tmp") -> Experience:
    return Experience(
        id=f"exp_{abs(hash(task_type + cwd))}",
        task_type=task_type,
        context="test context",
        insight="test insight",
        outcome="completed",
        tags=["test"],
        timestamp=datetime.now(timezone.utc).isoformat(),
        project=cwd,
    )


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Facts ─────────────────────────────────────────────────────────────────────

class TestLocalMemoryBackendFacts:
    def test_get_facts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            facts = run(_backend().get_facts(d))
            assert facts == []

    def test_store_and_get_fact(self):
        with tempfile.TemporaryDirectory() as d:
            b = _backend()
            run(b.store_fact(d, _fact("always use async")))
            facts = run(b.get_facts(d))
            assert any(f.content == "always use async" for f in facts)

    def test_store_multiple_facts(self):
        with tempfile.TemporaryDirectory() as d:
            b = _backend()
            for i in range(5):
                run(b.store_fact(d, _fact(f"fact content {i}", confidence=0.9)))
            facts = run(b.get_facts(d))
            assert len(facts) == 5

    def test_delete_fact(self):
        with tempfile.TemporaryDirectory() as d:
            b = _backend()
            f = _fact("to be deleted")
            run(b.store_fact(d, f))
            run(b.delete_fact(d, f.id))
            facts = run(b.get_facts(d))
            assert not any(x.id == f.id for x in facts)

    def test_delete_nonexistent_fact_noop(self):
        with tempfile.TemporaryDirectory() as d:
            run(_backend().delete_fact(d, "ghost_id"))   # must not raise

    def test_limit_respected(self):
        with tempfile.TemporaryDirectory() as d:
            b = _backend()
            for i in range(10):
                run(b.store_fact(d, _fact(f"fact num {i}")))
            facts = run(b.get_facts(d, limit=3))
            assert len(facts) <= 3

    def test_facts_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            run(_backend().store_fact(d, _fact("persistent")))
            facts = run(_backend().get_facts(d))
            assert any(f.content == "persistent" for f in facts)


# ── Experiences ───────────────────────────────────────────────────────────────

class TestLocalMemoryBackendExperiences:
    """Experiences go to ~/.prax/experiences.json — redirect to tmp in each test."""

    def test_store_and_get_experience(self):
        with tempfile.TemporaryDirectory() as exp_dir:
            exp_path = Path(exp_dir) / "experiences.json"
            with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                b = _backend()
                e = _exp("refactor", exp_dir)
                run(b.store_experience(e))
                exps = run(b.get_experiences("refactor"))
                assert any(x.id == e.id for x in exps)

    def test_get_experiences_empty(self):
        with tempfile.TemporaryDirectory() as exp_dir:
            exp_path = Path(exp_dir) / "experiences.json"
            with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                exps = run(_backend().get_experiences("nonexistent_type"))
                assert isinstance(exps, list)

    def test_experiences_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as exp_dir:
            exp_path = Path(exp_dir) / "experiences.json"
            with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                run(_backend().store_experience(_exp("feature", exp_dir)))
                exps = run(_backend().get_experiences("feature"))
                assert len(exps) >= 1

    def test_limit_on_get_experiences(self):
        with tempfile.TemporaryDirectory() as exp_dir:
            exp_path = Path(exp_dir) / "experiences.json"
            with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                b = _backend()
                for i in range(20):
                    e = _exp("load_test", f"/tmp/{i}")
                    e = Experience(
                        id=f"exp_{i}",
                        task_type="load_test",
                        context="ctx",
                        insight="ins",
                        outcome="ok",
                        tags=[],
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        project=f"/tmp/{i}",
                    )
                    run(b.store_experience(e))
                exps = run(b.get_experiences("load_test", limit=5))
                assert len(exps) <= 5


# ── MemoryContext ─────────────────────────────────────────────────────────────

class TestLocalMemoryBackendContext:
    def test_get_context_returns_default_when_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = run(_backend().get_context(d))
            assert isinstance(ctx, MemoryContext)

    def test_save_and_get_context(self):
        with tempfile.TemporaryDirectory() as d:
            b = _backend()
            ctx = MemoryContext(work_context="working on foo", top_of_mind="fix bar")
            run(b.save_context(d, ctx))
            loaded = run(b.get_context(d))
            assert loaded.work_context == "working on foo"
            assert loaded.top_of_mind == "fix bar"

    def test_context_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            run(_backend().save_context(d, MemoryContext(work_context="persistent", top_of_mind="")))
            ctx = run(_backend().get_context(d))
            assert ctx.work_context == "persistent"


# ── format_for_prompt ─────────────────────────────────────────────────────────

class TestLocalMemoryBackendPromptFormat:
    def test_returns_string(self):
        with tempfile.TemporaryDirectory() as d:
            with tempfile.TemporaryDirectory() as exp_dir:
                exp_path = Path(exp_dir) / "experiences.json"
                with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                    result = run(_backend().format_for_prompt(d))
                    assert isinstance(result, str)

    def test_empty_when_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            with tempfile.TemporaryDirectory() as exp_dir:
                exp_path = Path(exp_dir) / "experiences.json"
                with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                    result = run(_backend().format_for_prompt(d))
                    assert result == ""

    def test_includes_facts_in_output(self):
        with tempfile.TemporaryDirectory() as d:
            with tempfile.TemporaryDirectory() as exp_dir:
                exp_path = Path(exp_dir) / "experiences.json"
                with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                    b = _backend()
                    run(b.store_fact(d, _fact("always write tests")))
                    result = run(b.format_for_prompt(d))
                    assert "always write tests" in result

    def test_includes_context_in_output(self):
        with tempfile.TemporaryDirectory() as d:
            with tempfile.TemporaryDirectory() as exp_dir:
                exp_path = Path(exp_dir) / "experiences.json"
                with patch("prax.core.memory.local_backend._global_experiences_path", return_value=exp_path):
                    b = _backend()
                    run(b.save_context(d, MemoryContext(work_context="current sprint: auth", top_of_mind="")))
                    result = run(b.format_for_prompt(d))
                    assert "current sprint: auth" in result


# ── close / noop ──────────────────────────────────────────────────────────────

class TestLocalMemoryBackendClose:
    def test_close_does_not_raise(self):
        run(_backend().close())   # must be a no-op, not raise
