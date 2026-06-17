"""Unit tests for prax/core/orchestrator_loop.py.

The loop is tested against a FakeExecutor + a scripted verifier so the whole
outer-loop contract is exercised WITHOUT a real `claude -p` (the executor is
injected behind an interface).
"""
from __future__ import annotations

from prax.core.run_journal import RunJournal
from prax.core.orchestrator_loop import (
    OrchestratorLoop,
    StepResult,
    VerifyResult,
)


class FakeExecutor:
    """Records the instructions it's asked to run; returns canned StepResults."""

    def __init__(self) -> None:
        self.instructions: list[str] = []

    def run(self, instruction: str, *, session_id: str | None = None) -> StepResult:
        self.instructions.append(instruction)
        return StepResult(text=f"ran: {instruction[:24]}", session_id="sess_1")


def _scripted_verifier(results: list[VerifyResult]):
    """A verifier() that yields the given VerifyResults, then passes forever."""
    seq = iter(results)

    def _v() -> VerifyResult:
        try:
            return next(seq)
        except StopIteration:
            return VerifyResult(passed=True)

    return _v


class TestOrchestratorLoop:
    def test_passes_on_first_try(self, tmp_path):
        ex = FakeExecutor()
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "r1"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            max_iterations=5,
        )
        outcome = loop.run("做一个登录页")

        assert outcome.verified is True
        assert outcome.stop_reason == "verified"
        assert outcome.iterations == 1
        assert len(ex.instructions) == 1
        assert ex.instructions[0] == "做一个登录页"  # no feedback on first turn

    def test_self_heals_then_passes_threading_feedback(self, tmp_path):
        ex = FakeExecutor()
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "r2"),
            executor=ex,
            verifier=_scripted_verifier(
                [VerifyResult(passed=False, output="test_login FAILED")]
            ),
            max_iterations=5,
        )
        outcome = loop.run("做一个登录页")

        assert outcome.verified is True
        assert outcome.iterations == 2
        assert len(ex.instructions) == 2
        # the failure output is fed back into the next instruction (self-heal)
        assert "test_login FAILED" in ex.instructions[1]

    def test_stops_at_max_iterations_when_never_verified(self, tmp_path):
        ex = FakeExecutor()
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "r3"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=False, output="nope")] * 10),
            max_iterations=3,
        )
        outcome = loop.run("永远过不了的目标")

        assert outcome.verified is False
        assert outcome.stop_reason == "max_iterations"
        assert outcome.iterations == 3
        assert len(ex.instructions) == 3

    def test_journal_records_steps_and_outcome(self, tmp_path):
        journal = RunJournal(str(tmp_path), "r4")
        loop = OrchestratorLoop(
            journal=journal,
            executor=FakeExecutor(),
            verifier=_scripted_verifier(
                [VerifyResult(passed=False, output="x"), VerifyResult(passed=True)]
            ),
            max_iterations=5,
        )
        loop.run("目标")

        steps = journal.done_steps()
        assert "step" in steps
        assert "verify" in steps
        assert "outcome" in steps
        assert journal.result_for("outcome")["verified"] is True

    def test_resume_short_circuits_when_already_verified(self, tmp_path):
        journal = RunJournal(str(tmp_path), "r5")
        OrchestratorLoop(
            journal=journal,
            executor=FakeExecutor(),
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            max_iterations=5,
        ).run("目标")

        # fresh loop, same journal (crash + restart): must NOT re-run the executor
        ex2 = FakeExecutor()
        outcome = OrchestratorLoop(
            journal=journal,
            executor=ex2,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            max_iterations=5,
        ).run("目标")

        assert outcome.verified is True
        assert outcome.stop_reason == "verified"
        assert len(ex2.instructions) == 0  # short-circuited via journal replay
