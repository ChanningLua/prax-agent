from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_article_batch_skill_is_discoverable_and_keeps_quality_gates(tmp_path):
    skills = {skill.name: skill for skill in load_skills(str(tmp_path))}
    skill = skills["article-batch"]

    triggers = {trigger.lower() for trigger in skill.triggers}
    assert "十篇文章" in triggers
    assert "批量写作" in triggers
    assert "python3 -m prax.article_integrations probe" in skill.content
    assert "python3 -m prax.article_batch" in skill.content
    assert "event 30% / demand 50% / evergreen 20%" in skill.content
    assert "最多同时研究两篇" in skill.content
    assert "不自动发布" in skill.content
    assert "分析稿至少 12 个来源" in skill.content
    assert skill.priority > 10


def test_article_batch_recipe_documents_all_integrations():
    root = Path(__file__).resolve().parents[2]
    recipe = root / "docs" / "recipes" / "article-batch.md"
    text = recipe.read_text(encoding="utf-8")

    for keyword in (
        "TrendRadar",
        "AutoCLI",
        "Crawl4AI",
        "GPT Researcher",
        "STORM",
        "python3 -m prax.article_batch",
    ):
        assert keyword in text
