"""Unit tests for prax/commands/orchestrate.py + cron orchestrate wiring.

The executor/verifier are injected so the command's assembly + run is verified
without a real `claude -p`.
"""
from __future__ import annotations

from prax.commands.cron import _argv_for_job
from prax.commands.orchestrate import handle_orchestrate
from prax.core.cron_store import CronJob
from prax.core.orchestrator_loop import StepResult, VerifyResult


class _FakeExec:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, instruction, *, session_id=None):
        self.calls.append(instruction)
        return StepResult(text="ran", session_id="s")


class TestHandleOrchestrate:
    def test_assembles_and_runs_loop(self, tmp_path):
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["做一个登录页", "--run-id", "rt1", "--max-iterations", "3"],
            executor=ex,
            verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.verified is True
        assert outcome.iterations == 1
        assert len(ex.calls) == 1
        assert (tmp_path / ".prax" / "runs" / "rt1" / "journal.jsonl").exists()

    def test_no_verify_reports_unverified_not_verified(self, tmp_path):
        # G3: no --verify must NOT claim "verified"
        ex = _FakeExec()
        outcome = handle_orchestrate(str(tmp_path), ["g", "--run-id", "rt2"], executor=ex)
        assert outcome.verified is False
        assert outcome.stop_reason == "completed_no_verify"
        assert len(ex.calls) == 1

    def test_bad_verify_command_does_not_crash(self, tmp_path):
        # G2: an invalid --verify degrades gracefully, not a traceback
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path), ["g", "--verify", "rm -rf /", "--run-id", "rt9"], executor=ex
        )
        assert outcome.stop_reason == "bad_verifier"
        assert outcome.verified is False
        assert len(ex.calls) == 0  # bailed before running the executor

    def test_max_iterations_when_verifier_never_passes(self, tmp_path):
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["g", "--run-id", "rt3", "--max-iterations", "2"],
            executor=ex,
            verifier=lambda: VerifyResult(passed=False, output="nope"),
        )
        assert outcome.verified is False
        assert outcome.stop_reason == "max_iterations"
        assert outcome.iterations == 2
        assert len(ex.calls) == 2


class TestCronOrchestrateWiring:
    def test_run_mode_defaults_to_prompt(self):
        job = CronJob(name="j", schedule="* * * * *", prompt="hi")
        assert job.run_mode == "prompt"
        assert _argv_for_job(job, prefix=["prax"])[:2] == ["prax", "prompt"]

    def test_orchestrate_run_mode_emits_orchestrate_verb(self):
        job = CronJob(name="j", schedule="* * * * *", prompt="盯梢一遍", run_mode="orchestrate")
        argv = _argv_for_job(job, prefix=["prax"])
        assert argv[:2] == ["prax", "orchestrate"]
        assert "盯梢一遍" in argv

    def test_orchestrate_run_mode_omits_session_id(self):
        job = CronJob(
            name="j", schedule="* * * * *", prompt="x",
            session_id="abc", run_mode="orchestrate",
        )
        assert "--session-id" not in _argv_for_job(job, prefix=["prax"])

    def test_run_mode_roundtrips_through_dict(self):
        job = CronJob(name="j", schedule="* * * * *", prompt="x", run_mode="orchestrate")
        assert CronJob.from_dict(job.to_dict()).run_mode == "orchestrate"
