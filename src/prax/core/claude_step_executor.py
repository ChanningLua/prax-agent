"""Claude step executor — runs each orchestrator step as a black-box `claude -p`.

Adapts prax's existing (async, already-tested) ``ClaudeCliExecutor`` to the
synchronous ``Executor`` protocol the OrchestratorLoop expects.

Fresh-context-per-task: continuity comes from the loop's file memory
(ContextComposer + RunJournal), not a long-lived claude session — so each step
is a clean ``claude -p`` invocation (Ralph / Anthropic long-running-harness
pattern).

Auth-agnostic by construction: the underlying CLI inherits the process env, so
the same adapter rides a logged-in Claude subscription OR an ``ANTHROPIC_API_KEY``
with no code change (design R1 hedge).

Cost comes from the result event's top-level ``total_cost_usd`` (confirmed via
the M0 spike against the real CLI — it is NOT inside ``usage``); ClaudeCliExecutor
surfaces it as ``ExecutionResult.cost_usd``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .claude_cli_executor import ClaudeCliExecutor
from .orchestrator_loop import StepResult


class ClaudeStepExecutor:
    """``Executor`` protocol impl backed by ``claude -p`` (via ClaudeCliExecutor)."""

    def __init__(
        self,
        cwd: str,
        *,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        cli: Any | None = None,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._permission_mode = permission_mode
        self._cli = cli or ClaudeCliExecutor()

    def run(self, instruction: str, *, session_id: str | None = None) -> StepResult:
        result = asyncio.run(
            self._cli.run(
                instruction,
                session_id=session_id,
                model=self._model,
                permission_mode=self._permission_mode,
                cwd=self._cwd,
            )
        )
        return StepResult(
            text=result.text,
            session_id=result.session_id,
            cost_usd=result.cost_usd,
        )
