"""Contract/drift tests use disposable Git fixtures, never a project checkout."""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path

import pytest

from prax.harness_contract import (
    HarnessContractError,
    control_fingerprint,
    guard,
    load_contract,
    snapshot,
)


def git(root: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Harness test",
        "GIT_AUTHOR_EMAIL": "harness@example.invalid",
        "GIT_COMMITTER_NAME": "Harness test",
        "GIT_COMMITTER_EMAIL": "harness@example.invalid",
    }
    return subprocess.check_output(["git", *args], cwd=root, env=env).decode().strip()


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, dict, dict]:
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("VALUE = 1\n")
    (root / "README.md").write_text("Initial readme\n")
    (root / "AGENTS.md").write_text("Use the declared scope.\n")
    (root / "verify.py").write_text("assert True\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    task = {
        "schemaVersion": 1,
        "id": "repair-module",
        "goal": "Repair the module and run its unit tests.",
        "kind": "code-check",
        "repositories": [{"path": ".", "branch": "main", "scope": ["src"]}],
        "verifier": {"argv": ["python3", "verify.py"], "cwd": ".", "timeout_seconds": 10},
        "budget": {"max_iterations": 3, "wall_seconds": 120, "stuck_after": 2},
    }
    profile = {"schemaVersion": 1, "worker": "codex", "policy_files": ["AGENTS.md"]}
    return root, task, profile


def test_load_detaches_input_and_normalizes_paths(workspace):
    root, task, profile = workspace
    task["repositories"][0]["path"] = "./"
    frozen = load_contract(root, task, profile)
    task["repositories"][0]["scope"].append("README.md")
    profile["policy_files"].clear()
    assert frozen["task"]["repositories"] == [{"path": ".", "branch": "main", "scope": ["src"]}]
    assert frozen["profile"]["policy_files"] == ["AGENTS.md"]


@pytest.mark.parametrize("selection", ["tests", "tests/test_example.py::test_example"])
def test_pytest_selected_controls_cannot_change(workspace, selection):
    root, task, profile = workspace
    (root / "tests").mkdir()
    acceptance = root / "tests/test_example.py"
    acceptance.write_text("def test_example(): assert False\n")
    task["verifier"]["argv"] = ["python3", "-m", "pytest", selection]
    contract = load_contract(root, task, profile)
    before = control_fingerprint(root, contract)
    acceptance.write_text("def test_example(): assert True\n")
    assert control_fingerprint(root, contract) != before
    before = control_fingerprint(root, contract)
    (root / "tests/conftest.py").write_text("# new collection policy\n")
    assert control_fingerprint(root, contract) != before


def test_pytest_requires_explicit_selection(workspace):
    root, task, profile = workspace
    task["verifier"]["argv"] = ["pytest", "-q"]
    with pytest.raises(HarnessContractError, match="explicit test"):
        control_fingerprint(root, load_contract(root, task, profile))


def test_claude_model_cannot_silently_fall_back(workspace):
    root, task, profile = workspace
    profile.update(worker="claude", model="unrelated-model")
    with pytest.raises(HarnessContractError, match="explicit claude-"):
        load_contract(root, task, profile)


def test_explicit_verifier_data_is_bound_and_must_exist(workspace):
    root, task, profile = workspace
    task["verifier"]["control_files"] = ["expected.json"]
    with pytest.raises(HarnessContractError, match="control file is missing"):
        load_contract(root, task, profile)
    data = root / "expected.json"
    data.write_text('{"answer": 1}')
    contract = load_contract(root, task, profile)
    before = control_fingerprint(root, contract)
    data.write_text('{"answer": 2}')
    assert control_fingerprint(root, contract) != before


@pytest.mark.parametrize("field,value", [
    ("schemaVersion", True), ("schemaVersion", 2), ("kind", "regression"),
    ("id", "../escape"), ("id", "bad name"), ("goal", "  "),
])
def test_invalid_task_fields_are_rejected(workspace, field, value):
    root, task, profile = workspace
    task[field] = value
    with pytest.raises(HarnessContractError):
        load_contract(root, task, profile)


