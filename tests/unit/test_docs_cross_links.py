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
    targets: list[Path] = list((pkg_root / "docs").rglob("*.md"))
    # README.md and README.zh-CN.md at repo root are the first thing new users
    # read; broken links there are strictly worse than broken links in docs/.
    for name in ("README.md", "README.zh-CN.md", "CHANGELOG.md"):
        root_doc = pkg_root / name
        if root_doc.exists():
            targets.append(root_doc)

    broken: list[tuple[Path, str, Path]] = []

    for md in sorted(targets):
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
        # src.parents[2] may be a grandparent of src or not, depending on depth —
        # be defensive when generating relative paths for the failure message.
        pkg_root = Path(__file__).resolve().parents[2]
        lines = []
        for src, raw, resolved in broken:
            try:
                rel = src.relative_to(pkg_root)
            except ValueError:
                rel = src
            lines.append(f"  {rel}: {raw} → {resolved}")
        pytest.fail(
            f"{len(broken)} broken cross-link(s) found:\n" + "\n".join(lines)
        )
