"""Migration tool: convert existing memory.json facts into KG triples.

Reads facts from .prax/memory.json, heuristically splits each into
subject-predicate-object triples, and writes them to the KnowledgeGraph.

Usage::

    from prax.core.memory.migration import migrate_facts_to_kg
    count = migrate_facts_to_kg("/path/to/project")
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)

# Heuristic patterns for splitting facts into S-P-O triples
_PREFERENCE_PATTERNS = [
    # "User prefers X" / "用户偏好 X"
    re.compile(r"(?:user|用户)\s+(?:prefers?|偏好|喜欢)\s+(.+)", re.IGNORECASE),
    # "Always use X" / "始终使用 X"
    re.compile(r"(?:always|始终|总是)\s+(?:use|使用|用)\s+(.+)", re.IGNORECASE),
]

_USES_PATTERNS = [
    # "Project uses X" / "uses X for Y"
    re.compile(r"(?:project|项目)?\s*(?:uses?|使用)\s+(.+?)(?:\s+(?:for|用于)\s+(.+))?$", re.IGNORECASE),
]

_KNOWLEDGE_PATTERNS = [
    # "X is Y" / "X 是 Y"
    re.compile(r"^(.+?)\s+(?:is|are|was|were|是)\s+(.+)$", re.IGNORECASE),
    # "X has Y" / "X 有 Y"
    re.compile(r"^(.+?)\s+(?:has|have|had|有)\s+(.+)$", re.IGNORECASE),
]


def _split_fact_to_triples(
    content: str, category: str
) -> list[tuple[str, str, str]]:
    """Heuristically split a fact string into (subject, predicate, object) triples.

    Returns a list of triples (may be empty if no pattern matches).
    """
    content = content.strip()
    if not content:
        return []

    triples: list[tuple[str, str, str]] = []

    # Category-specific heuristics
    if category == "preference":
        for pat in _PREFERENCE_PATTERNS:
            m = pat.search(content)
            if m:
                triples.append(("user", "prefers", m.group(1).strip()))
                return triples

    if category in ("knowledge", "context"):
        for pat in _USES_PATTERNS:
            m = pat.search(content)
            if m:
                obj = m.group(1).strip()
                purpose = m.group(2).strip() if m.group(2) else None
                triples.append(("project", "uses", obj))
                if purpose:
                    triples.append((obj, "used_for", purpose))
                return triples

    # Generic pattern: try "X is/has Y" splits
    for pat in _KNOWLEDGE_PATTERNS:
        m = pat.match(content)
        if m:
            subj = m.group(1).strip()[:50]  # limit subject length
            obj = m.group(2).strip()[:80]
            # Determine predicate from the match
            if "is" in pat.pattern or "是" in pat.pattern:
                triples.append((subj, "is", obj))
            else:
                triples.append((subj, "has", obj))
            return triples

    # Fallback: store the whole fact as a triple with generic predicate
    if len(content) < 200:
        triples.append(("project", "knows", content[:100]))

    return triples


def migrate_facts_to_kg(cwd: str) -> int:
    """Migrate facts from memory.json to the KnowledgeGraph.

    Returns the number of triples written.
    """
    json_path = Path(cwd) / ".prax" / "memory.json"
    if not json_path.exists():
        logger.info("migrate_facts_to_kg: %s not found", json_path)
        return 0

    try:
        data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("migrate_facts_to_kg: failed to read %s: %s", json_path, e)
        return 0

    facts_raw: list[Any] = data.get("facts", [])
    if not facts_raw:
        return 0

    kg = KnowledgeGraph(cwd)
    count = 0

    for raw in facts_raw:
        if isinstance(raw, str):
            content = raw
            category = "context"
        elif isinstance(raw, dict):
            content = raw.get("content", "")
            category = raw.get("category", "context")
        else:
            continue

        content = content.strip()
        if not content:
            continue

        triples = _split_fact_to_triples(content, category)
        for subj, pred, obj in triples:
            kg.add_triple(subj, pred, obj, source="migration")
            count += 1

    logger.info("migrate_facts_to_kg: wrote %d triples from %d facts", count, len(facts_raw))
    return count
