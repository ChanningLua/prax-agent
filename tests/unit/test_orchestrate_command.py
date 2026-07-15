"""Unit tests for prax/commands/orchestrate.py + cron orchestrate wiring.

The executor/verifier are injected so the command's assembly + run is verified
without a real `claude -p`.
"""
from __future__ import annotations

from prax.commands.cron import _argv_for_job
from prax.commands.orchestrate import handle_orchestrate
from prax.core.cron_store import CronJob
from prax.core.orchestrator_loop import VERIFY_HARNESS_ERROR, StepResult, VerifyResult


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


class TestApprovalWiring:
    def test_injected_pending_gate_parks_before_executing(self, tmp_path):
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["部署到生产", "--run-id", "ra1"],
            executor=ex,
            verifier=lambda: VerifyResult(passed=True),
            approval_gate=lambda goal: "pending",
        )
        assert outcome.stop_reason == "awaiting_approval"
        assert outcome.verified is False
        assert len(ex.calls) == 0  # parked: never dispatched to the executor

    def test_gate_built_from_config_autoapproves_nonprod_goal(self, tmp_path):
        # config present (relay opt-in) but the goal is NOT prod-affecting →
        # the built gate auto-approves and the run proceeds, no relay/network.
        cfgdir = tmp_path / ".prax"
        cfgdir.mkdir(parents=True, exist_ok=True)
        (cfgdir / "config.yaml").write_text(
            "approval:\n"
            "  relay_url: http://relay\n"
            "  admin_token: T\n",
            encoding="utf-8",
        )
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["写一个登录页并跑测试", "--run-id", "ra2"],
            executor=ex,
            verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.verified is True
        assert len(ex.calls) == 1


class _FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> None:
        self.calls.append((title, body))


class TestTerminalNotify:
    def test_pings_on_stuck_no_progress(self, tmp_path):
        notifier = _FakeNotifier()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["修一个 bug", "--run-id", "n1", "--max-iterations", "10"],
            executor=_FakeExec(),
            verifier=lambda: VerifyResult(passed=False, output="boom"),  # identical -> stuck
            notifier=notifier,
        )
        assert outcome.stop_reason == "stuck_no_progress"
        assert len(notifier.calls) == 1
        title, body = notifier.calls[0]
        assert "stuck_no_progress" in title
        assert "n1" in body  # run-id for resume is in the message

    def test_silent_on_verified(self, tmp_path):
        notifier = _FakeNotifier()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["做一个登录页", "--run-id", "n2"],
            executor=_FakeExec(),
            verifier=lambda: VerifyResult(passed=True),
            notifier=notifier,
        )
        assert outcome.verified is True
        assert notifier.calls == []  # success must not ping

    def test_pings_on_bad_verifier(self, tmp_path):
        notifier = _FakeNotifier()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["g", "--verify", "rm -rf /", "--run-id", "n3"],
            executor=_FakeExec(),
            notifier=notifier,
        )
        assert outcome.stop_reason == "bad_verifier"
        assert len(notifier.calls) == 1
        assert "bad_verifier" in notifier.calls[0][0]

    def test_pings_on_harness_error(self, tmp_path):
        notifier = _FakeNotifier()
        outcome = handle_orchestrate(
            str(tmp_path),
            ["g", "--run-id", "n4"],
            executor=_FakeExec(),
            verifier=lambda: VerifyResult(
                passed=False,
                output="service unavailable",
                status=VERIFY_HARNESS_ERROR,
            ),
            notifier=notifier,
        )
        assert outcome.stop_reason == "harness_error"
        assert "harness_error" in notifier.calls[0][0]


