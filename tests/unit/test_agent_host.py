"""Agent-host resolution for Prax Shift."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from prax.core.agent_host import build_step_executor, resolve_agent_host
from prax.core.claude_step_executor import ClaudeStepExecutor
from prax.core.codex_step_executor import CodexStepExecutor


def test_auto_preserves_claude_preference():
    with (
        patch("prax.core.agent_host.claude_available", return_value=True),
        patch("prax.core.agent_host.codex_available", return_value=True),
    ):
        assert resolve_agent_host("auto") == "claude"


def test_auto_falls_back_to_codex():
    with (
        patch("prax.core.agent_host.claude_available", return_value=False),
        patch("prax.core.agent_host.codex_available", return_value=True),
    ):
        assert resolve_agent_host("auto") == "codex"


def test_explicit_host_must_exist():
    with (
        patch("prax.core.agent_host.codex_available", return_value=False),
        pytest.raises(RuntimeError, match="codex"),
    ):
        resolve_agent_host("codex")


def test_environment_selects_host(monkeypatch):
    monkeypatch.setenv("PRAX_AGENT_HOST", "codex")
    with patch("prax.core.agent_host.codex_available", return_value=True):
        assert resolve_agent_host() == "codex"


def test_builds_matching_executor(tmp_path):
    with patch("prax.core.agent_host.claude_available", return_value=True):
        host, executor = build_step_executor(str(tmp_path), agent_host="claude")
        assert host == "claude"
        assert isinstance(executor, ClaudeStepExecutor)

    with patch("prax.core.agent_host.codex_available", return_value=True):
        host, executor = build_step_executor(str(tmp_path), agent_host="codex")
        assert host == "codex"
        assert isinstance(executor, CodexStepExecutor)


def test_rejects_unknown_host():
    with pytest.raises(ValueError, match="Unsupported agent host"):
        resolve_agent_host("other")
