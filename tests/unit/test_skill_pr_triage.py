"""Structural regression test for skills/pr-triage/SKILL.md."""

from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_pr_triage_skill_is_discoverable(tmp_path):
    skills = {s.name: s for s in load_skills(str(tmp_path))}
    assert "pr-triage" in skills, f"pr-triage must be bundled. Found: {sorted(skills)}"
    skill = skills["pr-triage"]

    triggers = {t.lower() for t in skill.triggers}
    for expected in ("pr triage", "pr review", "审查 pr"):
        assert expected in triggers, f"should trigger on {expected!r}; got {sorted(triggers)}"

    # Needs VerifyCommand (the distinguishing Prax feature for this skill).
    for tool in ("Bash", "Write", "VerifyCommand", "Notify"):
        assert tool in skill.allowed_tools, f"must allow {tool}"

    body = skill.content

    # Mandatory workflow elements — these are the actual differentiators vs pure-LLM review.
    assert "git fetch" in body, "must actually fetch the PR branch"
    assert "VerifyCommand" in body, "must use VerifyCommand (Prax's signature verification)"
    # Must compare PR branch vs base branch tests.
    assert "base" in body.lower() and ("baseline" in body.lower() or "基线" in body)
    # gh CLI fallback path must be documented (P1 ask from plan file).
    assert any(marker in body for marker in ("gh 不可用", "degraded", "降级", "No gh", "without gh"))

    # Hard boundaries.
    for boundary in ("不 approve", "不 merge", "不 close"):
        assert boundary in body, f"boundary {boundary!r} must be explicit"

    # Must checkout back after running tests (cleanup).
    assert "checkout 回原分支" in body or "git checkout -" in body


def test_pr_triage_recipe_exists():
    pkg_root = Path(__file__).resolve().parents[2]
    recipe = pkg_root / "docs" / "recipes" / "pr-triage.md"
    assert recipe.exists()
    text = recipe.read_text(encoding="utf-8")
    for keyword in (".prax/pr-triage", "gh auth", "风险评分", "triage #"):
        assert keyword in text, f"recipe should mention {keyword!r}"
