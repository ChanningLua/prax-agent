"""Structural regression test for skills/docs-audit/SKILL.md."""

from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_docs_audit_skill_is_discoverable(tmp_path):
    skills = {s.name: s for s in load_skills(str(tmp_path))}
    assert "docs-audit" in skills, f"docs-audit must be bundled. Found: {sorted(skills)}"
    skill = skills["docs-audit"]

    triggers = {t.lower() for t in skill.triggers}
    for expected in ("docs audit", "文档审计", "stale docs"):
        assert expected in triggers, f"should trigger on {expected!r}; got {sorted(triggers)}"

    # Needs Bash (git/grep), Read, Write, Grep.
    for tool in ("Bash", "Write", "Read", "Grep"):
        assert tool in skill.allowed_tools, f"must allow {tool}"

    body = skill.content

    # Evidence-first contract: every drift must cite git log + doc mtime.
    assert "git log" in body
    assert "证据" in body or "evidence" in body.lower()

    # Hard boundaries.
    assert "不改文档" in body or "does not edit docs" in body.lower()
    # New files (no history) must be skipped.
    assert "新文件" in body or "no history" in body.lower()
    # Generated / vendored paths must be skipped.
    for skip in ("node_modules", "__pycache__"):
        assert skip in body, f"skip pattern {skip!r} must be documented"

    # Priority tiers named so the agent uses them.
    assert "🔴" in body or "high priority" in body.lower()
    assert "🟡" in body or "low priority" in body.lower()


def test_docs_audit_recipe_exists():
    pkg_root = Path(__file__).resolve().parents[2]
    recipe = pkg_root / "docs" / "recipes" / "docs-audit.md"
    assert recipe.exists()
    text = recipe.read_text(encoding="utf-8")
    for keyword in (".prax/reports", "auto_issue", "prax cron add"):
        assert keyword in text, f"recipe should mention {keyword!r}"
