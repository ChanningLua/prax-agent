"""``prax orchestrate <goal>`` — run the 7x24 outer loop once on a goal.

Assembles the durable file memory (ContextComposer + RunJournal) + a verifier +
the black-box ``claude -p`` executor into an OrchestratorLoop and runs it. Used
directly from the CLI, or driven by a cron job with ``run_mode: orchestrate``
(the 7x24 heartbeat — one bounded window per fire; it resumes from the journal).
"""
from __future__ import annotations

import argparse
import uuid

from ..core.claude_step_executor import ClaudeStepExecutor
from ..core.command_verifier import CommandVerifier
from ..core.context_composer import ContextComposer
from ..core.orchestrator_loop import (
    OrchestrationOutcome,
    OrchestratorLoop,
    VerifyResult,
)
from ..core.run_journal import RunJournal


def _always_pass() -> VerifyResult:
    return VerifyResult(passed=True)


def handle_orchestrate(cwd, args, *, executor=None, verifier=None) -> OrchestrationOutcome:
    """Parse ``orchestrate`` args, assemble the loop, run it, return the outcome.

    ``executor`` / ``verifier`` are injectable so the assembly + run is testable
    without a real ``claude -p``; in production they default to the claude
    executor and (if ``--verify`` is given) a CommandVerifier.
    """
    parser = argparse.ArgumentParser(prog="prax orchestrate", add_help=False)
    parser.add_argument("goal", nargs="+")
    parser.add_argument("--verify", default=None, help="verify command, e.g. 'pytest -q'")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reinject-every", type=int, default=3)
    ns = parser.parse_args(args)

    goal = " ".join(ns.goal)
    run_id = ns.run_id or f"run-{uuid.uuid4().hex[:8]}"
    journal = RunJournal(cwd, run_id)

    if executor is None:
        executor = ClaudeStepExecutor(cwd, model=ns.model)
    if verifier is None:
        verifier = CommandVerifier(ns.verify, cwd=cwd) if ns.verify else _always_pass

    loop = OrchestratorLoop(
        journal=journal,
        executor=executor,
        verifier=verifier,
        max_iterations=ns.max_iterations,
        compose=ContextComposer(cwd, reinject_every=ns.reinject_every),
    )
    return loop.run(goal)
