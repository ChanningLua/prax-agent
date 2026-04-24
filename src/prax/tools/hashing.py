"""Shared helpers for hash-anchored file editing."""

from __future__ import annotations

import hashlib


def compute_line_hash(line_number: int, content: str) -> str:
    """Return a short stable hash for a numbered line."""
    payload = f"{line_number}:{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]
