"""Synchronous OrchestratorLoop adapter backed by ``codex exec``."""

from __future__ import annotations

import asyncio
from typing import Any

from .codex_cli_executor import CodexCliExecutor
from .orchestrator_loop import StepResult


class CodexStepExecutor:
    """Run each Prax step in a fresh Codex context.

    Continuity belongs to Prax's ContextComposer and RunJournal, not an
    ever-growing model conversation.  ``session_id`` is therefore intentionally
    ignored by default even though the lower-level CLI adapter supports resume.
    """

    def __init__(
        self,
        cwd: str,
        *,
        model: str | None = None,
        sandbox: str = "workspace-write",
        cli: Any | None = None,
        resume_sessions: bool = False,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._sandbox = sandbox
        self._cli = cli or CodexCliExecutor()
        self._resume_sessions = resume_sessions

    def run(self, instruction: str, *, session_id: str | None = None) -> StepResult:
        result = asyncio.run(
            self._cli.run(
                instruction,
                session_id=session_id if self._resume_sessions else None,
                model=self._model,
                sandbox=self._sandbox,
                cwd=self._cwd,
            )
        )
        return StepResult(
            text=result.text,
            session_id=result.session_id,
            cost_usd=result.cost_usd,
        )
