"""Regression guards for the release-notes foolproof tutorial."""

from __future__ import annotations

from pathlib import Path


def test_release_notes_tutorial_has_three_phases():
    pkg_root = Path(__file__).resolve().parents[2]
    tut = pkg_root / "docs" / "tutorials" / "release-notes.md"
    assert tut.exists(), f"release-notes tutorial missing at {tut}"
    text = tut.read_text(encoding="utf-8")

    # Three-phase structure (dry-run → write → notify) is the safety net:
    # beginners can bail at each phase and still have something that works.
    for phase in ("Phase 1", "Phase 2", "Phase 3"):
        assert phase in text, f"missing {phase!r}"

    # Phase 1 must be read-only ("dry-run") so the beginner can preview.
    assert "dry-run" in text.lower() or "只读" in text

    # Scenario persona — same foolproof convention as sibling tutorials.
    assert "你是谁" in text

    # Key sibling links: getting-started is a prereq; support-digest demos
    # the feishu webhook flow; pr-triage and docs-audit round out the team
    # release workflow.
    for sibling in (
        "../getting-started.md",
        "./support-digest.md",
        "../recipes/pr-triage.md",
        "../recipes/docs-audit.md",
    ):
        assert sibling in text, f"cross-link to {sibling!r} missing"

    # Hard boundaries must be restated so the agent cannot quietly
    # auto-tag / auto-push / auto-publish.
    for boundary in ("不打 tag", "不 push", "不 npm publish", "不 `git push`"):
        # At least one of these exact phrasings must appear; all are explicit.
        pass
    boundaries_text = text
    assert any(b in boundaries_text for b in ("不打 tag", "不 tag")), (
        "must explicitly state 'no auto-tag'"
    )
    assert any(b in boundaries_text for b in ("不 push", "不 `git push`")), (
        "must explicitly state 'no auto-push'"
    )
    assert any(b in boundaries_text for b in ("不 npm publish", "不发 npm")), (
        "must explicitly state 'no auto-npm-publish'"
    )


def test_release_notes_tutorial_teaches_idempotency_check():
    """A beginner following this tutorial should come out understanding
    that rerunning doesn't duplicate — that's the whole point of
    using release-notes over a shell one-liner."""
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "tutorials" / "release-notes.md").read_text(
        encoding="utf-8"
    )
    # The specific `grep -c "^## \[` check is the verification step — if
    # the tutorial drops it a beginner can't confirm idempotency actually
    # worked.
    assert 'grep -c "^## \\[0.4.0\\]"' in text or "grep -c" in text
    assert "幂等" in text
