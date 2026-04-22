"""Structural regression test for skills/ai-news-daily/SKILL.md + reference agent spec."""

from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_ai_news_daily_skill_is_discoverable(tmp_path):
    skills = {s.name: s for s in load_skills(str(tmp_path))}
    assert "ai-news-daily" in skills, (
        f"ai-news-daily skill must be bundled. Found: {sorted(skills)}"
    )
    skill = skills["ai-news-daily"]

    # Triggers must route both English and Chinese variants.
    triggers = {t.lower() for t in skill.triggers}
    for expected in ("ai-news-daily", "日报"):
        assert expected in triggers, (
            f"ai-news-daily should trigger on {expected!r}; got {sorted(triggers)}"
        )

    # Must declare the tools it actually uses.
    for tool in ("Bash", "Write", "Read", "Notify"):
        assert tool in skill.allowed_tools, (
            f"ai-news-daily must allow {tool} (runs autocli + writes markdown + notifies)"
        )

    body = skill.content

    # Pipeline references the other three skills — keeps them discoverable via search.
    assert "browser-scrape" in body or "autocli" in body
    assert "knowledge-compile" in body
    assert "Notify" in body

    # Hard preconditions must be named so the agent won't skip them.
    assert "autocli doctor" in body
    assert ".prax/notify.yaml" in body

    # Vault layout convention — downstream knowledge-compile depends on this.
    assert ".prax/vault/ai-news-hub" in body

    # Forbidden: side-effecting actions.
    assert any(m in body for m in ("不发帖", "不点赞", "不关注"))


def test_research_analyst_reference_spec_exists():
    """The reference agent spec users copy into their own .prax/agents/ lives in docs/recipes/."""
    # Located relative to this test file: .../prax/tests/unit/ → package root is three up.
    pkg_root = Path(__file__).resolve().parents[2]
    spec_path = pkg_root / "docs" / "recipes" / "ai-news-daily" / "research-analyst.md"
    assert spec_path.exists(), f"reference agent spec missing at {spec_path}"

    content = spec_path.read_text(encoding="utf-8")

    # Frontmatter must parse as a proper agent spec.
    assert content.startswith("---\n"), "missing frontmatter"
    assert "name: research-analyst" in content
    assert "keywords:" in content
    # Must grant the same tools the pipeline needs.
    for tool in ("Bash", "Read", "Write", "Notify"):
        assert tool in content, f"reference spec should allow {tool}"


def test_end_to_end_recipe_exists():
    pkg_root = Path(__file__).resolve().parents[2]
    recipe = pkg_root / "docs" / "recipes" / "ai-news-daily.md"
    assert recipe.exists()
    text = recipe.read_text(encoding="utf-8")
    # Recipe must mention the four moving pieces so readers don't miss a step.
    for keyword in ("autocli", "notify.yaml", "prax cron add", "prax cron install"):
        assert keyword in text, f"recipe should document `{keyword}`"
