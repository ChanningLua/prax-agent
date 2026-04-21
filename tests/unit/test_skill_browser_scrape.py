"""Regression test: the bundled browser-scrape skill loads and has sane metadata.

This prevents accidental breakage of the SKILL.md frontmatter (wrong indent,
missing required field, etc.) since a broken frontmatter would silently degrade
to default values and the agent would stop triggering on the right keywords.
"""

from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_bundled_browser_scrape_skill_is_discoverable(tmp_path):
    # load_skills scans bundled + .claude + .prax; tmp_path is empty so only
    # bundled skills matter.
    skills = load_skills(str(tmp_path))
    by_name = {s.name: s for s in skills}

    assert "browser-scrape" in by_name, (
        f"browser-scrape skill must be bundled. Discovered: {sorted(by_name)}"
    )
    skill = by_name["browser-scrape"]

    # Triggers should cover the core scraping keywords.
    triggers_lower = {t.lower() for t in skill.triggers}
    for expected in ("autocli", "scrape", "twitter", "zhihu", "bilibili"):
        assert expected in triggers_lower, (
            f"browser-scrape should trigger on {expected!r}; "
            f"got {sorted(triggers_lower)}"
        )

    # It uses Bash (to call autocli) and Write (to store results).
    assert "Bash" in skill.allowed_tools
    assert "Write" in skill.allowed_tools

    # Content must mention the install precondition so the agent nags the user.
    assert "autocli doctor" in skill.content
    assert "Chrome" in skill.content

    # Priority must be positive so it outranks empty/default skills.
    assert skill.priority > 0