@pytest.mark.parametrize("key,value", [
    ("max_iterations", 0), ("max_iterations", 21), ("wall_seconds", 7201),
    ("stuck_after", 1), ("wall_seconds", True),
])
def test_budget_is_bounded(workspace, key, value):
    root, task, profile = workspace
    task["budget"][key] = value
    with pytest.raises(HarnessContractError):
        load_contract(root, task, profile)


@pytest.mark.parametrize("scope", ["../outside", "/tmp", "a/../../b", "C:\\temp", "a\\b"])
def test_scope_paths_must_stay_in_repository(workspace, scope):
    root, task, profile = workspace
    task["repositories"][0]["scope"] = [scope]
    with pytest.raises(HarnessContractError):
        load_contract(root, task, profile)


def test_symlink_escape_is_rejected(workspace, tmp_path):
    root, task, profile = workspace
    (root / "escape").symlink_to(tmp_path)
    task["repositories"][0]["scope"] = ["escape/new.py"]
    with pytest.raises(HarnessContractError, match="symlink"):
        load_contract(root, task, profile)


def test_branch_and_repository_root_must_match(workspace):
    root, task, profile = workspace
    task["repositories"][0]["branch"] = "some-other-branch"
    with pytest.raises(HarnessContractError, match="branch"):
        load_contract(root, task, profile)
    task["repositories"][0]["branch"] = "main"
    task["repositories"][0]["path"] = "src"
    with pytest.raises(HarnessContractError, match="repository root"):
        load_contract(root, task, profile)


def test_missing_policy_and_unrecognized_keys_rejected(workspace):
    root, task, profile = workspace
    profile["policy_files"] = ["missing.md"]
    with pytest.raises(HarnessContractError, match="policy file"):
        load_contract(root, task, profile)
    profile["policy_files"] = []
    task["authorize_platform"] = "android"
    with pytest.raises(HarnessContractError, match="unknown fields"):
        load_contract(root, task, profile)


def test_snapshot_only_hashes_dirty_files_and_allows_scoped_changes(workspace):
    root, task, _ = workspace
    before = snapshot(root, task)
    assert before[0]["dirty"] == {}
    (root / "src" / "module.py").write_text("VALUE = 2\n")
    (root / "src" / "new.py").write_text("NEW = True\n")
    after = snapshot(root, task)
    assert set(after[0]["dirty"]) == {"src/module.py", "src/new.py"}
    guard(before, after, task)
    with pytest.raises(HarnessContractError, match="checkpoint"):
        guard(before, after, task, resume=True)


def test_existing_out_of_scope_dirt_is_preserved_but_cannot_change(workspace):
    root, task, _ = workspace
    readme = root / "README.md"
    readme.write_text("User work\n")
    before = snapshot(root, task)
    (root / "src" / "module.py").write_text("VALUE = 2\n")
    guard(before, snapshot(root, task), task)
    readme.write_text("Clobbered user work\n")
    with pytest.raises(HarnessContractError, match="out-of-scope.*README"):
        guard(before, snapshot(root, task), task)