class TestFeatureMode:
    def _write_features(self, tmp_path, features):
        import json
        d = tmp_path / ".prax"
        d.mkdir(parents=True, exist_ok=True)
        (d / "feature_list.json").write_text(
            json.dumps({"features": features}, ensure_ascii=False), encoding="utf-8"
        )

    def test_all_features_done(self, tmp_path):
        self._write_features(tmp_path, [
            {"id": "f1", "title": "登录", "acceptance": "pytest -q", "risk_category": "mechanical", "priority": 1, "status": "pending"},
            {"id": "f2", "title": "列表", "acceptance": "pytest -q", "risk_category": "mechanical", "priority": 2, "status": "pending"},
        ])
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "fm1"],
            executor=ex, verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.stop_reason == "all_features_done"
        assert outcome.verified is True
        assert outcome.iterations == 2          # both features completed
        from prax.core.feature_list import FeatureList
        assert FeatureList(str(tmp_path)).next_pending() is None  # both persisted done

    def test_stops_on_blocked_feature(self, tmp_path):
        self._write_features(tmp_path, [
            {"id": "f1", "title": "登录", "acceptance": "pytest -q", "risk_category": "mechanical", "priority": 1, "status": "pending"},
            {"id": "f2", "title": "列表", "acceptance": "pytest -q", "risk_category": "mechanical", "priority": 2, "status": "pending"},
        ])
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "fm2", "--max-iterations", "1"],
            executor=_FakeExec(), verifier=lambda: VerifyResult(passed=False, output="nope"),
        )
        assert outcome.stop_reason == "feature_blocked:f1"
        assert outcome.verified is False
        from prax.core.feature_list import FeatureList
        assert FeatureList(str(tmp_path)).next_pending().id == "f1"  # nothing marked done

    def test_no_feature_list(self, tmp_path):
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "fm3"], executor=_FakeExec(),
        )
        assert outcome.stop_reason == "no_features"
        assert outcome.verified is False

    def test_no_goal_without_features_errors(self, tmp_path):
        outcome = handle_orchestrate(str(tmp_path), ["--run-id", "fm4"], executor=_FakeExec())
        assert outcome.stop_reason == "no_goal"

    def test_redline_feature_without_relay_fails_closed_before_executor(self, tmp_path):
        self._write_features(tmp_path, [
            {
                "id": "f1",
                "title": "调整价格",
                "acceptance": "node verify-features.mjs",
                "complexity": "simple",
                "risk_category": "money",
                "priority": 1,
                "status": "pending",
            },
        ])
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "risk1"],
            executor=ex, verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.stop_reason == "feature_blocked:f1"
        assert outcome.verified is False
        assert ex.calls == []
        from prax.core.feature_list import FeatureList
        assert FeatureList(str(tmp_path)).next_pending().id == "f1"

    def test_redline_feature_forces_relay_then_runs_after_approval(self, tmp_path):
        self._write_features(tmp_path, [
            {
                "id": "f1",
                "title": "调整价格",
                "acceptance": "node verify-features.mjs",
                "complexity": "simple",
                "risk_category": "money",
                "priority": 1,
                "status": "pending",
            },
        ])
        seen = {}

        def gate(goal, *, required=False, approval_id_override=None):
            seen.update(
                goal=goal,
                required=required,
                approval_id_override=approval_id_override,
            )
            return "approved"

        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "risk2"],
            executor=ex,
            verifier=lambda: VerifyResult(passed=True),
            approval_gate=gate,
        )
        assert outcome.stop_reason == "all_features_done"
        assert len(ex.calls) == 1
        assert seen["required"] is True
        assert seen["approval_id_override"] == "risk2-f1"
        assert "风险=money" in seen["goal"]

    def test_redline_feature_parks_then_resumes_after_approval(self, tmp_path):
        self._write_features(tmp_path, [
            {
                "id": "f1",
                "title": "调整价格",
                "acceptance": "pytest -q",
                "risk_category": "money",
                "priority": 1,
                "status": "pending",
            },
        ])
        ex1 = _FakeExec()
        first = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "risk3"],
            executor=ex1,
            verifier=lambda: VerifyResult(passed=True),
            approval_gate=lambda goal, *, required=False, approval_id_override=None: "pending",
        )
        assert first.stop_reason == "feature_blocked:f1"
        assert ex1.calls == []

        ex2 = _FakeExec()
        second = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "risk3"],
            executor=ex2,
            verifier=lambda: VerifyResult(passed=True),
            approval_gate=lambda goal, *, required=False, approval_id_override=None: "approved",
        )
        assert second.stop_reason == "all_features_done"
        assert len(ex2.calls) == 1

    def test_missing_risk_category_fails_closed(self, tmp_path):
        self._write_features(tmp_path, [
            {"id": "f1", "title": "未分类任务", "acceptance": "pytest -q"},
        ])
        ex = _FakeExec()
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "risk4"],
            executor=ex, verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.stop_reason == "feature_blocked:f1"
        assert ex.calls == []

    def test_redline_features_use_distinct_stable_approval_ids(self, tmp_path):
        self._write_features(tmp_path, [
            {
                "id": "f1", "title": "调整价格", "acceptance": "pytest -q",
                "risk_category": "money", "priority": 1, "status": "pending",
            },
            {
                "id": "f2", "title": "调整范围", "acceptance": "pytest -q",
                "risk_category": "scope", "priority": 2, "status": "pending",
            },
        ])
        seen_ids = []

        def gate(goal, *, required=False, approval_id_override=None):
            assert required is True
            seen_ids.append(approval_id_override)
            return "approved"

        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--run-id", "risk5"],
            executor=_FakeExec(),
            verifier=lambda: VerifyResult(passed=True),
            approval_gate=gate,
        )
        assert outcome.stop_reason == "all_features_done"
        assert seen_ids == ["risk5-f1", "risk5-f2"]


