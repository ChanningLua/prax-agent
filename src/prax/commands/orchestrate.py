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

from ..core.approval_policy import build_relay_gate
from ..core.claude_step_executor import ClaudeStepExecutor
from ..core.command_verifier import CommandVerifier
from ..core.config_files import load_approval_config
from ..core.context_composer import ContextComposer
from ..core.orchestrator_loop import OrchestrationOutcome, OrchestratorLoop
from ..core.run_journal import RunJournal


def _make_notifier(cwd, channel):
    """A best-effort (title, body) -> None notifier bound to a notify.yaml channel.

    Failure NEVER propagates — an unattended run must not crash because a
    notification couldn't be delivered; it logs and carries on (the approval
    still parks on the relay, reachable by its link).
    """
    if not channel:
        return None

    def notify(title: str, body: str) -> None:
        try:
            import asyncio

            from ..core.config_files import load_notify_config
            from ..tools.notify import build_provider

            cfg = load_notify_config(cwd).get("channels", {}).get(channel)
            if not cfg:
                print(
                    f"[prax] approval: notify channel {channel!r} not in notify.yaml",
                    file=sys.stderr,
                )
                return
            asyncio.run(build_provider(cfg).send(title=title, body=body, level="warn"))
        except Exception as exc:  # never let notify failure break the run
            print(f"[prax] approval: notify failed: {exc}", file=sys.stderr)

    return notify


def handle_orchestrate(
    cwd, args, *, executor=None, verifier=None, approval_gate=None
) -> OrchestrationOutcome:
    """Parse ``orchestrate`` args, assemble the loop, run it, return the outcome.

    ``executor`` / ``verifier`` / ``approval_gate`` are injectable so the
    assembly + run is testable without a real ``claude -p`` or a live relay; in
    production the executor defaults to the claude adapter, the verifier to a
    CommandVerifier (when ``--verify`` is given), and the approval gate is built
    from the ``approval:`` config block (absent → no gating, fully allow-all).
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
    # "completed_no_verify" instead of a hollow "verified" (G3). That's correct
    # for a pure monitoring/heartbeat goal, but for real work it means the loop
    # runs ONE step then stops with no self-heal and no authoritative done-signal
    # — the most common "it looks stuck / never makes progress" footgun. Warn
    # loudly (stderr) so an unattended operator sees WHY, without forcing a
    # verifier (which would break legitimate no-verify monitoring goals).
    if verifier is None:
        print(
            "[prax] orchestrate: no verifier — running UNCHECKED: one step, no "
            "self-heal, stops as 'completed_no_verify' (verified=False). For work "
            "that should loop-until-done, pass --verify '<cmd>' (e.g. "
            "--verify 'pytest -q' or an acceptance script).",
            file=sys.stderr,
        )

    # Production-approval gate: built from the ``approval:`` config block when an
    # operator has opted in (relay_url + admin_token). Absent → None → no gating
    # (fully allow-all). approval_id = run_id so a resumed run re-checks the SAME
    # approval rather than parking a new one each window (park-and-continue).
    if approval_gate is None:
        appcfg = load_approval_config(cwd)
        if appcfg:
            approval_gate = build_relay_gate(
                relay_url=appcfg["relay_url"],
                admin_token=appcfg["admin_token"],
                approval_id=run_id,
                deny_patterns=appcfg["deny_patterns"],
                notify=_make_notifier(cwd, appcfg["notify_channel"]),
            )

    loop = OrchestratorLoop(
        journal=journal,
        executor=executor,
        verifier=verifier,
        max_iterations=ns.max_iterations,
        compose=ContextComposer(cwd, reinject_every=ns.reinject_every),
        approval_gate=approval_gate,
    )
    return loop.run(goal)
