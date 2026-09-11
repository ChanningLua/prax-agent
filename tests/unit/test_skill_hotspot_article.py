from __future__ import annotations

from pathlib import Path

from prax.core.skills_loader import load_skills


def test_hotspot_article_skill_is_discoverable(tmp_path):
    skills = {skill.name: skill for skill in load_skills(str(tmp_path))}
    assert "hotspot-article" in skills
    skill = skills["hotspot-article"]

    triggers = {trigger.lower() for trigger in skill.triggers}
    for expected in ("热点文章", "热点追踪", "深度文章", "对标精选"):
        assert expected in triggers

    for tool in ("Bash", "Read", "Write", "WebSearch", "WebCrawler"):
        assert tool in skill.allowed_tools

    body = skill.content
    for artifact in (
        "topic-candidates.json",
        "opportunity-report.json",
        "business-bridge.json",
        "demand-brief.md",
        "source-index.json",
        "evidence.json",
        "fact-check.json",
        "quality-report.json",
        "publish-ready.md",
    ):
        assert artifact in body

    assert "python3 -m prax.content_quality" in body
    assert "python3 -m prax.topic_opportunity" in body
    assert "--bridge-out" in body
    assert "--business-bridge" in body
    assert "真实需求 45%" in body
    assert "event: 0.30" in body
    assert "demand: 0.50" in body
    assert "两类独立信号" in body
    assert "1h / 6h / 24h" in body
    assert "来源信号 → 技术变化 → 受影响流程" in body
    assert "技术到业务桥完整度为 1.00" in body
    assert "`daily-digest`" in body
    assert "selected-analysis" in body
    assert "selected-framework" in body
    assert "selected-hands-on" in body
    assert "撞题门" in body
    assert "去 AI 腔编辑" in body
    assert "作者感与自然度" in body
    assert "不自动发布" in body
    assert skill.priority >= 10


def test_hotspot_article_recipe_exists():
    root = Path(__file__).resolve().parents[2]
    recipe = root / "docs" / "recipes" / "hotspot-article.md"
    assert recipe.exists()
    text = recipe.read_text(encoding="utf-8")
    for keyword in (
        "hotspot-article",
        "source-index.json",
        "business-bridge.json",
        "fact-check.json",
        "quality-report.json",
    ):
        assert keyword in text
