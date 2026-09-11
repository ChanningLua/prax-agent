"""Independent negative regressions for source identity and failure checkpoints."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from prax import repository_harness as harness
from prax.harness_contract import load_contract, snapshot


@pytest.fixture
def checkpoint_repo(tmp_path):
    root = tmp_path / "checkpoint-repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / ".prax").mkdir()
    (root / ".gitignore").write_text(".prax/harness-runs/\n__pycache__/\n", encoding="utf-8")
    source = root / "src" / "answer.txt"
    source.write_text("wrong", encoding="utf-8")
    outside = root / "README.md"
    outside.write_text("Unrelated documentation.\n", encoding="utf-8")
    verifier = root / "verify.py"
    verifier.write_text(
        "from pathlib import Path\n"
        "answer = Path('src/answer.txt')\n"
        "assert answer.read_text() == 'correct'\n", encoding="utf-8")
    profile = {"schemaVersion": 1, "worker": "codex", "policy_files": []}
    harness.write_json(root / ".prax/harness.json", profile)
    task = {
        "schemaVersion": 1, "id": "checkpoint-check", "goal": "Make the answer correct",
        "kind": "code-check", "repositories": [
            {"path": ".", "branch": "dev", "scope": ["src/answer.txt"]},
        ],
        "verifier": {"argv": [sys.executable, "-B", "verify.py"], "cwd": ".", "timeout_seconds": 10},
        "budget": {"max_iterations": 3, "wall_seconds": 30, "stuck_after": 2},
    }
    for argv in (
        ["git", "init", "-b", "dev"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Checkpoint Test", "-c", "user.email=test@example.invalid",
         "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    contract = load_contract(root, task, profile)
    directory = root / ".prax/harness-runs/checkpoint-check"
    return SimpleNamespace(root=root, source=source, outside=outside, verifier=verifier,
                           task=task, profile=profile, contract=contract, directory=directory)


@pytest.mark.parametrize("drift", ["control", "outside"])
def test_worker_failure_cannot_checkpoint_control_or_out_of_scope_drift(checkpoint_repo, drift):
    repo = checkpoint_repo
    expected = snapshot(repo.root, repo.task)

    class FailedWorker:
        def run(self, instruction, *, session_id=None):
            repo.source.write_text("partial", encoding="utf-8")
            target = repo.verifier if drift == "control" else repo.outside
            target.write_text(target.read_text(encoding="utf-8") + "\n# unexpected change\n", encoding="utf-8")
            raise RuntimeError("worker disconnected after changing files")

    result = harness.execute(repo.root, repo.directory, repo.contract, executor=FailedWorker())
    identity = harness.read_json(repo.directory / "identity.json")
    assert result["status"] == "HARNESS-ERROR"
    assert result["verified"] is False
    assert result["stop_reason"] == "ownership_retained"
    assert identity["snapshot"] == expected
    assert snapshot(repo.root, repo.task) != expected


def test_verifier_cannot_accept_then_mutate_the_source_it_just_checked(checkpoint_repo):
    repo = checkpoint_repo
    repo.source.write_text("correct", encoding="utf-8")
    repo.verifier.write_text(
        repo.verifier.read_text(encoding="utf-8") + "answer.write_text('wrong')\n", encoding="utf-8")
    expected = snapshot(repo.root, repo.task)

    class ForbiddenWorker:
        def run(self, instruction, *, session_id=None):
            pytest.fail("a corrupted preflight must not dispatch coding work")

    result = harness.execute(repo.root, repo.directory, repo.contract, executor=ForbiddenWorker())
    assert result["checks"][0]["returncode"] == 0
    assert repo.source.read_text(encoding="utf-8") == "wrong"
    assert result["status"] == "HARNESS-ERROR"
    assert result["verified"] is False
    # The modified source has not passed acceptance and must not become the
    # identity used for a later successful-result cache or automatic resume.
    assert harness.read_json(repo.directory / "identity.json")["snapshot"] == expected
