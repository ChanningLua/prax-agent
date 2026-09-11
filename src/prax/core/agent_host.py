"""Resolve the coding-agent host used by Prax Shift."""

from __future__ import annotations

import os
from typing import Any

from .claude_cli_executor import is_available as claude_available
from .codex_cli_executor import is_available as codex_available

SUPPORTED_AGENT_HOSTS = ("auto", "claude", "codex")


def resolve_agent_host(requested: str | None = None) -> str:
    selected = (requested or os.environ.get("PRAX_AGENT_HOST") or "auto").lower()
    if selected not in SUPPORTED_AGENT_HOSTS:
        raise ValueError(
            f"Unsupported agent host {selected!r}; choose auto, claude, or codex"
        )
    if selected == "auto":
        # Preserve the historical Claude preference when both CLIs exist, while
        # making a Codex-only workstation work without extra configuration.
        if claude_available():
            return "claude"
        if codex_available():
            return "codex"
        raise RuntimeError("No supported coding-agent CLI found (claude or codex)")
    available = claude_available() if selected == "claude" else codex_available()
    if not available:
        raise RuntimeError(f"Requested agent host {selected!r} is not installed or not on PATH")
    return selected


def build_step_executor(
    cwd: str,
    *,
    agent_host: str | None = None,
    model: str | None = None,
) -> tuple[str, Any]:
    resolved = resolve_agent_host(agent_host)
    if resolved == "claude":
        from .claude_step_executor import ClaudeStepExecutor

        return resolved, ClaudeStepExecutor(cwd, model=model)

    from .codex_step_executor import CodexStepExecutor

    return resolved, CodexStepExecutor(cwd, model=model)
