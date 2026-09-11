"""Offline repository harness contracts: real journals/loop, never a real worker."""

from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from prax import repository_harness as harness
from prax.core.orchestrator_loop import StepResult
from prax.core.run_journal import RunJournal
from prax.harness_contract import load_contract


class FakeExecutor:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.instructions = []

    def run(self, instruction, *, session_id=None):
        self.instructions.append(instruction)
        if self.actions:
            action = self.actions.pop(0)
            if isinstance(action, Exception):
                raise action
            action()
        return StepResult(text="Everything is implemented and all checks passed.")


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / ".prax").mkdir()
    (root / ".gitignore").write_text("__pycache__/\n.prax/harness-runs/\n", encoding="utf-8")
    state = root / "src" / "answer.txt"
    state.write_text("wrong", encoding="utf-8")
    acceptance = root / "tests" / "test_acceptance.py"
    acceptance.write_text(
        "import pathlib\nimport unittest\n"
        "ROOT = pathlib.Path(__file__).resolve().parents[1]\n"
        "class Acceptance(unittest.TestCase):\n"
        "    def test_answer(self):\n"
        "        if (ROOT / '.prax/harness-runs/offline/infra-down').exists():\n"
        "            print('HARNESS-ERROR: acceptance environment unavailable')\n"
        "            return\n"
        "        self.assertEqual((ROOT / 'src/answer.txt').read_text(), 'correct')\n",
        encoding="utf-8",
    )
    profile = {"schemaVersion": 1, "worker": "codex", "policy_files": []}
    harness.write_json(root / ".prax/harness.json", profile)
    task = {
        "schemaVersion": 1, "id": "offline", "goal": "Make the answer correct",
        "kind": "code-check", "repositories": [{"path": ".", "branch": "dev", "scope": ["src"]}],
        "verifier": {"cwd": ".", "argv": [sys.executable, "-B", "-m", "unittest", "discover",
                                             "-s", "tests", "-p", "test_*.py"], "timeout_seconds": 30},
        "budget": {"max_iterations": 3, "wall_seconds": 60, "stuck_after": 2},
    }
    for argv in (
        ["git", "init", "-b", "dev"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Harness Test", "-c", "user.email=harness@example.invalid",
         "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    contract = load_contract(root, task, profile)
    run_dir = root / ".prax/harness-runs/offline"
    run_dir.mkdir(parents=True)
    return SimpleNamespace(root=root, run_dir=run_dir, task=task, profile=profile,
                           contract=contract, state=state, acceptance=acceptance)


def execute(repo, executor):
    result = harness.execute(repo.root, repo.run_dir, repo.contract, executor=executor)
    assert harness.read_json(repo.run_dir / "result.json") == result
    return result


def test_contract_run_and_scheduled_resume_share_one_identity(repository, monkeypatch):
    worker = FakeExecutor(lambda: repository.state.write_text("correct"))
    supervisor = Mock(side_effect=lambda root, directory, contract, **kw:
                      harness.execute(root, directory, contract, executor=worker))
    monkeypatch.setattr(harness, "supervise", supervisor)
    first = harness.run_contract(repository.root, repository.contract)
    second = harness.run_contract(repository.root, repository.contract, resume_existing=True)
    assert first["verified"] and second == first
    assert len(worker.instructions) == 1
    supervisor.assert_called_once()
    changed = copy.deepcopy(repository.contract)
    changed["task"]["goal"] = "An unrelated new goal"
    with pytest.raises(ValueError, match="contract changed"):
        harness.run_contract(repository.root, changed, resume_existing=True)


def test_contract_cached_pass_rejects_workspace_drift(repository, monkeypatch):
    repository.state.write_text("correct")
    monkeypatch.setattr(harness, "supervise", lambda root, directory, contract, **kw:
                        harness.execute(root, directory, contract, executor=FakeExecutor()))
    assert harness.run_contract(repository.root, repository.contract)["verified"]
    repository.state.write_text("regressed")
    with pytest.raises(ValueError, match="workspace changed"):
        harness.run_contract(repository.root, repository.contract, resume_existing=True)


def test_contract_cached_pass_rejects_verifier_drift(repository, monkeypatch):
    repository.state.write_text("correct")
    monkeypatch.setattr(harness, "supervise", lambda root, directory, contract, **kw:
                        harness.execute(root, directory, contract, executor=FakeExecutor()))
    harness.run_contract(repository.root, repository.contract)
    repository.acceptance.write_text("# weakened acceptance\n")
    with pytest.raises(ValueError, match="verifier"):
        harness.run_contract(repository.root, repository.contract, resume_existing=True)


def test_worker_error_after_legal_edits_retains_resumable_checkpoint(repository, monkeypatch):
    def partially_edit_then_fail():
        repository.state.write_text("partial")
        raise RuntimeError("worker connection lost")

    worker = FakeExecutor(partially_edit_then_fail, lambda: repository.state.write_text("correct"))
    monkeypatch.setattr(harness, "supervise", lambda root, directory, contract, **kw:
                        harness.execute(root, directory, contract, executor=worker))
    first = harness.run_contract(repository.root, repository.contract)
    assert first["status"] == "HARNESS-ERROR" and first["stop_reason"] == "executor_error"
    assert first["verified"] is False and first["iterations"] == 1
    resumed = harness.run_contract(repository.root, repository.contract,
                                    resume_existing=True, explicit_resume=True)
    assert resumed["status"] == "PASS" and resumed["iterations"] == 2
    assert resumed["elapsed_seconds"] >= first["elapsed_seconds"]
    assert len(worker.instructions) == 2


@pytest.mark.parametrize("target", ["source", "acceptance"])
def test_release_cannot_invalidate_accepted_source_or_controls(repository, monkeypatch, target):
    repository.state.write_text("correct")

    def release_changes_inputs(root, directory, contract, phase, remaining):
        if phase == "release":
            file = repository.state if target == "source" else repository.acceptance
            file.write_text("wrong")

    monkeypatch.setattr(harness, "adapter", release_changes_inputs)
    result = execute(repository, FakeExecutor())
    assert result["status"] == "HARNESS-ERROR" and result["verified"] is False
    assert result["stop_reason"] == "ownership_retained"


def test_post_verifier_adapter_check_cannot_change_source(repository, monkeypatch):
    repository.state.write_text("correct")
    check_count = 0

    def check_changes_source(root, directory, contract, phase, remaining):
        nonlocal check_count
        if phase == "check":
            check_count += 1
            if check_count == 2:
                repository.state.write_text("wrong")

    monkeypatch.setattr(harness, "adapter", check_changes_source)
    result = execute(repository, FakeExecutor())
    assert result["status"] == "HARNESS-ERROR" and result["verified"] is False


def test_pause_during_step_checkpoints_without_claiming_acceptance(repository, monkeypatch):
    def pause_after_partial_work():
        repository.state.write_text("partial")
        harness.write_json(repository.run_dir / "pause.json", {"operator": True})
    worker = FakeExecutor(pause_after_partial_work, lambda: repository.state.write_text("correct"))
    supervisor = Mock(side_effect=lambda root, directory, contract, **kw:
                      harness.execute(root, directory, contract, executor=worker))
    monkeypatch.setattr(harness, "supervise", supervisor)
    first = harness.run_contract(repository.root, repository.contract)
    assert first["status"] == "PAUSED" and not first["verified"]
    assert first["iterations"] == 1
    again = harness.run_contract(repository.root, repository.contract, resume_existing=True)
    assert again["status"] == "PAUSED"
    assert supervisor.call_count == 1  # cron cannot unpause
    resumed = harness.run_contract(repository.root, repository.contract,
                                    resume_existing=True, explicit_resume=True)
    assert resumed["status"] == "PASS"
    assert resumed["iterations"] == 2
    assert resumed["elapsed_seconds"] >= first["elapsed_seconds"]


def test_resume_cannot_replenish_task_budget(repository, monkeypatch):
    supervisor = Mock(side_effect=lambda root, directory, contract, **kw:
                      harness.execute(root, directory, contract, executor=FakeExecutor()))
    monkeypatch.setattr(harness, "supervise", supervisor)
    harness.run_contract(repository.root, repository.contract)
    changed = copy.deepcopy(repository.contract)
    changed["task"]["budget"]["max_iterations"] += 1
    with pytest.raises(ValueError, match="contract changed"):
        harness.run_contract(repository.root, changed, resume_existing=True)
    assert supervisor.call_count == 1


def test_supervisor_does_not_accept_stale_result_from_zero_exit(repository, monkeypatch):
    proc = Mock(pid=12345)
    proc.wait.return_value = 0
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(harness, "terminate_group", Mock())
    result = harness.supervise(repository.root, repository.run_dir, repository.contract, watch_pause=True)
    assert result["status"] == "HARNESS-ERROR"
    assert result["stop_reason"] == "missing_fresh_result"


def test_wait_for_runtime_observes_operator_pause(repository):
    harness.write_json(repository.run_dir / "pause.json", {})
    proc = Mock()
    with pytest.raises(harness.PauseRequested):
        harness.wait_for_runtime(proc, 60, repository.run_dir)
    proc.wait.assert_not_called()


def test_supervisor_spawn_failure_is_durable_not_running(repository, monkeypatch):
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(side_effect=OSError("cannot spawn")))
    result = harness.supervise(repository.root, repository.run_dir, repository.contract, watch_pause=True)
    assert result["status"] == "HARNESS-ERROR"
    assert result["stop_reason"] == "runtime_start_failed"
    assert harness.read_json(repository.run_dir / "result.json") == result


def test_supervisor_accounts_for_startup_and_cleanup(repository, monkeypatch):
    proc = Mock(pid=12345)
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(harness, "terminate_group", Mock())
    monkeypatch.setattr(harness.time, "monotonic", Mock(side_effect=[10, 15]))

    def completed_child(proc, timeout, directory):
        result = harness.read_json(directory / "result.json")
        harness.write_json(directory / "result.json", {**result, "status": "PASS",
                                                       "verified": True, "elapsed_seconds": 1})
        return 0
    monkeypatch.setattr(harness, "wait_for_runtime", completed_child)
    result = harness.supervise(repository.root, repository.run_dir, repository.contract, watch_pause=True)
    assert result["elapsed_seconds"] == 5


def test_resume_refuses_evidence_with_missing_identity(repository):
    harness.write_json(repository.run_dir / "contract.json", repository.contract)
    harness.write_json(repository.run_dir / "result.json", {"status": "PASS", "verified": True})
    with pytest.raises(ValueError, match="identity is missing"):
        harness.run_contract(repository.root, repository.contract, resume_existing=True)


def test_shift_cli_supervisor_preflight_and_cached_resume_offline(repository):
    """Real public CLI, supervisor, child and verifier; no model is dispatched."""
    repository.state.write_text("correct")
    harness.write_json(repository.root / ".prax/task.json", repository.task)
    source = str(Path(harness.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": source, "PYTHONDONTWRITEBYTECODE": "1"}

    def run(*arguments):
        completed = subprocess.run(
            [sys.executable, "-m", "prax", "shift", *arguments], cwd=repository.root,
            env=env, capture_output=True, text=True, timeout=45, check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    planned = run("plan", "--task", ".prax/task.json")
    assert planned["execution"] == "not-started"
    assert not (repository.run_dir / "contract.json").exists()
    first = run("run", "--task", ".prax/task.json")
    assert first["status"] == "PASS" and first["verified"] is True
    assert first["iterations"] == 0 and first["execution_id"]
    assert first["checks"][0]["returncode"] == 0
    second = run("run", "--task", ".prax/task.json", "--resume-existing")
    assert second == first
    assert run("status", "--run-id", "offline") == first


def test_shift_cli_environment_failure_pause_and_scheduled_resume(repository):
    repository.task["verifier"]["argv"] = [sys.executable, "-c", "print('HARNESS-ERROR: offline environment')"]
    harness.write_json(repository.root / ".prax/task.json", repository.task)
    env = {**os.environ, "PYTHONPATH": str(Path(harness.__file__).resolve().parents[1]),
           "PYTHONDONTWRITEBYTECODE": "1"}

    def run(expected_exit, *arguments):
        process = subprocess.run([sys.executable, "-m", "prax", "shift", *arguments],
                                 cwd=repository.root, env=env, capture_output=True,
                                 text=True, timeout=45, check=False)
        assert process.returncode == expected_exit, process.stdout + process.stderr
        return json.loads(process.stdout)

    failed = run(2, "run", "--task", ".prax/task.json")
    assert failed["status"] == "HARNESS-ERROR" and failed["iterations"] == 0
    assert run(0, "pause", "--run-id", "offline")["status"] == "PAUSE-REQUESTED"
    paused = run(3, "run", "--task", ".prax/task.json", "--resume-existing")
    assert paused["status"] == "PAUSED" and paused["elapsed_seconds"] == failed["elapsed_seconds"]
    resumed = run(2, "resume", "--run-id", "offline")
    assert resumed["status"] == "HARNESS-ERROR"
    assert resumed["elapsed_seconds"] > failed["elapsed_seconds"]


def test_contract_holds_the_same_repository_lock_as_harness(repository):
    with harness.repository_locks(repository.root, repository.task):
        with pytest.raises(ValueError, match="active harness"):
            harness.run_contract(repository.root, repository.contract)


def test_frozen_claude_profile_uses_safe_worker_adapter(repository, monkeypatch):
    repository.profile.update(worker="claude", model="claude-test-model")
    harness.write_json(repository.root / ".prax/harness.json", repository.profile)
    contract = load_contract(repository.root, repository.task, repository.profile)
    worker = FakeExecutor(lambda: repository.state.write_text("correct"))
    factory = Mock(return_value=("claude", worker))
    monkeypatch.setattr("prax.core.agent_host.build_step_executor", factory)
    result = harness.execute(repository.root, repository.run_dir, contract)
    assert result["worker"] == "claude" and result["verified"]
    factory.assert_called_once_with(str(repository.root), agent_host="claude", model="claude-test-model")


@pytest.mark.parametrize("status,verified,code", [
    ("PASS", True, 0), ("PASS", False, 2), ("FEATURE-FAIL", False, 1),
    ("HARNESS-ERROR", False, 2), ("PAUSED", False, 3), ("RUNNING", False, 2),
])
def test_exit_code_requires_a_verified_pass(status, verified, code):
    assert harness.outcome_exit_code({"status": status, "verified": verified}) == code


def test_preflight_pass_does_not_dispatch_worker(repository):
    repository.state.write_text("correct", encoding="utf-8")
    worker = FakeExecutor()
    result = execute(repository, worker)
    assert result["status"] == "PASS"
    assert result["verified"] is True
    assert result["iterations"] == 0
    assert worker.instructions == []
    assert result["platform_validation"] == "not-executed"
    assert result["publication"] == "not-executed"


def test_worker_claim_cannot_override_independent_failure(repository):
    worker = FakeExecutor()
    result = execute(repository, worker)
    assert result["status"] == "FEATURE-FAIL"
    assert result["stop_reason"] == "stuck_no_progress"
    assert result["verified"] is False
    assert len(worker.instructions) == result["iterations"] == 2
    assert all(check["status"] == "FEATURE-FAIL" for check in result["checks"])


def test_preflight_feedback_reaches_worker_and_real_verifier_decides_pass(repository):
    worker = FakeExecutor(lambda: repository.state.write_text("correct", encoding="utf-8"))
    result = execute(repository, worker)
    assert result["status"] == "PASS"
    assert result["verified"] is True
    assert result["iterations"] == 1
    assert "AssertionError" in worker.instructions[0]
    assert "Do not modify verifier" in worker.instructions[0]
    assert [check["status"] for check in result["checks"]] == ["FEATURE-FAIL", "PASS"]
    journal = RunJournal(str(repository.run_dir), "worker")
    assert journal.result_for("outcome")["verified"] is True


def test_failure_feedback_drives_a_second_bounded_worker_step(repository):
    worker = FakeExecutor(lambda: None, lambda: repository.state.write_text("correct", encoding="utf-8"))
    result = execute(repository, worker)
    assert result["status"] == "PASS"
    assert result["iterations"] == 2
    assert "AssertionError" in worker.instructions[1]
    assert [check["status"] for check in result["checks"]] == ["FEATURE-FAIL", "FEATURE-FAIL", "PASS"]


def test_harness_marker_overrides_zero_exit_and_prevents_worker(repository):
    (repository.run_dir / "infra-down").touch()
    worker = FakeExecutor()
    result = execute(repository, worker)
    assert result["status"] == "HARNESS-ERROR"
    assert result["stop_reason"] == "harness_error"
    assert result["checks"][0]["returncode"] == 0
    assert result["iterations"] == 0
    assert worker.instructions == []


def test_repaired_environment_resume_checks_before_dispatch(repository):
    marker = repository.run_dir / "infra-down"
    marker.touch()
    first = execute(repository, FakeExecutor())
    assert first["status"] == "HARNESS-ERROR"
    marker.unlink()
    worker = FakeExecutor(lambda: repository.state.write_text("correct", encoding="utf-8"))
    resumed = execute(repository, worker)
    assert resumed["status"] == "PASS"
    assert resumed["iterations"] == 1
    assert "AssertionError" in worker.instructions[0]
    assert "acceptance environment unavailable" not in worker.instructions[0]


def test_worker_exception_is_not_automatically_retried(repository):
    worker = FakeExecutor(RuntimeError("worker exited 17"))
    result = execute(repository, worker)
    assert result["status"] == "HARNESS-ERROR"
    assert result["stop_reason"] == "executor_error"
    assert result["iterations"] == len(worker.instructions) == 1
    assert len(result["checks"]) == 1


def test_resume_cannot_refresh_total_worker_budget(repository):
    repository.contract["task"]["budget"]["max_iterations"] = 1
    first = execute(repository, FakeExecutor())
    assert first["status"] == "FEATURE-FAIL"
    worker = FakeExecutor()
    second = execute(repository, worker)
    assert second["status"] == "FEATURE-FAIL"
    assert second["verified"] is False
    assert second["iterations"] == 1
    assert worker.instructions == []


def test_verified_resume_preserves_result_and_does_not_redo_acceptance(repository, monkeypatch):
    repository.state.write_text("correct", encoding="utf-8")
    first = execute(repository, FakeExecutor())
    monkeypatch.setattr(harness, "command", Mock(side_effect=AssertionError("unexpected verifier")))
    worker = FakeExecutor()
    second = execute(repository, worker)
    assert second == first
    assert worker.instructions == []


@pytest.mark.parametrize("changed", ["source", "verifier", "profile"])
def test_verified_resume_rejects_drift_instead_of_replaying_pass(repository, changed):
    repository.state.write_text("correct", encoding="utf-8")
    assert execute(repository, FakeExecutor())["verified"] is True
    target = {"source": repository.state, "verifier": repository.acceptance,
              "profile": repository.root / ".prax/harness.json"}[changed]
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    worker = FakeExecutor()
    result = execute(repository, worker)
    assert result["status"] == "HARNESS-ERROR"
    assert result["verified"] is False
    assert worker.instructions == []


def test_changed_frozen_task_is_rejected_without_overwriting_evidence(repository):
    execute(repository, FakeExecutor())
    saved = (repository.run_dir / "result.json").read_bytes()
    changed = copy.deepcopy(repository.contract)
    changed["task"]["goal"] = "Do unrelated work"
    with pytest.raises(ValueError, match="contract changed"):
        harness.execute(repository.root, repository.run_dir, changed, executor=FakeExecutor())
    assert (repository.run_dir / "result.json").read_bytes() == saved


def test_worker_verifier_edit_blocks_before_new_acceptance(repository):
    worker = FakeExecutor(lambda: repository.acceptance.write_text("# bypassed\n", encoding="utf-8"))
    result = execute(repository, worker)
    assert result["status"] == "HARNESS-ERROR"
    assert result["verified"] is False
    assert len(result["checks"]) == 1
    assert result["iterations"] == 1


def test_unexpected_verifier_failure_is_recorded_as_harness_error(repository, monkeypatch):
    monkeypatch.setattr(harness, "command", Mock(side_effect=RuntimeError("broken verification adapter")))
    result = execute(repository, FakeExecutor())
    assert result["status"] == "HARNESS-ERROR"
    assert "broken verification adapter" in result["error"]


def test_prior_loop_pass_cannot_override_failed_resume_preflight(repository, monkeypatch):
    acceptance = repository.acceptance.read_text(encoding="utf-8")
    repository.acceptance.write_text(
        acceptance.replace("    def test_answer(self):\n", "    def test_answer(self):\n"
                           "        if (ROOT / '.prax/harness-runs/offline/feature-broken').exists():\n"
                           "            self.fail('current acceptance is failing')\n"),
        encoding="utf-8",
    )
    release_failed = False

    def fail_first_release(root, run_dir, contract, phase, remaining):
        nonlocal release_failed
        if phase == "release" and not release_failed:
            release_failed = True
            raise harness.BoundaryStop("ownership release unavailable")

    monkeypatch.setattr(harness, "adapter", fail_first_release)
    first = execute(repository, FakeExecutor(lambda: repository.state.write_text("correct", encoding="utf-8")))
    assert first["status"] == "HARNESS-ERROR"
    assert RunJournal(str(repository.run_dir), "worker").result_for("outcome")["verified"] is True

    # An external acceptance condition changes while product/control files stay
    # at the checkpoint; the freshly run verifier must outrank the old loop pass.
    (repository.run_dir / "feature-broken").touch()
    resumed = execute(repository, FakeExecutor())
    assert resumed["checks"][0]["status"] == "FEATURE-FAIL"
    assert resumed["status"] == "FEATURE-FAIL"
    assert resumed["verified"] is False


def test_command_timeout_kills_direct_child_and_breaks_out_of_loop(tmp_path, monkeypatch):
    proc = Mock()
    proc.wait.side_effect = [subprocess.TimeoutExpired("fake-verifier", 1), 0]
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    with pytest.raises(harness.BoundaryStop, match="timed out"):
        harness.command(["fake-verifier"], tmp_path, 1, tmp_path / "verify.log")
    proc.kill.assert_called_once_with()
    assert proc.wait.call_count == 2


@pytest.mark.parametrize("python_options", [[], ["-B"]])
def test_empty_unittest_collection_is_not_acceptance(tmp_path, python_options):
    (tmp_path / "tests").mkdir()
    result = harness.command([sys.executable, *python_options, "-m", "unittest", "discover",
                              "-s", "tests"], tmp_path, 10, tmp_path / "empty.log")
    # Python versions differ on whether an empty unittest run exits 0 or 5.
    assert result["returncode"] in (0, 5)
    assert "Ran 0 tests" in result["output"]
    assert result["status"] == "HARNESS-ERROR"


def test_unittest_collection_error_is_environment_failure(tmp_path):
    result = harness.command([sys.executable, "-m", "unittest", "missing_test_module"],
                             tmp_path, 10, tmp_path / "collection.log")
    assert result["returncode"] == 1
    assert "_FailedTest" in result["output"]
    assert result["status"] == "HARNESS-ERROR"


@pytest.mark.parametrize("output,code,expected", [
    ("# tests 0\n", 0, "HARNESS-ERROR"),
    ("Error: Cannot find package 'tsx' imported from /fixture\n", 1, "HARNESS-ERROR"),
    ("# tests 1\n# fail 1\n", 1, "FEATURE-FAIL"),
])
def test_node_test_collection_and_loader_status(tmp_path, monkeypatch, output, code, expected):
    proc = Mock()
    proc.wait.return_value = code

    def runner(argv, **kwargs):
        kwargs["stdout"].write(output)
        kwargs["stdout"].flush()
        return proc

    monkeypatch.setattr(harness.subprocess, "Popen", runner)
    result = harness.command(["node", "--import", "tsx", "--test", "tests/example.test.ts"],
                             tmp_path, 10, tmp_path / "node-test.log")
    assert result["status"] == expected


@pytest.mark.parametrize("argv", [[sys.executable, "-m", "pytest"],
                                 [sys.executable, "-B", "-m", "pytest"], ["pytest"]])
@pytest.mark.parametrize("code,expected", [(0, "PASS"), (1, "FEATURE-FAIL"),
                                           (2, "HARNESS-ERROR"), (3, "HARNESS-ERROR"),
                                           (4, "HARNESS-ERROR"), (5, "HARNESS-ERROR")])
def test_pytest_process_status_distinguishes_assertions_from_runner_failure(tmp_path, monkeypatch, argv, code, expected):
    proc = Mock()
    proc.wait.return_value = code
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    result = harness.command(argv, tmp_path, 10, tmp_path / "pytest.log")
    assert result["status"] == expected


@pytest.mark.parametrize("python_options", [[], ["-B"]])
def test_missing_pytest_module_exit_one_is_environment_failure(tmp_path, monkeypatch, python_options):
    proc = Mock()
    proc.wait.return_value = 1

    def missing_module(argv, **kwargs):
        kwargs["stdout"].write(f"{argv[0]}: No module named pytest\n")
        kwargs["stdout"].flush()
        return proc

    monkeypatch.setattr(harness.subprocess, "Popen", missing_module)
    result = harness.command([sys.executable, *python_options, "-m", "pytest"],
                             tmp_path, 10, tmp_path / "missing-pytest.log")
    assert result["returncode"] == 1
    assert result["status"] == "HARNESS-ERROR"


@pytest.mark.parametrize("failure,reason", [
    (subprocess.TimeoutExpired("runtime", 60), "wall budget exhausted"),
    (KeyboardInterrupt(), "interrupted"),
])
def test_supervisor_timeout_or_interrupt_cleans_group_and_records_error(repository, monkeypatch, failure, reason):
    proc = Mock(pid=12345)
    proc.wait.side_effect = failure
    popen = Mock(return_value=proc)
    cleanup = Mock()
    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    monkeypatch.setattr(harness, "terminate_group", cleanup)
    result = harness.supervise(repository.root, repository.run_dir, repository.contract)
    assert result["status"] == "HARNESS-ERROR"
    assert result["stop_reason"] == reason
    assert result["verified"] is False
    cleanup.assert_called_once_with(proc)
    assert popen.call_args.kwargs["start_new_session"] is True
    assert harness.read_json(repository.run_dir / "result.json") == result


def test_successful_runtime_still_cleans_descendants_and_uses_structured_verdict(repository, monkeypatch):
    expected = {"status": "FEATURE-FAIL", "verified": False,
                "stop_reason": "max_iterations", "elapsed_seconds": 11}
    harness.write_json(repository.run_dir / "result.json", expected)
    proc = Mock(pid=12345)
    proc.wait.return_value = 0
    cleanup = Mock()
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(harness, "terminate_group", cleanup)
    result = harness.supervise(repository.root, repository.run_dir, repository.contract)
    assert result == expected
    proc.wait.assert_called_once_with(timeout=49)
    cleanup.assert_called_once_with(proc)


def test_nonzero_runtime_exit_overrides_stale_pass(repository, monkeypatch):
    harness.write_json(repository.run_dir / "result.json", {"status": "PASS", "verified": True})
    proc = Mock(pid=12345)
    proc.wait.return_value = 9
    monkeypatch.setattr(harness.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(harness, "terminate_group", Mock())
    result = harness.supervise(repository.root, repository.run_dir, repository.contract)
    assert result["status"] == "HARNESS-ERROR"
    assert result["stop_reason"] == "runtime exited 9"
    assert result["verified"] is False


def test_cleanup_targets_group_even_when_parent_already_exited(monkeypatch):
    proc = Mock(pid=12345)
    proc.poll.return_value = 0
    killpg = Mock()
    monkeypatch.setattr(harness.os, "killpg", killpg)
    monkeypatch.setattr(harness.time, "sleep", Mock())
    harness.terminate_group(proc)
    assert killpg.call_args_list == [((12345, signal.SIGTERM),), ((12345, signal.SIGKILL),)]
    proc.wait.assert_called_once_with()


def test_run_lock_rejects_concurrent_owner_and_releases_after_exit(tmp_path):
    with (harness.run_lock(tmp_path), pytest.raises(ValueError, match="already active"),
          harness.run_lock(tmp_path)):
        pytest.fail("concurrent run lock acquired")
    with harness.run_lock(tmp_path):
        pass


def test_repository_lock_rejects_other_run_ids_and_releases_after_exit(repository):
    other_task = copy.deepcopy(repository.task)
    other_task["id"] = "another-run"
    with (harness.repository_locks(repository.root, repository.task),
          pytest.raises(ValueError, match="active harness task"),
          harness.repository_locks(repository.root, other_task)):
        pytest.fail("different run ID acquired the same repository")
    with harness.repository_locks(repository.root, other_task):
        pass


@pytest.mark.parametrize("status,exit_code", [("PASS", 0), ("FEATURE-FAIL", 1), ("HARNESS-ERROR", 2)])
def test_public_harness_run_exit_codes_follow_verifier(repository, monkeypatch, capsys, status, exit_code):
    harness.write_json(repository.root / "task.json", repository.task)
    monkeypatch.setattr(harness, "supervise", Mock(return_value={"status": status, "verified": status == "PASS"}))
    assert harness.main(["run", "--root", str(repository.root), "--task", "task.json"]) == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_public_prax_dispatches_harness_before_legacy_parsing(monkeypatch):
    from prax import main as legacy_main

    handler = Mock(return_value=2)
    monkeypatch.setattr(harness, "main", handler)
    monkeypatch.setattr(sys, "argv", ["prax", "harness", "plan", "--task", "task.json", "--root", "/repo"])
    with pytest.raises(SystemExit) as stopped:
        legacy_main.main()
    assert stopped.value.code == 2
    handler.assert_called_once_with(["plan", "--task", "task.json", "--root", "/repo"])
