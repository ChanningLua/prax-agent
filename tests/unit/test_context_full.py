"""Unit tests for prax/core/context.py.

All tests are pure unit tests. No real file I/O against production paths —
tmp_path is used for any filesystem interaction. No LLM or network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prax.core.context import (
    Context,
    SYSTEM_PROMPT_TEMPLATE,
    INTENT_GATE_PROMPT,
    _build_rules_filter,
    _RULES_TASK_KEYWORDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(tmp_path, **kwargs) -> Context:
    defaults = dict(cwd=str(tmp_path), model="test-model")
    defaults.update(kwargs)
    return Context(**defaults)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# TestBuildRulesFilter
# ---------------------------------------------------------------------------


class TestBuildRulesFilter:
    def test_general_allows_everything(self):
        f = _build_rules_filter("general")
        assert f("any-rule-file") is True
        assert f("security") is True
        assert f("totally-unrelated") is True

    def test_unknown_task_type_allows_everything(self):
        f = _build_rules_filter("exotic_unknown_type")
        assert f("something") is True

    def test_git_task_type_allows_git_rules(self):
        f = _build_rules_filter("git")
        assert f("git-workflow") is True
        assert f("commit-style") is True
        assert f("coding-guidelines") is True

    def test_git_task_type_excludes_unrelated(self):
        f = _build_rules_filter("git")
        assert f("deploy-pipeline") is False

    def test_testing_task_type_allows_test_rules(self):
        f = _build_rules_filter("testing")
        assert f("test-style") is True
        assert f("quality-gate") is True

    def test_security_task_type_allows_security_rules(self):
        f = _build_rules_filter("security")
        assert f("security-policy") is True
        assert f("coding-best-practices") is True

    def test_case_insensitive_matching(self):
        f = _build_rules_filter("git")
        assert f("Git-Workflow") is True
        assert f("GIT_COMMIT") is True

    def test_hyphen_normalized_to_underscore(self):
        f = _build_rules_filter("code_review")
        assert f("coding-quality") is True

    def test_all_known_task_types_have_keywords(self):
        for task_type in _RULES_TASK_KEYWORDS:
            f = _build_rules_filter(task_type)
            keywords = _RULES_TASK_KEYWORDS[task_type]
            # At least one keyword should match a stem containing the keyword
            assert f(keywords[0]) is True


# ---------------------------------------------------------------------------
# TestContextInit
# ---------------------------------------------------------------------------


class TestContextInit:
    def test_defaults(self, tmp_path):
        ctx = Context(cwd=str(tmp_path))
        assert ctx.model == "glm-4-flash"
        assert ctx.thinking_enabled is False
        assert ctx.reasoning_effort is None
        assert ctx.intent_gate is False
        assert ctx.task_type == "general"

    def test_cwd_defaults_to_cwd(self):
        ctx = Context()
        assert ctx.cwd is not None
        assert Path(ctx.cwd).is_absolute()

    def test_explicit_cwd(self, tmp_path):
        ctx = Context(cwd=str(tmp_path))
        assert ctx.cwd == str(tmp_path)

    def test_model_is_stored(self, tmp_path):
        ctx = Context(cwd=str(tmp_path), model="gpt-4o")
        assert ctx.model == "gpt-4o"

    def test_thinking_enabled_stored(self, tmp_path):
        ctx = Context(cwd=str(tmp_path), thinking_enabled=True)
        assert ctx.thinking_enabled is True

    def test_intent_gate_stored(self, tmp_path):
        ctx = Context(cwd=str(tmp_path), intent_gate=True)
        assert ctx.intent_gate is True

    def test_agent_system_prompt_stored(self, tmp_path):
        ctx = Context(cwd=str(tmp_path), agent_system_prompt="You are a reviewer.")
        assert ctx.agent_system_prompt == "You are a reviewer."

    def test_agent_name_loads_spec_when_available(self, tmp_path):
        with patch("prax.core.config_files.load_agent_spec") as mock_load:
            mock_spec = MagicMock()
            mock_spec.model = "spec-model"
            mock_spec.system_prompt = "spec system prompt"
            mock_load.return_value = mock_spec

            ctx = Context(cwd=str(tmp_path), agent_name="code-reviewer")

        assert ctx.model == "spec-model"
        assert ctx.agent_system_prompt == "spec system prompt"

    def test_agent_name_spec_not_found_is_noop(self, tmp_path):
        with patch("prax.core.config_files.load_agent_spec", return_value=None):
            ctx = Context(cwd=str(tmp_path), model="default-m", agent_name="nonexistent")

        assert ctx.model == "default-m"
        assert ctx.agent_system_prompt is None

    def test_explicit_system_prompt_not_overridden_by_spec(self, tmp_path):
        """If agent_system_prompt is already set, spec should not override it."""
        with patch("prax.core.config_files.load_agent_spec") as mock_load:
            mock_spec = MagicMock()
            mock_spec.model = "spec-model"
            mock_spec.system_prompt = "spec prompt"
            mock_load.return_value = mock_spec

            ctx = Context(
                cwd=str(tmp_path),
                agent_name="code-reviewer",
                agent_system_prompt="my explicit prompt",
            )

        assert ctx.agent_system_prompt == "my explicit prompt"


# ---------------------------------------------------------------------------
# TestBuildSystemPrompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_contains_cwd(self, tmp_path):
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert str(tmp_path) in prompt

    def test_contains_base_instructions(self, tmp_path):
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        # The SYSTEM_PROMPT_TEMPLATE text should appear
        assert "orchestration" in prompt.lower() or "coding assistant" in prompt.lower()

    def test_intent_gate_appended_when_enabled(self, tmp_path):
        ctx = _make_context(tmp_path, intent_gate=True)
        prompt = ctx.build_system_prompt()
        # INTENT_GATE_PROMPT content should be present
        assert "Step 0" in prompt or "Intent" in prompt or "意图" in prompt

    def test_intent_gate_not_in_prompt_when_disabled(self, tmp_path):
        ctx = _make_context(tmp_path, intent_gate=False)
        prompt = ctx.build_system_prompt()
        assert "Step 0" not in prompt

    def test_agent_system_prompt_appended(self, tmp_path):
        ctx = _make_context(tmp_path, agent_system_prompt="You are a security expert.")
        prompt = ctx.build_system_prompt()
        assert "You are a security expert." in prompt

    def test_agent_system_prompt_not_in_prompt_when_none(self, tmp_path):
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "## Agent Role" not in prompt


# ---------------------------------------------------------------------------
# TestLoadLocalContext — rule files
# ---------------------------------------------------------------------------


class TestLoadLocalContextRules:
    def test_claude_rules_dir_loaded(self, tmp_path):
        rule_file = tmp_path / ".claude" / "rules" / "coding.md"
        _write_file(rule_file, "# Coding Standard\nUse type hints.")
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "Use type hints" in prompt

    def test_prax_rules_dir_loaded(self, tmp_path):
        rule_file = tmp_path / ".prax" / "rules" / "security.md"
        _write_file(rule_file, "# Security Policy\nNo hardcoded keys.")
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "No hardcoded keys" in prompt

    def test_rules_filtered_by_task_type(self, tmp_path):
        """A rule file with an irrelevant name is excluded for a specific task_type."""
        deploy_rule = tmp_path / ".claude" / "rules" / "deploy-pipeline.md"
        _write_file(deploy_rule, "# Deploy\nDeploy to prod.")
        ctx = _make_context(tmp_path, task_type="testing")
        prompt = ctx.build_system_prompt(task_type="testing")
        # "deploy" is not in the testing keyword list
        assert "Deploy to prod" not in prompt

    def test_general_task_type_loads_all_rules(self, tmp_path):
        rule1 = tmp_path / ".claude" / "rules" / "any-rule.md"
        _write_file(rule1, "# Any Rule\nArbitrary content.")
        ctx = _make_context(tmp_path, task_type="general")
        prompt = ctx.build_system_prompt(task_type="general")
        assert "Arbitrary content" in prompt


# ---------------------------------------------------------------------------
# TestLoadLocalContext — project context files
# ---------------------------------------------------------------------------


class TestLoadLocalContextProjectFiles:
    def test_claude_md_loaded(self, tmp_path):
        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        _write_file(claude_md, "# My Project\nProject specific rules here.")
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "Project specific rules here" in prompt

    def test_prax_context_yaml_loaded(self, tmp_path):
        ctx_yaml = tmp_path / ".prax" / "context.yaml"
        _write_file(ctx_yaml, "project: myapp\nversion: 1.0")
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "myapp" in prompt

    def test_missing_context_files_ignored(self, tmp_path):
        """No CLAUDE.md, no context.yaml — prompt still builds without error."""
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ---------------------------------------------------------------------------
# TestLoadRecentEpisodicMemory
# ---------------------------------------------------------------------------


class TestLoadRecentEpisodicMemory:
    def test_episodic_memory_injected(self, tmp_path):
        sessions_dir = tmp_path / ".prax" / "sessions"
        sessions_dir.mkdir(parents=True)
        facts_file = sessions_dir / "2026-04-13-facts.json"
        facts_file.write_text(json.dumps({
            "date": "2026-04-13",
            "facts": [
                {"content": "Deployed new auth service", "category": "deploy"},
            ],
        }), encoding="utf-8")

        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "Deployed new auth service" in prompt

    def test_empty_sessions_dir_is_silent(self, tmp_path):
        sessions_dir = tmp_path / ".prax" / "sessions"
        sessions_dir.mkdir(parents=True)
        ctx = _make_context(tmp_path)
        # Should not raise, episodic section simply absent
        prompt = ctx.build_system_prompt()
        assert "Recent Episodic Memory" not in prompt

    def test_malformed_facts_file_is_skipped(self, tmp_path):
        sessions_dir = tmp_path / ".prax" / "sessions"
        sessions_dir.mkdir(parents=True)
        bad_file = sessions_dir / "2026-04-12-facts.json"
        bad_file.write_text("not json at all", encoding="utf-8")
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        # Should not raise, bad file silently skipped
        assert isinstance(prompt, str)

    def test_facts_without_content_field_skipped(self, tmp_path):
        sessions_dir = tmp_path / ".prax" / "sessions"
        sessions_dir.mkdir(parents=True)
        facts_file = sessions_dir / "2026-04-11-facts.json"
        facts_file.write_text(json.dumps({
            "date": "2026-04-11",
            "facts": [{"category": "context"}],  # no content field
        }), encoding="utf-8")
        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        # Should not raise
        assert isinstance(prompt, str)

    def test_episodic_memory_section_header(self, tmp_path):
        sessions_dir = tmp_path / ".prax" / "sessions"
        sessions_dir.mkdir(parents=True)
        facts_file = sessions_dir / "2026-04-13-facts.json"
        facts_file.write_text(json.dumps({
            "date": "2026-04-13",
            "facts": [
                {"content": "Finished refactor", "category": "code"},
            ],
        }), encoding="utf-8")

        ctx = _make_context(tmp_path)
        prompt = ctx.build_system_prompt()
        assert "Recent Episodic Memory" in prompt


# ---------------------------------------------------------------------------
# TestContextMemoryBackendIntegration
# ---------------------------------------------------------------------------


class TestContextMemoryBackendIntegration:
    def test_memory_backend_knowledge_graph_injected(self, tmp_path):
        mock_backend = MagicMock()
        mock_kg = MagicMock()
        mock_backend.get_knowledge_graph.return_value = mock_kg

        with patch("prax.core.context.LayeredInjector") as MockInjector:
            mock_injector = MagicMock()
            mock_injector.build_sync.return_value = "## Knowledge Graph\nSome KG data."
            MockInjector.return_value = mock_injector

            ctx = _make_context(tmp_path, memory_backend=mock_backend)
            prompt = ctx.build_system_prompt()

        assert "Knowledge Graph" in prompt or "KG data" in prompt

    def test_memory_backend_exception_is_silenced(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.get_knowledge_graph.side_effect = RuntimeError("backend error")

        ctx = _make_context(tmp_path, memory_backend=mock_backend)
        # Should not raise
        prompt = ctx.build_system_prompt()
        assert isinstance(prompt, str)
