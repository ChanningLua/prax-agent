"""Structural regression test for skills/knowledge-compile/SKILL.md."""

from __future__ import annotations

from prax.core.skills_loader import load_skills


def test_knowledge_compile_skill_is_discoverable(tmp_path):
    skills = {s.name: s for s in load_skills(str(tmp_path))}

    assert "knowledge-compile" in skills, (
        f"knowledge-compile skill must be bundled. Found: {sorted(skills)}"
    )
    skill = skills["knowledge-compile"]

    # Triggers must fire on both Chinese and English synonyms.
    triggers = {t.lower() for t in skill.triggers}
    for expected in ("wiki", "digest", "整理", "知识库"):
        assert expected in triggers, (
            f"knowledge-compile should trigger on {expected!r}; got {sorted(triggers)}"
        )

    # It writes files, so Write and Read must be allowed.
    assert "Write" in skill.allowed_tools
    assert "Read" in skill.allowed_tools

    # Hard contract: must mention Obsidian double-link style and the three
    # output artifacts (index / topics / daily-digest).
    body = skill.content
    assert "[[" in body, "must document Obsidian double-link syntax"
    assert "index.md" in body
    assert "daily-digest.md" in body
    assert "topics/" in body

    # Non-destructive: the skill must say it does not delete raw files.
    assert any(marker in body for marker in ("不删", "不要删", "只写不删"))

    # Priority positive so it beats default-priority skills.
    assert skill.priority > 0
