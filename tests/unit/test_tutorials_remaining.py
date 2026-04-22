"""Structural regression guards for docs-audit and pr-triage tutorials."""

from __future__ import annotations

from pathlib import Path


# ── docs-audit ─────────────────────────────────────────────────────────────


def test_docs_audit_tutorial_has_three_phases():
    pkg_root = Path(__file__).resolve().parents[2]
    tut = pkg_root / "docs" / "tutorials" / "docs-audit.md"
    assert tut.exists(), f"docs-audit tutorial missing at {tut}"
    text = tut.read_text(encoding="utf-8")

    for phase in ("Phase 1", "Phase 2", "Phase 3"):
        assert phase in text, f"missing {phase!r}"

    assert "你是谁" in text

    # Tutorial demos against this repo itself before jumping to "your repo".
    assert "prax-agent" in text.lower() or "本仓库" in text or "本 Prax 仓库" in text

    # Priority tiers that the skill enforces must be documented.
    assert "🔴" in text
    assert "🟡" in text

    # Evidence-first narrative — the `git log` evidence example must show up.
    assert "git log" in text

    # Cross-links: prereq + related tutorials in the release workflow.
    for link in (
        "../getting-started.md",
        "./release-notes.md",
        "./support-digest.md",
        "../recipes/pr-triage.md",
    ):
        assert link in text, f"cross-link {link!r} missing"


def test_docs_audit_tutorial_states_non_destructive_boundary():
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "tutorials" / "docs-audit.md").read_text(encoding="utf-8")
    # The boundary the skill enforces: only reports, never edits docs.
    assert "不改任何文档" in text or "不改文档" in text
    # Must also say it won't open issues by default (compliance-friendly).
    assert "auto_issue: false" in text or "默认**不**自动开 issue" in text


# ── pr-triage ──────────────────────────────────────────────────────────────


def test_pr_triage_tutorial_has_three_phases():
    pkg_root = Path(__file__).resolve().parents[2]
    tut = pkg_root / "docs" / "tutorials" / "pr-triage.md"
    assert tut.exists(), f"pr-triage tutorial missing at {tut}"
    text = tut.read_text(encoding="utf-8")

    for phase in ("Phase 1", "Phase 2", "Phase 3"):
        assert phase in text, f"missing {phase!r}"

    assert "你是谁" in text


def test_pr_triage_tutorial_teaches_test_comparison_table():
    """The 4-cell truth table (PR passes × base passes) is the
    load-bearing mental model for this skill. Without it the tutorial
    reads as "run the command, get a report" — and a beginner can't
    interpret the report usefully."""
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "tutorials" / "pr-triage.md").read_text(encoding="utf-8")

    # Must show a comparison table that names all 4 cells.
    for cell_marker in ("✅ 通过", "❌ 失败", "PR 引入了问题", "基线坏"):
        assert cell_marker in text, f"test-comparison cell {cell_marker!r} missing"


def test_pr_triage_tutorial_has_gh_fallback_note():
    """The `gh`-unavailable degraded path is a real user situation (CI
    containers, offline laptops). Must be documented, not glossed over."""
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "tutorials" / "pr-triage.md").read_text(encoding="utf-8")
    assert "gh" in text.lower()
    assert "degraded" in text.lower() or "降级" in text


def test_pr_triage_tutorial_states_no_github_side_effects():
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "tutorials" / "pr-triage.md").read_text(encoding="utf-8")
    # The most-asked question for any AI review tool: "will it click merge?"
    # Answer must be no, in plain Chinese/English.
    for forbidden in ("approve", "merge", "close"):
        assert forbidden in text.lower()
    # At least one clear "won't auto-do-that" statement.
    assert "不" in text and "**approve**" in text
