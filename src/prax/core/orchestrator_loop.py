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

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


VERIFY_PASS = "PASS"
VERIFY_FEATURE_FAIL = "FEATURE-FAIL"
VERIFY_HARNESS_ERROR = "HARNESS-ERROR"
_VERIFY_STATUSES = frozenset({VERIFY_PASS, VERIFY_FEATURE_FAIL, VERIFY_HARNESS_ERROR})


@dataclass
class StepResult:
    """What one executor invocation returns."""

    text: str
    session_id: str | None = None
    cost_usd: float | None = None


@dataclass
class VerifyResult:
    """Outcome of one verification check with an explicit three-state verdict.

    ``passed`` remains for backward compatibility with existing custom
    verifiers. When ``status`` is omitted it is derived as PASS/FEATURE-FAIL.
    HARNESS-ERROR is explicit: it means the acceptance environment or verifier
    failed, not that the product behavior is wrong.
    """

    passed: bool
    output: str = ""
    status: str = ""

    def __post_init__(self) -> None:
        status = (self.status or (VERIFY_PASS if self.passed else VERIFY_FEATURE_FAIL)).upper()
        if status not in _VERIFY_STATUSES:
            raise ValueError(f"Unknown verification status: {self.status!r}")
        if self.passed != (status == VERIFY_PASS):
            raise ValueError(f"passed={self.passed} conflicts with verification status {status}")
        self.status = status


@dataclass
class OrchestrationOutcome:
    """Terminal result of a loop run."""

    stop_reason: str  # verified | max_iterations | completed_no_verify
    #                   | awaiting_approval | approval_rejected | executor_error
    #                   | stuck_no_progress | harness_error
    #                   | approval_unconfigured
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
class ApprovalGate(Protocol):
    def __call__(
        self,
        goal: str,
        *,
        required: bool = False,
        approval_id_override: str | None = None,
    ) -> str: ...


def _default_compose(goal: str, feedback: str | None, iteration: int) -> str:
    if not feedback:
        return goal
    return f"{goal}\n\n[上一轮验证未通过，请据此修复]\n{feedback}"


_VOLATILE_DURATION = re.compile(r"\bin \d+\.\d+s\b")
_HEX_ADDR = re.compile(r"0x[0-9a-fA-F]+")


