"""Offline boundaries for project-only Codex skill installation and diagnosis."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

from prax.integrations.codex.project import doctor_project_codex, install_project_codex


SKILL = Path(".codex/skills/prax-shift/SKILL.md")
RECEIPT = Path(".prax/codex-install.json")


def bundled_skill() -> bytes:
    return files("prax.assets.codex").joinpath("prax", "skills", "prax-shift", "SKILL.md").read_bytes()


def tree_state(root: Path) -> dict:
    state = {}
    for path in root.rglob("*"):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[key] = ("symlink", str(path.readlink()))
        elif path.is_file():
            state[key] = ("file", path.read_bytes())
        elif path.is_dir():
            state[key] = ("directory",)
    return state


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for relative, contents in {
        "business.py": "do_not_touch = True\n",
        ".claude/skills/existing/SKILL.md": "Existing Claude skill\n",
        ".agents/skills/existing/SKILL.md": "Generated skill mirror\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    for relative in (".codex/config.toml", ".agents/plugins/marketplace.json", "plugins/prax/private.txt"):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("untouched user configuration\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("an injected offline probe must not start a CLI, worker, or scheduler")

    monkeypatch.setattr("subprocess.run", no_process)
    monkeypatch.setattr("subprocess.Popen", no_process)
    return root


@pytest.fixture
def probe(project):
    calls = []

    def matching_probe(root, *, codex_bin=None):
        assert Path(root).resolve() == project.resolve()
        calls.append((Path(root), codex_bin))
        discovered = [{"name": "prax-shift", "path": str(project / SKILL),
                       "scope": "repo", "enabled": True, "pluginId": None}] if (project / SKILL).is_file() else []
        return {
            "verified": True,
            "skills": discovered,
            "errors": [], "codex_version": "codex-cli 0.153.4",
        }

    matching_probe.calls = calls
    return matching_probe


def install(project, probe, **kwargs):
    return install_project_codex(project_root=str(project), codex_bin="/offline/codex", probe=probe, **kwargs)


def doctor(project, probe):
    return doctor_project_codex(project_root=str(project), codex_bin="/offline/codex", probe=probe)


def test_install_writes_only_project_skill_and_receipt(project, probe):
    before = tree_state(project.parent)
    result = install(project, probe)
    assert result["status"] == "ok"
    rendered = (project / SKILL).read_bytes()
    assert rendered.startswith(bundled_skill())
    assert b".prax/venv/bin/prax" in rendered
    assert b"Do not silently" in rendered
    receipt = json.loads((project / RECEIPT).read_text(encoding="utf-8"))
    assert len(receipt["source_sha256"]) == 64
    assert set(receipt["source_sha256"]) <= set("0123456789abcdef")
    assert receipt["version"].endswith(f"+codex.project-{receipt['source_sha256'][:16]}")
    assert receipt["files"][SKILL.as_posix()] == hashlib.sha256(rendered).hexdigest()
    after = tree_state(project.parent)
    assert all(after.get(key) == value for key, value in before.items())
    added_files = {key for key in after.keys() - before.keys() if after[key][0] == "file"}
    assert added_files == {f"project/{SKILL.as_posix()}", f"project/{RECEIPT.as_posix()}"}
    assert probe.calls
    assert doctor(project, probe)["status"] == "ok"


def test_install_is_idempotent_without_rewriting_receipt(project, probe):
    install(project, probe)
    before = tree_state(project.parent)
    assert install(project, probe)["status"] == "ok"
    assert tree_state(project.parent) == before


def test_dry_run_neither_writes_nor_probes(project):
    before = tree_state(project.parent)

    def forbidden_probe(*args, **kwargs):
        pytest.fail("dry-run must not probe")

    result = install(project, forbidden_probe, dry_run=True)
    assert result["dry_run"] is True
    assert tree_state(project.parent) == before


def test_foreign_same_name_skill_is_not_overwritten(project, probe):
    target = project / SKILL
    target.parent.mkdir(parents=True)
    target.write_text("This is my unrelated custom skill.\n", encoding="utf-8")
    before = tree_state(project.parent)
    with pytest.raises(ValueError):
        install(project, probe)
    assert tree_state(project.parent) == before
    assert not probe.calls


def test_reinstall_does_not_overwrite_user_edits(project, probe):
    install(project, probe)
    target = project / SKILL
    target.write_bytes(target.read_bytes() + b"\nLocal user instruction.\n")
    before = tree_state(project.parent)
    assert doctor(project, probe)["status"] != "ok"
    with pytest.raises(ValueError):
        install(project, probe)
    assert tree_state(project.parent) == before


@pytest.mark.parametrize("relative", [".codex", ".codex/skills", ".codex/skills/prax-shift", ".prax"])
def test_symlinked_install_parent_is_rejected_without_writing_outside(project, probe, relative):
    outside = project.parent / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("preserve", encoding="utf-8")
    link = project / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    before = tree_state(project.parent)
    with pytest.raises(ValueError):
        install(project, probe)
    assert tree_state(project.parent) == before
    assert not probe.calls


def test_symlinked_skill_file_is_not_followed(project, probe):
    outside = project.parent / "outside-skill.md"
    outside.write_text("preserve", encoding="utf-8")
    target = project / SKILL
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    before = tree_state(project.parent)
    with pytest.raises(ValueError):
        install(project, probe)
    assert tree_state(project.parent) == before


def test_symlinked_receipt_is_not_followed(project, probe):
    outside = project.parent / "outside-receipt.json"
    outside.write_text("{}\n", encoding="utf-8")
    target = project / RECEIPT
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    before = tree_state(project.parent)
    with pytest.raises(ValueError):
        install(project, probe)
    assert tree_state(project.parent) == before


@pytest.mark.parametrize("relative", [".prax", ".codex/skills"])
def test_regular_file_in_install_parent_is_rejected_before_any_write(project, probe, relative):
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Existing ordinary file; not an install directory.\n", encoding="utf-8")
    before = tree_state(project.parent)
    with pytest.raises(ValueError):
        install(project, probe)
    assert tree_state(project.parent) == before
    assert not probe.calls


def test_global_install_cannot_hide_missing_project_install(project):
    before = tree_state(project.parent)

    def global_only(root, *, codex_bin=None):
        return {"verified": True, "skills": [
            {"name": "prax-shift", "path": str(project.parent / "fake-home/plugins/prax/skills/prax-shift/SKILL.md"),
             "scope": "user", "enabled": True, "pluginId": "prax@personal"},
        ], "errors": [], "codex_version": "codex-cli 0.153.4"}

    assert doctor(project, global_only)["status"] != "ok"
    assert tree_state(project.parent) == before
    assert not (project / SKILL).exists()


@pytest.mark.parametrize("mismatch", ["wrong_path", "global_scope", "disabled", "plugin", "empty", "unverified", "error"])
def test_doctor_requires_exact_enabled_repo_skill(project, probe, mismatch):
    install(project, probe)
    before = tree_state(project.parent)

    def mismatched(root, *, codex_bin=None):
        result = probe(root, codex_bin=codex_bin)
        skill = result["skills"][0]
        if mismatch == "wrong_path":
            skill["path"] = str(project / ".agents/skills/prax-shift/SKILL.md")
        elif mismatch == "global_scope":
            skill["scope"] = "user"
        elif mismatch == "disabled":
            skill["enabled"] = False
        elif mismatch == "plugin":
            skill["pluginId"] = "prax@personal"
        elif mismatch == "empty":
            result["skills"] = []
        elif mismatch == "unverified":
            result["verified"] = False
        else:
            result["errors"] = ["project skill could not be loaded"]
        return result

    assert doctor(project, mismatched)["status"] != "ok"
    assert tree_state(project.parent) == before


def test_install_does_not_claim_loaded_when_probe_finds_nothing(project):
    def empty_probe(root, *, codex_bin=None):
        return {"verified": True, "skills": [], "errors": [], "codex_version": "codex-cli 0.153.4"}

    result = install(project, empty_probe)
    assert result["status"] != "ok"
    assert (project / SKILL).is_file()


def test_doctor_with_corrupt_receipt_is_read_only_and_not_ok(project, probe):
    install(project, probe)
    (project / RECEIPT).write_text("{broken json", encoding="utf-8")
    before = tree_state(project.parent)
    assert doctor(project, probe)["status"] != "ok"
    assert tree_state(project.parent) == before


def test_explicit_project_disable_outranks_positive_native_probe(project, probe):
    install(project, probe)
    config = project / ".codex/config.toml"
    contents = (
        '# Preserve this project configuration exactly.\nmodel = "existing-model"\n\n'
        '[[skills.config]]\n'
        f'path = {json.dumps(str(project / SKILL))}\n'
        'enabled = false\n'
    ).encode("utf-8")
    config.write_bytes(contents)
    before = tree_state(project.parent)
    # The fake native API deliberately reports enabled=True, as an isolated
    # probe can miss project configuration. The explicit local setting wins.
    assert probe(project)["skills"][0]["enabled"] is True
    assert doctor(project, probe)["status"] == "degraded"
    assert config.read_bytes() == contents
    assert tree_state(project.parent) == before


@pytest.mark.parametrize("receipt_change", ["modify", "delete"])
def test_receipt_change_during_upgrade_probe_prevents_all_skill_writes(project, probe, monkeypatch, receipt_change):
    install(project, probe)
    skill_path = project / SKILL
    receipt_path = project / RECEIPT
    original_skill = skill_path.read_bytes()
    original_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    updated_skill = original_skill + b"\nNew bundled release instruction.\n"
    payload = {SKILL.as_posix(): updated_skill}
    digest = hashlib.sha256(SKILL.as_posix().encode() + b"\0" + updated_skill).hexdigest()
    version = original_receipt["version"].split("+", 1)[0] + f"+codex.project-{digest[:16]}"
    monkeypatch.setattr("prax.integrations.codex.project._assets", lambda: (payload, version, digest))
    other_receipt = dict(original_receipt, operator_note="Changed by another project window during discovery")
    other_bytes = (json.dumps(other_receipt, indent=2) + "\n").encode("utf-8")
    expected_state = None

    def concurrent_probe(root, *, codex_bin=None):
        nonlocal expected_state
        assert expected_state is None, "the conflicting upgrade must stop before a second probe"
        if receipt_change == "modify":
            receipt_path.write_bytes(other_bytes)
        else:
            receipt_path.unlink()
        expected_state = tree_state(project.parent)
        return probe(root, codex_bin=codex_bin)

    with pytest.raises(ValueError, match="receipt changed"):
        install(project, concurrent_probe)
    assert expected_state is not None
    assert skill_path.read_bytes() == original_skill
    if receipt_change == "modify":
        assert receipt_path.read_bytes() == other_bytes
    else:
        assert not receipt_path.exists()
    assert tree_state(project.parent) == expected_state


@pytest.mark.parametrize("input_change", ["skill", "receipt_modify", "receipt_delete", "config_disable"])
def test_doctor_rejects_project_input_changes_during_probe_without_writing(project, probe, input_change):
    install(project, probe)
    expected_state = None

    def concurrent_probe(root, *, codex_bin=None):
        nonlocal expected_state
        assert expected_state is None, "doctor must not restart discovery after input drift"
        if input_change == "skill":
            skill = project / SKILL
            skill.write_bytes(skill.read_bytes() + b"\nUser edit during discovery.\n")
        elif input_change == "receipt_modify":
            receipt = project / RECEIPT
            data = json.loads(receipt.read_text(encoding="utf-8"))
            data["operator_note"] = "Changed by another project window during discovery"
            receipt.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        elif input_change == "receipt_delete":
            (project / RECEIPT).unlink()
        else:
            (project / ".codex/config.toml").write_text(
                '# Operator explicitly disabled the project skill.\n[[skills.config]]\n'
                f'path = {json.dumps(str(project / SKILL))}\nenabled = false\n',
                encoding="utf-8",
            )
        expected_state = tree_state(project.parent)
        # Native metadata still names the enabled repo skill, so only the
        # post-probe input check can reject this stale success observation.
        return probe(root, codex_bin=codex_bin)

    result = doctor(project, concurrent_probe)
    assert expected_state is not None
    assert result["status"] == "degraded"
    assert result["checks"]["project_inputs_unchanged"] is False
    assert tree_state(project.parent) == expected_state


def test_successful_doctor_does_not_inherit_probe_claim_of_current_task_activation(project, probe):
    install(project, probe)
    before = tree_state(project.parent)

    def overclaiming_probe(root, *, codex_bin=None):
        result = probe(root, codex_bin=codex_bin)
        result["activation_verified"] = True
        return result

    result = doctor(project, overclaiming_probe)
    assert result["status"] == "ok"
    assert result["activation_verified"] is False
    assert tree_state(project.parent) == before
