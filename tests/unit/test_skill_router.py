"""Unit tests for SkillRouter helpers in skills_loader.py."""
from __future__ import annotations

import pytest

from prax.core.skills_loader import (
    Skill,
    filter_skills_by_task_type,
    format_skills_for_subagent,
)


def _skill(name: str, description: str = "", content: str = "# content") -> Skill:
    return Skill(name=name, description=description, content=content, path=f"/fake/{name}.md")


class TestFilterSkillsByTaskType:
    def test_returns_empty_when_no_skills(self):
        assert filter_skills_by_task_type([], "debugging") == []

    def test_returns_relevant_skill_by_name(self):
        skills = [_skill("debug-helper", "Helps debug issues"), _skill("deploy")]
        result = filter_skills_by_task_type(skills, "debugging")
        names = [s.name for s in result]
        assert "debug-helper" in names

    def test_excludes_irrelevant_skills(self):
        skills = [_skill("deploy"), _skill("i18n")]
        result = filter_skills_by_task_type(skills, "debugging")
        # Neither 'deploy' nor 'i18n' should rank highly for debugging
        assert len(result) == 0 or all(s.name not in ("deploy", "i18n") for s in result)

    def test_respects_max_skills_limit(self):
        skills = [_skill(f"debug-tool-{i}", f"debug tool {i}") for i in range(10)]
        result = filter_skills_by_task_type(skills, "debugging", max_skills=3)
        assert len(result) <= 3

    def test_description_keyword_match(self):
        skills = [
            _skill("helper", "Useful for debugging errors and tracebacks"),
            _skill("unrelated", "Does something completely different"),
        ]
        result = filter_skills_by_task_type(skills, "debugging")
        assert result[0].name == "helper"


class TestFormatSkillsForSubagent:
    def test_returns_empty_string_when_no_skills(self):
        assert format_skills_for_subagent([]) == ""

    def test_includes_skill_name_in_output(self):
        skills = [_skill("my-skill", "Does something", "Full content here")]
        output = format_skills_for_subagent(skills)
        assert "my-skill" in output

    def test_includes_full_content(self):
        skills = [_skill("s", content="Detailed instructions for the subagent")]
        output = format_skills_for_subagent(skills)
        assert "Detailed instructions" in output

    def test_truncates_long_content(self):
        long_content = "x" * 5000
        skills = [_skill("big", content=long_content)]
        output = format_skills_for_subagent(skills, max_chars_per_skill=2000)
        # Should not include all 5000 chars
        assert output.count("x") <= 2000 + 50  # small slack for surrounding text

    def test_multiple_skills_all_present(self):
        skills = [_skill("alpha", content="alpha content"), _skill("beta", content="beta content")]
        output = format_skills_for_subagent(skills)
        assert "alpha" in output
        assert "beta" in output
