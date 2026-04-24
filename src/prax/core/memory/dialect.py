"""AAAK Dialect — compact symbolic encoding for KG triples.

Adapted from MemPalace dialect.py for Prax. Compresses KG triples into
a compact format that any LLM reads natively — no decoder required.

Entity codebook is built dynamically from KG entities table.

Format per triple:
    ENTITY_CODE|predicate|ENTITY_CODE

Example:
    USR|prefers|CHN_LNG
    PRJ|uses|SQLT

Usage::

    from prax.core.memory.dialect import Dialect

    dialect = Dialect.from_kg(kg)
    compressed = dialect.compress_triples(triples)
    stats = dialect.compression_stats(original, compressed)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .knowledge_graph import KnowledgeGraph

import logging

logger = logging.getLogger(__name__)

# Common stop words to strip from entity codes
_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "about", "and", "but", "or", "if", "that", "this",
}

# CJK Unicode range
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
# Emoji and special character pattern
_EMOJI_RE = re.compile(
    r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
    r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF'
    r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]+',
    flags=re.UNICODE,
)


def _make_code(name: str, max_len: int = 8) -> str:
    """Generate a short uppercase code from an entity name.

    Examples:
        "Chinese language" -> "CHN_LNG"
        "user" -> "USR"
        "SQLite" -> "SQLT"
        "Python" -> "PYTH"
        "用户" -> "用户"
        "中文回答" -> "中文"
        "用户 偏好 中文" -> "用偏中"
    """
    # Clean emoji and special characters
    name = _EMOJI_RE.sub('', name).strip()
    if not name:
        return "UNK"

    # Check if name contains CJK characters
    cjk_chars = _CJK_RE.findall(name)
    if cjk_chars:
        # Split into CJK-word segments
        segments = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', name)
        segments = [s for s in segments if s.strip()]
        if not segments:
            return "UNK"
        if len(segments) == 1:
            # Single CJK word: take first 2 characters
            return segments[0][:2]
        # Multi-segment: take first character of each segment
        code = "".join(s[0] for s in segments[:4])
        return code[:max_len]

    # Remove stop words
    words = [w for w in name.split() if w.lower() not in _STOP_WORDS]
    if not words:
        words = name.split()[:1] or [name]

    if len(words) == 1:
        w = words[0]
        # Keep consonants + first vowel for short words
        if len(w) <= 4:
            return w.upper()
        # Strip vowels from middle for longer words
        code = w[0] + re.sub(r"[aeiou]", "", w[1:], flags=re.IGNORECASE)
        return code[:max_len].upper()

    # Multi-word: take first 3 chars of each word
    parts = []
    for w in words[:3]:
        parts.append(w[:3].upper())
    return "_".join(parts)[:max_len]


class Dialect:
    """AAAK Dialect encoder for KG triples."""

    def __init__(self, entity_codes: dict[str, str] | None = None) -> None:
        self._codes: dict[str, str] = {}
        if entity_codes:
            for name, code in entity_codes.items():
                self._codes[name.lower()] = code

    @classmethod
    def from_kg(cls, kg: "KnowledgeGraph") -> "Dialect":
        """Build dialect with entity codebook from KG entities table."""
        try:
            conn = kg._conn()
            rows = conn.execute("SELECT id, name FROM entities").fetchall()

            codes: dict[str, str] = {}
            used_codes: set[str] = set()
            for row in rows:
                name = row["name"] if hasattr(row, "keys") else row[1]
                code = _make_code(name)
                # Resolve collisions by appending digits
                base = code
                i = 2
                while code in used_codes:
                    code = f"{base}{i}"
                    i += 1
                codes[name] = code
                used_codes.add(code)

            return cls(entity_codes=codes)
        except Exception as e:
            logger.warning("Dialect.from_kg failed: %s", e)
            return cls()

    def save_codebook(self, path: str | Path) -> None:
        """Save entity codebook to JSON."""
        canonical: dict[str, str] = {}
        seen: set[str] = set()
        for name, code in self._codes.items():
            if code not in seen:
                canonical[name] = code
                seen.add(code)
        Path(path).write_text(
            json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def from_codebook(cls, path: str | Path) -> "Dialect":
        """Load entity codebook from JSON."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(entity_codes=data)

    def encode_entity(self, name: str) -> str:
        """Encode an entity name to its short code."""
        key = name.lower()
        if key in self._codes:
            return self._codes[key]
        # Auto-generate code
        code = _make_code(name)
        self._codes[key] = code
        return code

    def build_codebook(self) -> str:
        """Return a compact human-readable codebook string."""
        if not self._codes:
            return ""
        code_to_name: dict[str, str] = {}
        for name, code in self._codes.items():
            if code not in code_to_name:
                code_to_name[code] = name
        parts = [f"{code}={name}" for code, name in sorted(code_to_name.items())]
        return "CODES:" + ",".join(parts)

    def compress_triple(self, triple: dict[str, Any]) -> str:
        """Compress a single triple dict to AAAK format."""
        subj = self.encode_entity(triple.get("subject", "?"))
        pred = triple.get("predicate", "?")
        obj = self.encode_entity(triple.get("object", "?"))
        conf = triple.get("confidence")
        vf = triple.get("valid_from")

        parts = [f"{subj}|{pred}|{obj}"]
        if conf is not None and conf < 1.0:
            parts.append(f"{conf:.1f}")
        if vf:
            parts.append(vf)
        return "|".join(parts)

    def compress_triples(self, triples: list[dict[str, Any]]) -> str:
        """Compress a list of triple dicts to AAAK format."""
        if not triples:
            return ""
        lines = [self.compress_triple(t) for t in triples]
        return "\n".join(lines)

    def compress_for_l1(self, triples: list[dict[str, Any]]) -> str:
        """Compress triples for L1 injection with codebook header."""
        if not triples:
            return ""

        # Build minimal codebook for referenced entities
        referenced: set[str] = set()
        for t in triples:
            referenced.add(t.get("subject", "").lower())
            referenced.add(t.get("object", "").lower())

        codebook_lines = []
        for name in sorted(referenced):
            if name and name in self._codes:
                codebook_lines.append(f"{self._codes[name]}={name}")

        parts = []
        if codebook_lines:
            parts.append("CODES:" + ",".join(codebook_lines))
        parts.append(self.compress_triples(triples))
        return "\n".join(parts)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Conservative token estimate (~1.3 tokens per word)."""
        return max(1, int(len(text.split()) * 1.3))

    def compression_stats(self, original: str, compressed: str) -> dict[str, Any]:
        """Compare original vs compressed token usage."""
        orig_tokens = self.estimate_tokens(original)
        comp_tokens = self.estimate_tokens(compressed)
        return {
            "original_tokens": orig_tokens,
            "compressed_tokens": comp_tokens,
            "ratio": round(orig_tokens / max(comp_tokens, 1), 1),
            "original_chars": len(original),
            "compressed_chars": len(compressed),
        }