class TestMemoryWriteBack:
    def test_verified_run_records_completion_fact(self, tmp_path):
        # closes the loop: a verified outcome persists a fact the read side injects
        from prax.core.memory_store import MemoryStore

        outcome = handle_orchestrate(
            str(tmp_path),
            ["实现登录页", "--run-id", "mw1"],
            executor=_FakeExec(),
            verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.verified is True
        mem = MemoryStore(str(tmp_path)).format_for_prompt()
        assert "已验证达成目标" in mem
        assert "实现登录页" in mem

    def test_unverified_run_records_nothing(self, tmp_path):
        from prax.core.memory_store import MemoryStore

        outcome = handle_orchestrate(
            str(tmp_path),
            ["过不了的目标", "--run-id", "mw2", "--max-iterations", "1"],
            executor=_FakeExec(),
            verifier=lambda: VerifyResult(passed=False, output="nope"),
        )
        assert outcome.verified is False
        mem = MemoryStore(str(tmp_path)).format_for_prompt()
        assert "已验证达成目标" not in mem


class TestCommitPerFeature:
    def _git_repo(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    def test_commits_verified_feature_on_branch(self, tmp_path):
        import subprocess
        from prax.commands.orchestrate import _commit_feature
        from prax.core.feature_list import Feature

        self._git_repo(tmp_path)
        (tmp_path / "a.txt").write_text("changed by the agent")
        assert _commit_feature(str(tmp_path), Feature(id="f1", title="do thing")) is True
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
        ).stdout
        assert "f1 do thing" in log  # committed with the feature-scoped message

    def test_skips_when_nothing_to_commit(self, tmp_path):
        import subprocess
        from prax.commands.orchestrate import _commit_feature
        from prax.core.feature_list import Feature

        self._git_repo(tmp_path)
        (tmp_path / "a.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
        # no new working-tree change → nothing to commit
        assert _commit_feature(str(tmp_path), Feature(id="f1", title="x")) is False


class _WritingExec:
    """Fake executor that makes a working-tree change each step (so there's
    something for the commit gate to commit)."""

    def __init__(self, cwd) -> None:
        self.cwd = cwd
        self.n = 0

    def run(self, instruction, *, session_id=None):
        self.n += 1
        (self.cwd / f"out{self.n}.txt").write_text("changed by the agent")
        return StepResult(text="ran", session_id="s")


class TestThreeStepCommitGating:
    """--commit is an 自主循环-only privilege: HUMAN_CHECK features are verified +
    marked done but left uncommitted for a human to review."""

    def _git_repo(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    def _write_features(self, tmp_path, features):
        import json
        d = tmp_path / ".prax"
        d.mkdir(parents=True, exist_ok=True)
        (d / "feature_list.json").write_text(
            json.dumps({"features": features}, ensure_ascii=False), encoding="utf-8"
        )

    def _log(self, tmp_path):
        import subprocess
        return subprocess.run(
            ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
        ).stdout

    def test_autonomous_feature_is_committed(self, tmp_path):
        # simple (declared) + behavioral acceptance → 自主循环 → auto-commit
        self._git_repo(tmp_path)
        self._write_features(tmp_path, [
            {"id": "f1", "title": "小改", "acceptance": "node verify-features.mjs",
             "complexity": "simple", "risk_category": "mechanical",
             "priority": 1, "status": "pending"},
        ])
        handle_orchestrate(
            str(tmp_path), ["--features", "--commit", "--run-id", "ts1"],
            executor=_WritingExec(tmp_path), verifier=lambda: VerifyResult(passed=True),
        )
        assert "f1 小改" in self._log(tmp_path)

    def test_human_check_feature_is_not_committed(self, tmp_path):
        # tests-grade acceptance + undeclared complexity → HUMAN_CHECK → NO auto-commit
        self._git_repo(tmp_path)
        self._write_features(tmp_path, [
            {"id": "f1", "title": "普通活", "acceptance": "pytest -q",
             "risk_category": "mechanical", "priority": 1, "status": "pending"},
        ])
        outcome = handle_orchestrate(
            str(tmp_path), ["--features", "--commit", "--run-id", "ts2"],
            executor=_WritingExec(tmp_path), verifier=lambda: VerifyResult(passed=True),
        )
        assert outcome.verified is True          # work got done + verified
        assert "f1" not in self._log(tmp_path)   # but it was NOT auto-committed
        from prax.core.feature_list import FeatureList
        assert FeatureList(str(tmp_path)).next_pending() is None  # still marked done


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
