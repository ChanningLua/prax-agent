"""Tests for Batch 5 skills system."""
import tempfile
from pathlib import Path

import pytest

from prax.core.skills_loader import load_skills, format_skills_for_prompt, Skill
from prax.core.context import Context
from prax.commands.handlers import CommandContext, _handle_skills
from prax.commands.registry import parse_slash_command
from prax.core.session_store import FileSessionStore


class TestLoadSkills:
    """Tests for load_skills function."""

    def test_subdirectory_format(self):
        """Test loading skills from subdirectory format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create skill in subdirectory format
            skill_dir = Path(tmpdir) / ".prax" / "skills" / "commit"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Commit Skill\nCreate a git commit.")

            skills = load_skills(tmpdir)

            names = [s.name for s in skills]
            assert "commit" in names
            commit_skill = next(s for s in skills if s.name == "commit")
            assert "Create a git commit" in commit_skill.description

    def test_single_file_format(self):
        """Test loading skills from single file format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create skill as single file
            skills_dir = Path(tmpdir) / ".prax" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "deploy.md").write_text("# Deploy Skill\nDeploy the application.")

            skills = load_skills(tmpdir)

            names = [s.name for s in skills]
            assert "deploy" in names
            deploy_skill = next(s for s in skills if s.name == "deploy")
            assert "Deploy the application" in deploy_skill.description

    def test_empty_directory(self):
        """Test loading from empty skills directory returns no local skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".prax" / "skills"
            skills_dir.mkdir(parents=True)

            skills = load_skills(tmpdir)
            local_names = {s.name for s in skills if str(skills_dir) in s.path}
            assert local_names == set()

    def test_no_directory(self):
        """Test loading when skills directory doesn't exist returns no local skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills = load_skills(tmpdir)
            local_dir = str(Path(tmpdir) / ".prax" / "skills")
            local_names = {s.name for s in skills if local_dir in s.path}
            assert local_names == set()

    def test_both_formats(self):
        """Test loading both subdirectory and single file formats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".prax" / "skills"
            skills_dir.mkdir(parents=True)

            # Subdirectory format
            commit_dir = skills_dir / "commit"
            commit_dir.mkdir()
            (commit_dir / "SKILL.md").write_text("Create a commit.")

            # Single file format
            (skills_dir / "review.md").write_text("Review code changes.")

            skills = load_skills(tmpdir)
            names = {s.name for s in skills}

            assert "commit" in names
            assert "review" in names


class TestFormatSkillsForPrompt:
    """Tests for format_skills_for_prompt function."""

    def test_empty_skills(self):
        """Test formatting empty skills list."""
        result = format_skills_for_prompt([])
        assert result == ""

    def test_includes_name_and_description(self):
        """Test that formatted output includes name and description."""
        skills = [
            Skill(
                name="commit",
                description="Create a git commit",
                content="Full content",
                path="/path/to/SKILL.md"
            )
        ]

        result = format_skills_for_prompt(skills)

        assert "commit" in result
        assert "Create a git commit" in result

    def test_does_not_include_full_content(self):
        """Test that full content is not included in prompt format."""
        skills = [
            Skill(
                name="commit",
                description="Create a git commit",
                content="This is the full detailed content that should not appear",
                path="/path/to/SKILL.md"
            )
        ]

        result = format_skills_for_prompt(skills)

        assert "This is the full detailed content" not in result


class TestContextSkillsIntegration:
    """Tests for skills integration with Context."""

    def test_context_includes_skills(self):
        """Test that context system prompt includes skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a skill
            skill_dir = Path(tmpdir) / ".prax" / "skills" / "commit"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("Create a git commit.")

            context = Context(cwd=tmpdir)
            prompt = context.build_system_prompt()

            assert "commit" in prompt
            assert "Available Skills" in prompt


class TestSkillsCommand:
    """Tests for /skills command."""

    def test_skills_list(self):
        """Test listing skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a skill
            skill_dir = Path(tmpdir) / ".prax" / "skills" / "commit"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("Create a git commit.")

            store = FileSessionStore(tmpdir)
            ctx = CommandContext(
                cwd=tmpdir,
                models_config={},
                session_store=store
            )

            result = _handle_skills([], ctx)
            assert "commit" in result.text

    def test_skills_show(self):
        """Test showing a specific skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a skill
            skill_dir = Path(tmpdir) / ".prax" / "skills" / "commit"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Commit\nCreate a git commit with proper message.")

            store = FileSessionStore(tmpdir)
            ctx = CommandContext(
                cwd=tmpdir,
                models_config={},
                session_store=store
            )

            result = _handle_skills(["show", "commit"], ctx)
            assert "Create a git commit with proper message" in result.text

    def test_skills_command_registered(self):
        """Test that /skills command is registered."""
        cmd = parse_slash_command("/skills")
        assert cmd is not None
        assert cmd.name == "skills"
