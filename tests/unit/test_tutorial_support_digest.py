"""Regression test: the foolproof support-digest tutorial keeps its core sections.

Users reach for this tutorial cold — they can't read a 700-line prose blob.
Guard the key sections so a well-intentioned edit doesn't accidentally delete
Step 3 (the "run it" step) or the expected-output block.
"""

from __future__ import annotations

from pathlib import Path


def test_tutorial_has_required_structure():
    pkg_root = Path(__file__).resolve().parents[2]
    tut = pkg_root / "docs" / "tutorials" / "support-digest.md"
    assert tut.exists(), f"foolproof tutorial missing at {tut}"
    text = tut.read_text(encoding="utf-8")

    # Scenario framing — "你是谁" — so beginners know this targets their role.
    assert "你是谁" in text, "tutorial must open with a scenario persona"

    # Numbered steps — beginners need checkpoints.
    for step in ("Step 1", "Step 2", "Step 3", "Step 4", "Step 5"):
        assert step in text, f"missing {step!r}"

    # Core demo commands must be present (paths/files must be right
    # or the beginner's copy-paste will 404).
    assert "tickets-2026-04-21.json" in text, "sample data path must match"
    assert ".prax/inbox" in text
    assert ".prax/vault/support" in text
    assert ".prax/inbox/archive" in text

    # Expected-output blocks — a foolproof tutorial always shows what
    # "success" looks like.
    assert "应该看到" in text
    assert "会看到" in text

    # Troubleshooting table / per-step recoveries.
    assert "常见失败" in text or "排错" in text

    # Reference to the canonical recipe and other skills (cross-links).
    assert "release-notes" in text


def test_tutorial_references_real_sample_data():
    """If the tutorial's sample-data path drifts from where the skill actually
    ships it, beginners hit a silent 404. Guard the tight coupling."""
    pkg_root = Path(__file__).resolve().parents[2]
    tut = pkg_root / "docs" / "tutorials" / "support-digest.md"
    sample = pkg_root / "docs" / "recipes" / "support-digest" / "sample-tickets.json"

    text = tut.read_text(encoding="utf-8")
    assert "docs/recipes/support-digest/sample-tickets.json" in text, (
        "tutorial should reference the exact sample path the skill ships"
    )
    assert sample.exists(), "sample data referenced by tutorial must exist"
