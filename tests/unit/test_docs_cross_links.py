"""Guardrail: every relative cross-link in docs/ must resolve to a real path.

Beginners following a tutorial hit "page not found" when a refactor moves
a file but forgets the incoming link. This test catches that at PR time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _collect_broken_links() -> list[tuple[Path, str, Path]]:
    """Return (source_md, raw_target, resolved_path) for every broken link."""
    pkg_root = Path(__file__).resolve().parents[2]
    docs = pkg_root / "docs"
    broken: list[tuple[Path, str, Path]] = []

    for md in sorted(docs.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for target in _LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            clean = target.split("#", 1)[0].split("?", 1)[0]
            if not clean:
                continue
            resolved = (md.parent / clean).resolve()
            if clean.endswith("/"):
                if not resolved.is_dir():
                    broken.append((md, target, resolved))
                continue
            if not resolved.exists():
                broken.append((md, target, resolved))
    return broken


def test_no_broken_cross_links_in_docs():
    broken = _collect_broken_links()
    if broken:
        lines = [
            f"  {src.relative_to(src.parents[2])}: {raw} → {resolved}"
            for src, raw, resolved in broken
        ]
        pytest.fail(
            f"{len(broken)} broken cross-link(s) found in docs/:\n"
            + "\n".join(lines)
        )
