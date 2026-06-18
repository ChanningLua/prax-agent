"""``prax orchestrate <goal>`` — run the 7x24 outer loop once on a goal.

Assembles the durable file memory (ContextComposer + RunJournal) + a verifier +
the black-box ``claude -p`` executor into an OrchestratorLoop and runs it. Used
directly from the CLI, or driven by a cron job with ``run_mode: orchestrate``
(the 7x24 heartbeat — one bounded window per fire; it resumes from the journal).
"""
from __future__ import annotations

import argparse
import sys
import uuid

from ..core.claude_step_executor import ClaudeStepExecutor
from ..core.command_verifier import CommandVerifier
from ..core.context_composer import ContextComposer
from ..core.orchestrator_loop import OrchestrationOutcome, OrchestratorLoop
from ..core.run_journal import RunJournal


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
    if verifier is None and ns.verify:
        try:
            verifier = CommandVerifier(ns.verify, cwd=cwd)
        except ValueError as exc:
            # Don't crash an (unattended) run on a bad --verify command (G2).
            print(
                f"[prax] orchestrate: invalid --verify {ns.verify!r}: {exc}",
                file=sys.stderr,
            )
            return OrchestrationOutcome(stop_reason="bad_verifier", iterations=0, verified=False)
    # No --verify and none injected → verifier stays None; the loop reports
    # "completed_no_verify" instead of a hollow "verified" (G3).

    loop = OrchestratorLoop(
        journal=journal,
        executor=executor,
        verifier=verifier,
        max_iterations=ns.max_iterations,
        compose=ContextComposer(cwd, reinject_every=ns.reinject_every),
    )
    return loop.run(goal)
