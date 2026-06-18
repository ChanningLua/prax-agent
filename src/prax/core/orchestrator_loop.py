"""Orchestrator loop — the 7x24 outer "brain" that drives a black-box executor.

Each iteration: compose a step instruction (goal + last verification feedback)
-> hand the whole sub-task to an injected ``executor`` (e.g. a ``claude -p``
adapter; tests use a FakeExecutor) -> run a ``verifier`` (prax runs the
tests/acceptance check itself) -> if it fails, feed the failure back into the
next instruction (self-heal); if it passes, stop. Bounded by ``max_iterations``.

Everything is recorded to a :class:`~prax.core.run_journal.RunJournal` so a
crashed / quota-stopped run can resume: a run whose journal already holds a
verified outcome short-circuits without re-running the executor.

The executor is behind a tiny interface on purpose — the loop never executes
tools itself (that's the black-box agent's job), and the backend (subscription
``claude -p`` vs an API key) stays swappable (see design R1 auth-agnostic hedge).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class StepResult:
    """What one executor invocation returns."""

    text: str
    session_id: str | None = None
    cost_usd: float | None = None


@dataclass
class VerifyResult:
    """Outcome of one verification check."""

    passed: bool
    output: str = ""


@dataclass
class OrchestrationOutcome:
    """Terminal result of a loop run."""

    stop_reason: str  # verified | max_iterations | completed_no_verify
    #                   | awaiting_approval | approval_rejected
    iterations: int
    verified: bool


class Executor(Protocol):
    """A black-box step executor (the claude -p adapter, or a FakeExecutor)."""

    def run(self, instruction: str, *, session_id: str | None = ...) -> StepResult: ...


Composer = Callable[[str, str | None, int], str]
Verifier = Callable[[], VerifyResult]
# Given the goal, decide whether work may be dispatched. Returns one of
# "approved" | "pending" | "rejected" (any other value is treated as pending,
# fail-safe). Non-prod goals return "approved" immediately; prod-affecting goals
# go through the remote approval relay (see core.approval_policy).
ApprovalGate = Callable[[str], str]


def _default_compose(goal: str, feedback: str | None, iteration: int) -> str:
    if not feedback:
        return goal
    return f"{goal}\n\n[上一轮验证未通过，请据此修复]\n{feedback}"


class OrchestratorLoop:
    """Loop-until-verified over a black-box executor, journalled + resumable."""

    def __init__(
        self,
        *,
        journal: Any,
        executor: Executor,
        verifier: Verifier | None,
        max_iterations: int = 10,
        compose: Composer | None = None,
        session_id: str | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        self._journal = journal
        self._executor = executor
        self._verifier = verifier
        self._max_iterations = max_iterations
        self._compose = compose or _default_compose
        self._session_id = session_id
        self._approval_gate = approval_gate

    def run(self, goal: str) -> OrchestrationOutcome:
        # ── Resume: a journal that already verified short-circuits ──────────
        prior = self._journal.result_for("outcome")
        if isinstance(prior, dict) and prior.get("verified"):
            return OrchestrationOutcome(
                stop_reason=str(prior.get("stop_reason", "verified")),
                iterations=int(prior.get("iterations", 0)),
                verified=True,
            )

        # ── Resume: continue the iteration count + feedback from the journal ─
        events = self._journal.events()
        iteration = sum(1 for e in events if e.get("step") == "step")
        feedback = self._last_feedback(events)

        # ── Approval gate (run-level) ────────────────────────────────────────
        # A production-affecting goal must be approved before we dispatch ANY
        # work to the black-box executor (which we can't intercept per-command).
        # Park-and-continue: "pending" stops THIS window cleanly (journalled) and
        # a later resume re-checks the gate — the run never blocks inline waiting
        # for a human. Fail-safe: anything other than "approved"/"rejected" parks.
        if self._approval_gate is not None:
            decision = self._approval_gate(goal)
            self._journal.record("approval", output={"decision": decision}, iteration=iteration)
            if decision == "rejected":
                return self._finish("approval_rejected", iteration, False)
            if decision != "approved":
                return self._finish("awaiting_approval", iteration, False)

        # ── Main loop ───────────────────────────────────────────────────────
        while iteration < self._max_iterations:
            instruction = self._compose(goal, feedback, iteration)
            step = self._executor.run(instruction, session_id=self._session_id)
            self._session_id = step.session_id or self._session_id
            self._journal.record(
                "step",
                output={"text": step.text, "session_id": step.session_id},
                instruction=instruction,
                iteration=iteration,
            )

            # No verifier configured → be honest: the work ran but nothing was
            # checked. Report "completed_no_verify" (verified=False), never a
            # hollow "verified" (G3).
            if self._verifier is None:
                return self._finish("completed_no_verify", iteration + 1, False)

            verdict = self._verifier()
            self._journal.record(
                "verify",
                output={"passed": verdict.passed, "output": verdict.output},
                iteration=iteration,
            )
            iteration += 1

            if verdict.passed:
                return self._finish("verified", iteration, True)
            feedback = verdict.output

        return self._finish("max_iterations", iteration, False)

    # -- helpers --------------------------------------------------------------

    def _finish(self, stop_reason: str, iterations: int, verified: bool) -> OrchestrationOutcome:
        self._journal.record(
            "outcome",
            output={"stop_reason": stop_reason, "iterations": iterations, "verified": verified},
        )
        return OrchestrationOutcome(stop_reason=stop_reason, iterations=iterations, verified=verified)

    @staticmethod
    def _last_feedback(events: list[dict[str, Any]]) -> str | None:
        """Feedback to seed a resumed loop: output of the last *failed* verify."""
        feedback: str | None = None
        for e in events:
            if e.get("step") == "verify":
                out = e.get("output") or {}
                feedback = None if out.get("passed") else out.get("output")
        return feedback
