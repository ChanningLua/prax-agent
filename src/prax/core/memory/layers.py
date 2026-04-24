"""Layered memory injection for system prompt construction.

Replaces flat fact injection with a tiered approach:

  L0 Identity   (~100 tokens) — user preferences, project identity
  L1 Essential   (~500 tokens) — high-confidence KG triples summary
  L2 On-Demand   (~300 tokens) — semantically relevant facts
  L3 Deep Search  (~800 tokens) — full KG query fallback

When KnowledgeGraph is unavailable, falls back to MemoryStore.format_for_prompt().
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .knowledge_graph import KnowledgeGraph
    from .vector_store import VectorStore
    from ..memory_store import MemoryStore

logger = logging.getLogger(__name__)

# Token budget per layer (approximate)
L0_BUDGET = 100
L1_BUDGET = 500
L2_BUDGET = 300
L3_BUDGET = 800

# CJK Unicode range for token estimation
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')

# English stop words for L3 query filtering
_EN_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "about", "and", "but", "or", "if", "that", "this",
    "it", "its", "my", "your", "we", "they", "what", "which", "how",
}


def _estimate_tokens(text: str) -> int:
    """Token estimate with CJK awareness.

    CJK characters: ~1.5 tokens per character.
    English words: ~1.3 tokens per word.
    """
    cjk_chars = len(_CJK_RE.findall(text))
    # Remove CJK chars to count English words
    non_cjk = _CJK_RE.sub(' ', text)
    en_words = len(non_cjk.split())
    return max(1, int(cjk_chars * 1.5 + en_words * 1.3))


def _truncate_to_budget(text: str, budget: int) -> str:
    """Truncate text to approximately fit within token budget."""
    tokens = _estimate_tokens(text)
    if tokens <= budget:
        return text
    # Rough ratio: cut proportionally
    ratio = budget / tokens
    char_limit = max(10, int(len(text) * ratio))
    lines = text.split("\n")
    result = []
    total = 0
    for line in lines:
        line_len = len(line) + 1
        if total + line_len > char_limit:
            # If no lines fit yet, truncate the first line instead of returning empty
            if not result:
                result.append(line[:char_limit])
            break
        result.append(line)
        total += line_len
    return "\n".join(result)


class LayeredInjector:
    """Builds layered memory prompt from KG + VectorStore + MemoryStore."""

    def __init__(
        self,
        *,
        kg: "KnowledgeGraph | None" = None,
        vector_store: "VectorStore | None" = None,
        memory_store: "MemoryStore | None" = None,
        memory_backend: Any = None,
    ) -> None:
        self._kg = kg
        self._vector_store = vector_store
        self._memory_store = memory_store
        self._memory_backend = memory_backend
        self._dialect: Any = None  # cached Dialect instance

    def build_l0(self, cwd: str) -> str:
        """L0 Identity: user preferences and project identity."""
        if self._memory_store is None:
            return ""
        entry = self._memory_store.load()
        parts: list[str] = []
        if entry.workContext:
            parts.append(f"Project: {entry.workContext}")
        if entry.topOfMind:
            parts.append(f"Focus: {entry.topOfMind}")

        # Extract preference facts (high confidence)
        for fact in entry.facts:
            if isinstance(fact, dict):
                category = fact.get("category", "")
                confidence = fact.get("confidence", 0)
                if category == "preference" and confidence >= 0.9:
                    parts.append(f"- {fact.get('content', '')}")

        text = "\n".join(parts)
        return _truncate_to_budget(text, L0_BUDGET)

    def build_l1(self, cwd: str) -> str:
        """L1 Essential: high-confidence KG triples summary, dialect-compressed."""
        if self._kg is None:
            return ""
        triples = self._kg.get_top_triples(limit=15, min_confidence=0.9)
        if not triples:
            return ""

        # Try dialect compression for compactness
        try:
            from .dialect import Dialect
            if self._dialect is None:
                self._dialect = Dialect.from_kg(self._kg)
            compressed = self._dialect.compress_for_l1(triples)
            if compressed:
                return _truncate_to_budget(compressed, L1_BUDGET)
        except Exception:
            pass

        # Fallback: plain triple format
        lines = []
        for t in triples:
            lines.append(f"- {t['subject']} → {t['predicate']} → {t['object']}")
        text = "\n".join(lines)
        return _truncate_to_budget(text, L1_BUDGET)

    async def build_l2(self, cwd: str, query: str, where: dict[str, Any] | None = None) -> str:
        """L2 On-Demand: semantically relevant facts."""
        if self._vector_store is None or not query:
            return ""
        try:
            results = await self._vector_store.query(cwd, query, n_results=5, where=where)
        except Exception as e:
            logger.warning("build_l2 vector query failed: %s", e)
            return ""
        if not results:
            return ""
        lines = []
        for item in results:
            meta = item.get("metadata", {})
            category = meta.get("category", "context")
            score = item.get("score", 0)
            lines.append(f"- [{category}] {item['content']} ({score:.2f})")
        text = "\n".join(lines)
        return _truncate_to_budget(text, L2_BUDGET)

    def build_l3(self, cwd: str, query: str) -> str:
        """L3 Deep Search: full KG query fallback when L2 returns nothing."""
        if self._kg is None or not query:
            return ""
        # Extract query terms: CJK character sequences + English words
        tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', query)
        # Filter English stop words, keep CJK as-is
        words = [
            t for t in tokens
            if t.lower() not in _EN_STOP_WORDS and len(t) >= 2
        ]
        results: list[dict[str, Any]] = []
        seen_predicates: set[str] = set()
        for word in words[:8]:  # limit to first 8 terms
            entity_results = self._kg.query_entity(word, direction="both")
            for r in entity_results:
                key = f"{r['subject']}_{r['predicate']}_{r['object']}"
                if key not in seen_predicates and r.get("current", True):
                    results.append(r)
                    seen_predicates.add(key)
                    if len(results) >= 20:
                        break
            if len(results) >= 20:
                break

        if not results:
            return ""

        lines = []
        for r in results:
            lines.append(f"- {r['subject']} → {r['predicate']} → {r['object']}")
        text = "\n".join(lines)
        return _truncate_to_budget(text, L3_BUDGET)

    def build_sync(self, cwd: str, query: str = "") -> str:
        """Build layered prompt synchronously (for sync context builder).

        Uses L0 + L1 only (no async vector search).
        When query is provided, also attempts L3 deep search.
        Falls back to MemoryStore.format_for_prompt() when KG is unavailable.
        """
        # Fallback path: no KG available
        if self._kg is None:
            if self._memory_store is not None:
                return self._memory_store.format_for_prompt()
            return ""

        parts: list[str] = []

        l0 = self.build_l0(cwd)
        if l0:
            parts.append(f"### Identity\n{l0}")

        l1 = self.build_l1(cwd)
        if l1:
            parts.append(f"### Knowledge Graph\n{l1}")

        # L3 deep search when query is provided
        if query:
            l3 = self.build_l3(cwd, query)
            if l3:
                parts.append(f"### Deep Search\n{l3}")

        if not parts:
            # KG exists but is empty — fall back to flat facts
            if self._memory_store is not None:
                return self._memory_store.format_for_prompt()
            return ""

        return "## Memory\n\n" + "\n\n".join(parts)

    async def build_async(self, cwd: str, query: str = "") -> str:
        """Build full layered prompt (async, includes L2/L3).

        Falls back to MemoryStore.format_for_prompt() when KG is unavailable.
        """
        if self._kg is None:
            if self._memory_store is not None:
                return self._memory_store.format_for_prompt()
            return ""

        parts: list[str] = []

        l0 = self.build_l0(cwd)
        if l0:
            parts.append(f"### Identity\n{l0}")

        l1 = self.build_l1(cwd)
        if l1:
            parts.append(f"### Knowledge Graph\n{l1}")

        l2 = await self.build_l2(cwd, query)
        if l2:
            parts.append(f"### Relevant Facts\n{l2}")
        elif query:
            # L2 returned nothing — try L3 deep search
            l3 = self.build_l3(cwd, query)
            if l3:
                parts.append(f"### Deep Search\n{l3}")

        if not parts:
            if self._memory_store is not None:
                return self._memory_store.format_for_prompt()
            return ""

        return "## Memory\n\n" + "\n\n".join(parts)
