"""MemoryBackend — abstract base class for Prax memory backends.

All backends must implement this interface.  The factory chooses the
concrete implementation based on .prax/config.yaml [memory] section.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .knowledge_graph import KnowledgeGraph


# ── Data transfer objects ────────────────────────────────────────────────────


@dataclass
class Fact:
    """A discrete piece of knowledge extracted from a conversation.

    Schema mirrors the existing MemoryStore fact dict so the two are
    byte-compatible — no migration needed.
    """
    id: str
    content: str
    category: str = "context"   # preference|knowledge|context|behavior|goal|correction
    confidence: float = 0.5     # 0.0–1.0
    created_at: str = ""        # ISO-8601
    source: str = "unknown"     # thread_id or "manual"
    source_error: str = ""      # what was wrong (for category=correction)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "confidence": self.confidence,
            "createdAt": self.created_at,
            "source": self.source,
        }
        if self.source_error:
            d["sourceError"] = self.source_error
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Fact":
        return cls(
            id=d.get("id", ""),
            content=d.get("content", ""),
            category=d.get("category", "context"),
            confidence=float(d.get("confidence", 0.5)),
            created_at=d.get("createdAt", ""),
            source=d.get("source", "unknown"),
            source_error=d.get("sourceError", ""),
        )


@dataclass
class MemoryContext:
    """Structured summaries for a project.

    Keeps stable project-level summaries for the fields Prax uses most.
    """
    work_context: str = ""      # project background (low-frequency updates)
    top_of_mind: str = ""       # current priorities (per-session updates)
    updated_at: str = ""


@dataclass
class Experience:
    """A cross-project experience record.

    Stored globally (~/.prax/experiences.json) so insights from one
    project inform the agent on other projects.
    """
    id: str
    task_type: str              # e.g. "refactor", "debug", "implement"
    context: str                # situation description
    insight: str                # what was learned
    outcome: str                # "completed" | "partial" | "failed"
    tags: list[str] = field(default_factory=list)
    timestamp: str = ""         # ISO-8601
    project: str = ""           # cwd basename for traceability

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "context": self.context,
            "insight": self.insight,
            "outcome": self.outcome,
            "tags": self.tags,
            "timestamp": self.timestamp,
            "project": self.project,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Experience":
        return cls(
            id=d.get("id", ""),
            task_type=d.get("task_type", "general"),
            context=d.get("context", ""),
            insight=d.get("insight", ""),
            outcome=d.get("outcome", ""),
            tags=d.get("tags", []),
            timestamp=d.get("timestamp", ""),
            project=d.get("project", ""),
        )


# ── Abstract backend ─────────────────────────────────────────────────────────


class MemoryBackend(abc.ABC):
    """Abstract interface for all Prax memory backends.

    Concrete implementations:
      LocalMemoryBackend  — JSON files under .prax/ and ~/.prax/
      OpenVikingBackend   — delegates to the OpenViking gRPC service
    """

    # ── Project-level facts ───────────────────────────────────────────────

    @abc.abstractmethod
    async def get_facts(self, cwd: str, limit: int = 100) -> list[Fact]:
        """Return top-confidence facts for the given project directory."""
        ...

    @abc.abstractmethod
    async def store_fact(self, cwd: str, fact: Fact) -> None:
        """Persist a new fact.  Implementations should deduplicate."""
        ...

    @abc.abstractmethod
    async def delete_fact(self, cwd: str, fact_id: str) -> None:
        """Remove a fact by id (no-op if not found)."""
        ...

    # ── Project context (workContext / topOfMind) ─────────────────────────

    @abc.abstractmethod
    async def get_context(self, cwd: str) -> MemoryContext:
        """Return the structured summary context for a project."""
        ...

    @abc.abstractmethod
    async def save_context(self, cwd: str, ctx: MemoryContext) -> None:
        """Persist the project context."""
        ...

    # ── Global experiences ────────────────────────────────────────────────

    @abc.abstractmethod
    async def get_experiences(
        self, task_type: str, limit: int = 10
    ) -> list[Experience]:
        """Return cross-project experiences relevant to *task_type*."""
        ...

    @abc.abstractmethod
    async def store_experience(self, exp: Experience) -> None:
        """Append a new experience to the global store."""
        ...

    # ── Prompt injection ──────────────────────────────────────────────────

    async def format_for_prompt(
        self, cwd: str, task_type: str = "general", max_facts: int = 15
    ) -> str:
        """Build the memory section to inject into the system prompt.

        Default implementation composes get_context + get_facts +
        get_experiences.  Backends may override for custom formatting.
        """
        parts: list[str] = []

        # 1. Project context
        ctx = await self.get_context(cwd)
        if ctx.work_context or ctx.top_of_mind:
            parts.append("## Memory")
            if ctx.work_context:
                parts.append(f"### Work Context\n{ctx.work_context}")
            if ctx.top_of_mind:
                parts.append(f"### Top of Mind\n{ctx.top_of_mind}")

        # 2. Project facts (top by confidence)
        facts = await self.get_facts(cwd, limit=max_facts)
        if facts:
            if not parts:
                parts.append("## Memory")
            fact_lines = []
            for f in facts:
                line = f"- [{f.category}] {f.content}"
                if f.confidence >= 0.9:
                    line += " ✓"
                fact_lines.append(line)
            parts.append("### Facts\n" + "\n".join(fact_lines))

        # 3. Global experiences
        experiences = await self.get_experiences(task_type, limit=5)
        if experiences:
            if not parts:
                parts.append("## Memory")
            exp_lines = [
                f"- [{e.task_type}] {e.insight}"
                for e in experiences
                if e.insight
            ]
            if exp_lines:
                parts.append("### Global Experiences\n" + "\n".join(exp_lines))

        return "\n\n".join(parts)

    # ── Knowledge Graph ───────────────────────────────────────────────────

    def get_knowledge_graph(self, cwd: str) -> "KnowledgeGraph | None":
        """Return a KnowledgeGraph bound to the project, or None if unsupported.

        Default returns None. Backends with SQLite storage override this.
        """
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def close(self) -> None:
        """Release any held resources (connections, file handles, etc.)."""
        ...
