"""Structural regression test for skills/release-notes/SKILL.md."""

from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_release_notes_skill_is_discoverable(tmp_path):
    skills = {s.name: s for s in load_skills(str(tmp_path))}
    assert "release-notes" in skills, f"release-notes must be bundled. Found: {sorted(skills)}"
    skill = skills["release-notes"]

    # Trigger on both Chinese and English + conventional phrasing.
    triggers = {t.lower() for t in skill.triggers}
    for expected in ("release notes", "changelog", "发版说明"):
        assert expected in triggers, f"should trigger on {expected!r}; got {sorted(triggers)}"

    # Needs Bash (git/gh) + Write (CHANGELOG.md + docs/releases/).
    for tool in ("Bash", "Write", "Read"):
        assert tool in skill.allowed_tools, f"must allow {tool}"

    body = skill.content

    # Format contract: Keep a Changelog.
    assert "Keep a Changelog" in body or "keep-a-changelog" in body.lower()
    # Conventional Commits prefixes must be named so the agent uses the mapping.
    for prefix in ("feat", "fix", "refactor", "chore", "BREAKING CHANGE"):
        assert prefix in body, f"prefix {prefix!r} must be documented"
    # Keep-a-Changelog section headers must be enumerated.
    for section in ("### Added", "### Changed", "### Fixed"):
        assert section in body, f"section {section!r} must be documented"

    # Hard boundaries: skill must say it doesn't tag/push/publish.
    assert any(marker in body for marker in ("不打 tag", "不发 npm", "不 push"))
    # Idempotent: same version rerun overwrites, not appends.
    assert "幂等" in body or "idempotent" in body.lower()


def test_release_notes_recipe_exists():
    pkg_root = Path(__file__).resolve().parents[2]
    recipe = pkg_root / "docs" / "recipes" / "release-notes.md"
    assert recipe.exists(), f"recipe must ship at {recipe}"
    text = recipe.read_text(encoding="utf-8")
    for keyword in ("CHANGELOG.md", "docs/releases", "prax prompt", "Conventional Commits"):
        assert keyword in text, f"recipe should mention {keyword!r}"