def _normalize_failure(text: str) -> str:
    """Normalize a verify-failure for 'is this the SAME failure again?' compares.

    Strips only non-semantic volatility (run duration, hex addresses) and
    collapses whitespace, while PRESERVING failure counts / test names /
    assertion text. So a shrinking failure count ("5 failed" -> "3 failed" =
    progress) is NOT seen as identical, but "1 failed in 0.03s" vs
    "1 failed in 0.14s" is.
    """
    t = _VOLATILE_DURATION.sub("in #s", text or "")
    t = _HEX_ADDR.sub("0x#", t)
    return " ".join(t.split()).strip()


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
        approval_required: bool = False,
        approval_id_override: str | None = None,
        error_limit: int = 3,
        stuck_after: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._journal = journal
        self._executor = executor
        self._verifier = verifier
        self._max_iterations = max_iterations
        self._compose = compose or _default_compose
        self._session_id = session_id
        self._approval_gate = approval_gate
        self._approval_required = approval_required
        self._approval_id_override = approval_id_override
        # D3: how many *consecutive* executor crashes to tolerate (with backoff)
        # before stopping the window with "executor_error". ``sleep`` is injected
        # so tests don't actually wait out the backoff.
        self._error_limit = max(1, error_limit)
        # §8.1-7: stop early after this many CONSECUTIVE identical verify
        # failures (no progress). < 2 disables stuck detection.
        self._stuck_after = stuck_after
        self._sleep = sleep

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
        # A HARNESS-ERROR is an environment/verifier failure, not a coding-loop
        # attempt. Do not let repeated infrastructure outages exhaust the
        # feature's max-iteration budget across resume windows.
        iteration -= sum(
            1
            for e in events
            if e.get("step") == "verify"
            and (e.get("output") or {}).get("status") == VERIFY_HARNESS_ERROR
        )
        iteration = max(0, iteration)
        feedback = self._last_feedback(events)

        # ── Approval gate (run-level) ────────────────────────────────────────
        # A production-affecting goal must be approved before we dispatch ANY
        # work to the black-box executor (which we can't intercept per-command).
        # Park-and-continue: "pending" stops THIS window cleanly (journalled) and
        # a later resume re-checks the gate — the run never blocks inline waiting
        # for a human. Fail-safe: anything other than "approved"/"rejected" parks.
        if self._approval_required and self._approval_gate is None:
            # Explicit decision red line with no relay configured: fail closed.
            # Running anyway would turn a missing control-plane config into a
            # silent autonomy escalation.
            self._journal.record(
                "approval",
                output={"decision": "unconfigured", "required": True},
                iteration=iteration,
            )
            return self._finish("approval_unconfigured", iteration, False)

        if self._approval_gate is not None:
            try:
                gate_kwargs: dict[str, Any] = {}
                if self._approval_required:
                    gate_kwargs["required"] = True
                if self._approval_id_override:
                    gate_kwargs["approval_id_override"] = self._approval_id_override
                decision = self._approval_gate(goal, **gate_kwargs)
            except TypeError as exc:
                # A legacy/injected gate that cannot honor the explicit
                # ``required`` contract must not accidentally approve red-line
                # work. Park it as an unconfigured control plane.
                if not self._approval_required:
                    raise
                self._journal.record(
                    "approval",
                    output={"decision": "unconfigured", "required": True, "error": repr(exc)},
                    iteration=iteration,
                )
                return self._finish("approval_unconfigured", iteration, False)
            self._journal.record(
                "approval",
                output={
                    "decision": decision,
                    "required": self._approval_required,
                    "approval_id": self._approval_id_override,
                },
                iteration=iteration,
            )
            if decision == "rejected":
                return self._finish("approval_rejected", iteration, False)
            if decision != "approved":
                return self._finish("awaiting_approval", iteration, False)

        # ── Main loop ───────────────────────────────────────────────────────
        consecutive_errors = 0
        stuck_streak = 0
        last_fail_norm: str | None = None
        while iteration < self._max_iterations:
            instruction = self._compose(goal, feedback, iteration)

            # D3: a black-box `claude -p` step can fail hard (rate limit, network,
            # not-logged-in → Python traceback). An unattended 7x24 window must
            # NOT crash on it: journal the error, back off, and retry it as the
            # next turn. After `error_limit` CONSECUTIVE failures, stop cleanly
            # with "executor_error" (a tooling problem, distinct from a code
            # problem / max_iterations) so the operator is signalled rather than
            # the run dying with a traceback.
            try:
                step = self._executor.run(instruction, session_id=self._session_id)
            except Exception as exc:  # noqa: BLE001 — must catch all to stay alive
                consecutive_errors += 1
                self._journal.record(
                    "step_error",
                    output={"error": repr(exc), "attempt": consecutive_errors},
                    instruction=instruction,
                    iteration=iteration,
                )
                if consecutive_errors >= self._error_limit:
                    return self._finish("executor_error", iteration, False)
                self._sleep(min(2 ** (consecutive_errors - 1), 30))
                feedback = (
                    f"上一轮执行器报错（已重试 {consecutive_errors} 次），"
                    f"请据此调整或直接重试：{exc}"
                )
                continue

            consecutive_errors = 0
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
                output={
                    "passed": verdict.passed,
                    "status": verdict.status,
                    "output": verdict.output,
                },
                iteration=iteration,
            )
            iteration += 1

            if verdict.passed:
                return self._finish("verified", iteration, True)
            if verdict.status == VERIFY_HARNESS_ERROR:
                # Environment/verifier failures are not product failures. Do
                # not feed them to the coding agent or burn the retry budget;
                # park this window so the harness can be repaired, then resume.
                return self._finish("harness_error", iteration, False)
            feedback = verdict.output

            # Stuck detection (§8.1-7): if the verifier keeps failing with the
            # SAME (normalized) output, the executor is making no progress — stop
            # EARLY with "stuck_no_progress" rather than burning the whole budget
            # re-feeding an identical failure (the operator should look). Guarded
            # by `iteration < max_iterations` so a coincident ceiling stays
            # "max_iterations" — this only ever stops things EARLY.
            norm = _normalize_failure(verdict.output)
            if norm == last_fail_norm:
                stuck_streak += 1
            else:
                stuck_streak = 1
                last_fail_norm = norm
            if (
                self._stuck_after >= 2
                and stuck_streak >= self._stuck_after
                and iteration < self._max_iterations
            ):
                return self._finish("stuck_no_progress", iteration, False)

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
                status = out.get("status")
                if out.get("passed") or status == VERIFY_HARNESS_ERROR:
                    feedback = None
                else:
                    # Old journals have no status; their false result retains
                    # the historical FEATURE-FAIL/self-heal behavior.
                    feedback = out.get("output")
        return feedback