def test_unrelated_untracked_artifacts_use_metadata_and_detect_changes(workspace, monkeypatch):
    root, task, _ = workspace
    artifact = root / "big-untracked.bin"
    artifact.write_bytes(b"original contents")
    original_open = Path.open

    def refuse_artifact_reads(path, *args, **kwargs):
        if path == artifact:
            raise AssertionError("out-of-scope untracked file contents must not be read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_artifact_reads)
    before = snapshot(root, task)
    assert before[0]["dirty"]["big-untracked.bin"].startswith("metadata:")
    info = artifact.stat()
    os.utime(artifact, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
    with pytest.raises(HarnessContractError, match="big-untracked.bin"):
        guard(before, snapshot(root, task), task)


def test_restore_out_of_scope_file_is_a_change(workspace):
    root, task, _ = workspace
    (root / "README.md").write_text("User work\n")
    before = snapshot(root, task)
    (root / "README.md").write_text("Initial readme\n")
    with pytest.raises(HarnessContractError, match="out-of-scope"):
        guard(before, snapshot(root, task), task)


@pytest.mark.parametrize("mutation,drift", [("stage", "index"), ("commit", "head"), ("branch", "branch")])
def test_git_identity_drift_rejected(workspace, mutation, drift):
    root, task, _ = workspace
    before = snapshot(root, task)
    if mutation == "branch":
        git(root, "checkout", "-qb", "different")
    else:
        (root / "src" / "module.py").write_text("VALUE = 3\n")
        git(root, "add", "src/module.py")
        if mutation == "commit":
            git(root, "commit", "-qm", "new fixture commit")
    with pytest.raises(HarnessContractError, match=drift):
        guard(before, snapshot(root, task), task)


def test_rename_checks_both_old_and_new_paths(workspace):
    root, task, _ = workspace
    before = snapshot(root, task)
    (root / "src" / "module.py").rename(root / "moved.py")
    with pytest.raises(HarnessContractError, match="moved.py"):
        guard(before, snapshot(root, task), task)


def test_run_output_is_ignored_but_profile_is_not(workspace):
    root, task, _ = workspace
    before = snapshot(root, task)
    for name in ("harness-runs", "runs", "logs/cron"):
        directory = root / ".prax" / name / "task"
        directory.mkdir(parents=True)
        (directory / "events.jsonl").write_text("{}\n")
    guard(before, snapshot(root, task), task, resume=True)
    (root / ".prax" / "harness.json").write_text("{}\n")
    with pytest.raises(HarnessContractError, match="harness.json"):
        guard(before, snapshot(root, task), task)


@pytest.mark.parametrize("name", ["logs/cron-other/log.txt", "logs/other/log.txt", "cron.yaml"])
def test_cron_output_exclusion_does_not_hide_other_prax_files(workspace, name):
    root, task, _ = workspace
    before = snapshot(root, task)
    path = root / ".prax" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not scheduler output\n")
    with pytest.raises(HarnessContractError, match="workspace changed"):
        guard(before, snapshot(root, task), task, resume=True)


def test_nested_gitlink_scope_requires_explicit_repository(workspace):
    root, task, profile = workspace
    oid = git(root, "rev-parse", "HEAD")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{oid},vendor/child")
    task["repositories"][0]["scope"] = ["vendor/child/src"]
    with pytest.raises(HarnessContractError, match="undeclared nested"):
        load_contract(root, task, profile)
    # Parent directory ownership does not trigger traversal of child repos.
    task["repositories"][0]["scope"] = ["."]
    load_contract(root, task, profile)


def test_multiple_repositories_are_snapshotted_independently(workspace):
    root, task, profile = workspace
    child = root / "child"
    child.mkdir()
    git(child, "init", "-q", "-b", "dev")
    (child / "app.py").write_text("pass\n")
    git(child, "add", ".")
    git(child, "commit", "-qm", "child fixture")
    # Declare the parent gitlink so the root snapshot does not treat it as an
    # ordinary untracked folder.
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{git(child, 'rev-parse', 'HEAD')},child")
    git(root, "commit", "-qm", "child gitlink")
    task["repositories"].append({"path": "child", "branch": "dev", "scope": ["app.py"]})
    contract = load_contract(root, task, profile)
    before = snapshot(root, contract["task"])
    (child / "app.py").write_text("VALUE = 2\n")
    after = snapshot(root, contract["task"])
    assert before[0] == after[0]
    assert set(after[1]["dirty"]) == {"app.py"}
    guard(before, after, contract["task"])


@pytest.mark.parametrize("target", ["AGENTS.md", "verify.py", "adapter.py", ".prax/harness.json"])
def test_control_fingerprint_binds_all_controls(workspace, target):
    root, task, profile = workspace
    (root / "adapter.py").write_text("pass\n")
    profile["adapter"] = {"argv": ["python3", "adapter.py"]}
    (root / ".prax").mkdir()
    (root / ".prax" / "harness.json").write_text("{}\n")
    frozen = load_contract(root, task, profile)
    before = control_fingerprint(root, frozen)
    (root / target).write_text("changed\n")
    assert control_fingerprint(root, frozen) != before


def test_control_fingerprint_binds_task_specification(workspace):
    root, task, profile = workspace
    contract = load_contract(root, task, profile)
    other = copy.deepcopy(contract)
    other["task"]["goal"] = "Another goal"
    assert control_fingerprint(root, contract) != control_fingerprint(root, other)


def test_control_fingerprint_missing_policy_rejected(workspace):
    root, task, profile = workspace
    contract = load_contract(root, task, profile)
    (root / "AGENTS.md").unlink()
    with pytest.raises(HarnessContractError, match="missing"):
        control_fingerprint(root, contract)


def test_policy_symlink_target_content_is_bound(workspace):
    root, task, profile = workspace
    (root / "POLICY.md").symlink_to("AGENTS.md")
    profile["policy_files"] = ["POLICY.md"]
    contract = load_contract(root, task, profile)
    before = control_fingerprint(root, contract)
    (root / "AGENTS.md").write_text("Changed policy\n")
    assert control_fingerprint(root, contract) != before


def test_unittest_discovery_binds_contents_and_membership(workspace):
    root, task, profile = workspace
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_math.py").write_text("assert True\n")
    (tests / "test_other.py").write_text("assert True\n")
    (tests / "helper.txt").write_text("not a selected test\n")
    task["verifier"]["argv"] = ["python3", "-m", "unittest", "discover", "-s", "tests"]
    contract = load_contract(root, task, profile)
    before = control_fingerprint(root, contract)
    (tests / "helper.txt").write_text("changed unrelated file\n")
    assert control_fingerprint(root, contract) == before
    (tests / "test_math.py").write_text("assert False\n")
    assert control_fingerprint(root, contract) != before
    changed = control_fingerprint(root, contract)
    (tests / "test_math.py").unlink()
    assert control_fingerprint(root, contract) != changed


def test_unittest_discovery_refuses_unbounded_selection(workspace):
    root, task, profile = workspace
    tests = root / "tests"
    tests.mkdir()
    for number in range(201):
        (tests / f"test_{number}.py").write_text("pass\n")
    task["verifier"]["argv"] = ["python3", "-m", "unittest", "discover", "-s", "tests"]
    contract = load_contract(root, task, profile)
    with pytest.raises(HarnessContractError, match="200"):
        control_fingerprint(root, contract)


def test_unittest_discovery_refuses_zero_test_files(workspace):
    root, task, profile = workspace
    (root / "tests").mkdir()
    task["verifier"]["argv"] = ["python3", "-m", "unittest", "discover", "-s", "tests"]
    contract = load_contract(root, task, profile)
    with pytest.raises(HarnessContractError, match="no test files"):
        control_fingerprint(root, contract)


def test_verifier_cannot_bind_a_symlink_outside_workspace(workspace, tmp_path):
    root, task, profile = workspace
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")
    (root / "verify.py").unlink()
    (root / "verify.py").symlink_to(outside)
    contract = load_contract(root, task, profile)
    with pytest.raises(HarnessContractError, match="symlink"):
        control_fingerprint(root, contract)


def test_snapshot_hashes_symlink_without_reading_external_target(workspace, tmp_path):
    root, task, _ = workspace
    (root / "src" / "link").symlink_to(tmp_path / "missing-target")
    result = snapshot(root, task)
    assert "src/link" in result[0]["dirty"]
