"""Tests for Batch 3 persistent memory system."""
import json
import tempfile
from pathlib import Path

import pytest

from prax.core.memory_store import MemoryStore, MemoryEntry
from prax.core.context import Context


class TestMemoryStore:
    """Tests for MemoryStore."""

    def test_save_and_load(self):
        """Test save/load round trip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(tmpdir)
            entry = MemoryEntry(
                workContext="Test project",
                topOfMind="Current task",
                facts=["Fact 1", "Fact 2"]
            )

            store.save(entry)
            loaded = store.load()
            payload = json.loads((Path(tmpdir) / ".prax" / "memory.json").read_text(encoding="utf-8"))

            assert loaded.workContext == "Test project"
            assert loaded.topOfMind == "Current task"
            assert payload["schema_version"] == "prax.memory.v1"
            fact_contents = [
                f["content"] if isinstance(f, dict) else f for f in loaded.facts
            ]
            assert fact_contents == ["Fact 1", "Fact 2"]

    def test_load_empty_returns_default(self):
        """Test loading from non-existent file returns empty entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(tmpdir)
            entry = store.load()

            assert entry.workContext == ""
            assert entry.topOfMind == ""
            assert entry.facts == []

    def test_format_for_prompt_empty(self):
        """Test formatting empty memory returns empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(tmpdir)
            formatted = store.format_for_prompt()

            assert formatted == ""

    def test_format_for_prompt_with_content(self):
        """Test formatting memory with content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(tmpdir)
            entry = MemoryEntry(
                workContext="Project context",
                topOfMind="Current focus",
                facts=["Fact 1", "Fact 2"]
            )
            store.save(entry)

            formatted = store.format_for_prompt()

            assert "## Persistent Memory" in formatted
            assert "Project context" in formatted
            assert "Current focus" in formatted
            assert "Fact 1" in formatted
            assert "Fact 2" in formatted

    def test_format_limits_facts_to_20(self):
        """Test that formatting limits facts to 20 items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(tmpdir)
            facts = [f"Fact {i}" for i in range(30)]
            entry = MemoryEntry(facts=facts)
            store.save(entry)

            formatted = store.format_for_prompt(max_facts=20)

            # Should only include first 20 facts
            assert "Fact 0" in formatted
            assert "Fact 19" in formatted
            assert "Fact 20" not in formatted


class TestContextMemoryIntegration:
    """Tests for memory integration with Context."""

    def test_context_includes_memory(self):
        """Test that context system prompt includes memory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create memory
            store = MemoryStore(tmpdir)
            entry = MemoryEntry(
                workContext="Test project",
                facts=["Important fact"]
            )
            store.save(entry)

            # Build context
            context = Context(cwd=tmpdir)
            prompt = context.build_system_prompt()

            assert "Persistent Memory" in prompt
            assert "Test project" in prompt
            assert "Important fact" in prompt
