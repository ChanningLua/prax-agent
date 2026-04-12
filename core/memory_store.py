"""Persistent memory store for cross-session knowledge accumulation."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .persistence import atomic_write_json


MEMORY_SCHEMA_VERSION = "prax.memory.v1"


@dataclass
class Fact:
    """Individual fact with metadata."""
    id: str
    content: str
    category: str = "context"  # preference/knowledge/context/behavior/goal/correction
    confidence: float = 0.5  # 0.0 to 1.0
    createdAt: str = ""
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "confidence": self.confidence,
            "createdAt": self.createdAt,
            "source": self.source,
        }


@dataclass
class MemoryEntry:
    """Structured memory entry for persistent storage."""
    schema_version: str = MEMORY_SCHEMA_VERSION
    workContext: str = ""  # Project background (low-frequency updates)
    topOfMind: str = ""    # Current most important items (per-session updates)
    facts: list[dict[str, Any]] = field(default_factory=list)  # Structured facts with metadata


class MemoryStore:
    """Manages persistent memory storage in .prax/memory.json."""

    def __init__(self, cwd: str):
        self.cwd = Path(cwd)
        self.memory_dir = self.cwd / ".prax"
        self.memory_file = self.memory_dir / "memory.json"

    def load(self) -> MemoryEntry:
        """Load memory from disk, return empty entry if not found."""
        if not self.memory_file.exists():
            return MemoryEntry()

        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Support both old format (list of strings) and new format (list of dicts)
                facts_data = data.get("facts", [])
                if facts_data and isinstance(facts_data[0], str):
                    # Migrate old format to new format
                    facts_data = self._migrate_facts(facts_data)
                return MemoryEntry(
                    schema_version=str(data.get("schema_version", MEMORY_SCHEMA_VERSION)),
                    workContext=data.get("workContext", ""),
                    topOfMind=data.get("topOfMind", ""),
                    facts=facts_data
                )
        except Exception:
            return MemoryEntry()

    def _migrate_facts(self, old_facts: list[str]) -> list[dict[str, Any]]:
        """Migrate old string-based facts to new structured format."""
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "id": f"fact_{uuid.uuid4().hex[:8]}",
                "content": fact,
                "category": "context",
                "confidence": 0.8,  # Default confidence for migrated facts
                "createdAt": now,
                "source": "migration",
            }
            for fact in old_facts
        ]

    def save(self, entry: MemoryEntry) -> None:
        """Save memory to disk."""
        # Ensure directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        try:
            atomic_write_json(self.memory_file, asdict(entry))
        except Exception:
            # Silent failure - don't disrupt main flow
            pass

    def format_for_prompt(self, max_facts: int = 100) -> str:
        """Format memory for injection into system prompt.

        Returns empty string if no memory exists.
        Limits facts based on confidence scoring (default 100 items).
        """
        entry = self.load()

        # Return empty if no content
        if not entry.workContext and not entry.topOfMind and not entry.facts:
            return ""

        parts = ["## Persistent Memory"]

        if entry.workContext:
            parts.append(f"\n### Work Context\n{entry.workContext}")

        if entry.topOfMind:
            parts.append(f"\n### Top of Mind\n{entry.topOfMind}")

        if entry.facts:
            # Sort by confidence (descending) and limit
            sorted_facts = sorted(
                entry.facts,
                key=lambda f: f.get("confidence", 0.5),
                reverse=True
            )[:max_facts]

            # Format facts with category and confidence
            facts_text = []
            for fact in sorted_facts:
                content = fact.get("content", "")
                category = fact.get("category", "context")
                confidence = fact.get("confidence", 0.5)
                facts_text.append(f"- [{category}] {content} (confidence: {confidence:.2f})")

            parts.append(f"\n### Facts\n" + "\n".join(facts_text))

        return "\n".join(parts)

    def add_fact(
        self,
        content: str,
        category: str = "context",
        confidence: float = 0.5,
        source: str = "unknown"
    ) -> None:
        """Add a new fact to memory with deduplication.

        Args:
            content: Fact content
            category: Fact category (preference/knowledge/context/behavior/goal/correction)
            confidence: Confidence score (0.0 to 1.0)
            source: Source identifier (thread_id or "manual")
        """
        entry = self.load()

        # Normalize content for deduplication
        normalized_content = content.strip()
        if not normalized_content:
            return

        # Check for duplicates using normalized comparison
        existing_keys = {
            self._normalize_fact_key(f.get("content", ""))
            for f in entry.facts
        }

        fact_key = self._normalize_fact_key(normalized_content)
        if fact_key in existing_keys:
            return  # Skip duplicate

        # Create new fact
        now = datetime.now(timezone.utc).isoformat()
        new_fact = {
            "id": f"fact_{uuid.uuid4().hex[:8]}",
            "content": normalized_content,
            "category": category,
            "confidence": max(0.0, min(1.0, confidence)),  # Clamp to [0, 1]
            "createdAt": now,
            "source": source,
        }

        entry.facts.append(new_fact)
        self.save(entry)

    def _normalize_fact_key(self, content: str) -> str:
        """Normalize fact content for deduplication (whitespace-normalized)."""
        return content.strip().lower()
