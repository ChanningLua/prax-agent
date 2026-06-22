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

    def test_no_verifier_completes_unverified(self, tmp_path):
        # G3: verifier=None must report completed_no_verify, never a hollow verified
        ex = FakeExecutor()
        outcome = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "rn"),
            executor=ex,
            verifier=None,
            max_iterations=5,
        ).run("目标")
        assert outcome.verified is False
        assert outcome.stop_reason == "completed_no_verify"
        assert outcome.iterations == 1
        assert len(ex.instructions) == 1

    def test_executor_error_retries_then_succeeds(self, tmp_path):
        # D3: a transient executor crash must NOT kill the window — it's
        # journalled, backed off, and retried as the next turn.
        class FlakyExec:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, instruction, *, session_id=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("claude -p: rate limited")
                return StepResult(text="ran", session_id="s")

        ex = FlakyExec()
        journal = RunJournal(str(tmp_path), "e1")
        outcome = OrchestratorLoop(
            journal=journal,
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            max_iterations=5,
            sleep=lambda _s: None,  # don't actually back off in tests
        ).run("目标")

        assert outcome.verified is True
        assert outcome.stop_reason == "verified"
        assert ex.calls == 2  # crashed once, retried, then ran
        assert "step_error" in journal.done_steps()

    def test_executor_error_limit_stops_cleanly_not_crash(self, tmp_path):
        # persistent crash → "executor_error" (bounded), never a naked traceback.
        class AlwaysErrors:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, instruction, *, session_id=None):
                self.calls += 1
                raise RuntimeError("not logged in")

        ex = AlwaysErrors()
        outcome = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "e2"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            max_iterations=10,
            error_limit=3,
            sleep=lambda _s: None,
        ).run("目标")

        assert outcome.verified is False
        assert outcome.stop_reason == "executor_error"
        assert ex.calls == 3  # stopped at the consecutive-error limit

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


class TestApprovalGate:
    """Run-level approval: a prod-affecting goal must be approved before ANY
    work is dispatched to the black-box executor. Park-and-continue — pending
    stops the window cleanly and a later resume re-checks; never blocks inline."""

    def _loop(self, tmp_path, run_id, gate):
        return OrchestratorLoop(
            journal=RunJournal(str(tmp_path), run_id),
            executor=FakeExecutor(),
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=gate,
        )

    def test_pending_parks_without_executing(self, tmp_path):
        ex = FakeExecutor()
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag1"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "pending",
        )
        outcome = loop.run("部署到生产")
        assert outcome.stop_reason == "awaiting_approval"
        assert outcome.verified is False
        assert len(ex.instructions) == 0  # parked: executor never ran

    def test_rejected_stops_without_executing(self, tmp_path):
        ex = FakeExecutor()
        outcome = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag2"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "rejected",
        ).run("部署到生产")
        assert outcome.stop_reason == "approval_rejected"
        assert outcome.verified is False
        assert len(ex.instructions) == 0

    def test_unknown_decision_parks_failsafe(self, tmp_path):
        # never run prod work on an unrecognized decision
        ex = FakeExecutor()
        outcome = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag2b"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "timed_out",
        ).run("部署到生产")
        assert outcome.stop_reason == "awaiting_approval"
        assert len(ex.instructions) == 0

    def test_approved_proceeds_and_verifies(self, tmp_path):
        ex = FakeExecutor()
        outcome = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag3"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "approved",
        ).run("部署到生产")
        assert outcome.verified is True
        assert outcome.stop_reason == "verified"
        assert len(ex.instructions) == 1

    def test_gate_receives_goal(self, tmp_path):
        seen: list[str] = []
        OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag4"),
            executor=FakeExecutor(),
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: seen.append(goal) or "approved",
        ).run("部署到生产 DB")
        assert seen == ["部署到生产 DB"]

    def test_no_gate_runs_normally(self, tmp_path):
        ex = FakeExecutor()
        outcome = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag4b"),
            executor=ex,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=None,
        ).run("普通任务")
        assert outcome.verified is True
        assert len(ex.instructions) == 1

    def test_approval_event_journalled(self, tmp_path):
        journal = RunJournal(str(tmp_path), "ag4c")
        OrchestratorLoop(
            journal=journal,
            executor=FakeExecutor(),
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "approved",
        ).run("部署到生产")
        assert "approval" in journal.done_steps()

    def test_resume_after_approval_then_proceeds(self, tmp_path):
        # park first (pending) ...
        ex1 = FakeExecutor()
        o1 = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag5"),
            executor=ex1,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "pending",
        ).run("部署到生产")
        assert o1.stop_reason == "awaiting_approval"
        assert len(ex1.instructions) == 0

        # ... then a fresh loop on the SAME journal, gate now approving, proceeds
        ex2 = FakeExecutor()
        o2 = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "ag5"),
            executor=ex2,
            verifier=_scripted_verifier([VerifyResult(passed=True)]),
            approval_gate=lambda goal: "approved",
        ).run("部署到生产")
        assert o2.verified is True
        assert len(ex2.instructions) == 1  # park-and-continue: ran after approval
