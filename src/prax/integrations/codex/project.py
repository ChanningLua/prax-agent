"""Project-scoped Codex skill installation, without a user plugin install.

Codex 0.153.4 discovers repository ``.codex/skills``. Discovery is checked
through its read-only app-server API in an isolated process; file copying is
not activation evidence. This is a repo skill, not a marketplace plugin.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from importlib.resources import files
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

SKILL_NAME = "prax-shift"
SKILL_RELATIVE = Path(".codex/skills/prax-shift/SKILL.md")
RECEIPT_RELATIVE = Path(".prax/codex-install.json")
MECHANISM = "codex-repository-skill"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _runtime_identity() -> dict:
    try:
        version = package_version("prax")
    except PackageNotFoundError:
        version = "unpackaged-source"
    return {"python": sys.executable, "module_path": str(files("prax")), "version": version}


def _project(project_root: str | Path) -> Path:
    requested = Path(project_root).expanduser().absolute()
    if requested.is_symlink():
        raise ValueError("project root must not be a symbolic link")
    root = requested.resolve()
    if not root.is_dir() or root == Path.home().resolve():
        raise ValueError("--local requires an existing project directory, not the user home")
    return root


def _safe_path(root: Path, relative: Path) -> Path:
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        if current.is_symlink():
            raise ValueError(f"project install path must not follow symbolic links: {current}")
        if index < len(relative.parts) - 1 and current.exists() and not current.is_dir():
            raise ValueError(f"project install parent must be a directory: {current}")
    if current.exists() and not current.is_file():
        raise ValueError(f"expected a regular project install file: {current}")
    return current


def _project_skill_enabled(root: Path) -> bool:
    """Respect project opt-outs even on hosts that misreport enabled in skills/list."""
    config_path = _safe_path(root, Path(".codex/config.toml"))
    if not config_path.exists():
        return True
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    skills = config.get("skills", {})
    if not isinstance(skills, dict) or not isinstance(skills.get("config", []), list):
        raise ValueError("invalid project skills.config; preserve the existing Codex configuration")
    targets = {root / SKILL_RELATIVE, (root / SKILL_RELATIVE).parent}
    for entry in skills.get("config", []):
        if not isinstance(entry, dict):
            raise ValueError("invalid project skills.config entry")
        if entry.get("enabled") is not False:
            continue
        raw = entry.get("path")
        if not isinstance(raw, str):
            raise ValueError("disabled project skill requires a string path")
        path = Path(raw).expanduser()
        resolved = {path.resolve()} if path.is_absolute() else {
            (config_path.parent / path).resolve(), (root / path).resolve(),
        }
        if targets.intersection(resolved):
            return False
    return True


def _read_receipt(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"invalid Prax project receipt: {path}") from exc
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1
            or value.get("scope") != "project" or value.get("mechanism") != MECHANISM
            or not isinstance(value.get("files"), dict)):
        raise ValueError(f"unrecognized Prax project receipt; preserve it for inspection: {path}")
    return value


def _snapshot_project_inputs(root: Path, payload: dict[str, bytes]) -> dict[str, bytes | None]:
    """Capture diagnosis inputs, without following project symlinks or writing."""
    relative_paths = {*payload, RECEIPT_RELATIVE.as_posix(), ".codex/config.toml"}
    snapshot = {}
    for relative in sorted(relative_paths):
        path = _safe_path(root, Path(relative))
        snapshot[relative] = path.read_bytes() if path.exists() else None
    return snapshot


def _assets() -> tuple[dict[str, bytes], str, str]:
    source = files("prax.assets.codex").joinpath("prax")
    manifest = json.loads(source.joinpath(".codex-plugin", "plugin.json").read_text(encoding="utf-8"))
    skill = source.joinpath("skills", SKILL_NAME, "SKILL.md").read_text(encoding="utf-8")
    # The bundled instructions remain the source of truth. Local invocation
    # must not accidentally resolve an older user-global Prax executable.
    skill += (
        "\n## Project-local Prax runtime\n\n"
        "This is a repository-scoped skill, not the user-global Prax plugin. "
        "For Prax commands in this project, use `.prax/venv/bin/prax` from the "
        "repository root (or `.prax/venv/bin/python -m prax`). Do not silently "
        "fall back to a global `prax` executable. If this environment is missing, "
        "report that prerequisite instead of installing packages or starting work. "
        "Preserve the existing `.prax/harness.json` and project adapter.\n"
    )
    payload = {SKILL_RELATIVE.as_posix(): skill.encode("utf-8")}
    digest = _hash(b"".join(name.encode() + b"\0" + data for name, data in sorted(payload.items())))
    base = str(manifest.get("version", "1.0.0")).split("+", 1)[0]
    return payload, f"{base}+codex.project-{digest[:16]}", digest


def _probe(root: Path, codex_bin: str | None, probe: Callable | None) -> dict:
    if probe is None:
        from .probe import probe_project_skills
        probe = probe_project_skills
    try:
        result = probe(root, codex_bin=codex_bin)
        if not isinstance(result, dict):
            raise ValueError("Codex discovery returned a non-object result")
        return result
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        return {"verified": False, "skills": [], "errors": [str(exc)]}


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prax-install-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def install_project_codex(*, project_root: str | Path, dry_run: bool = False,
                          codex_bin: str | None = None, probe: Callable | None = None) -> dict:
    root = _project(project_root)
    payload, version, source_sha256 = _assets()
    receipt_path = _safe_path(root, RECEIPT_RELATIVE)
    previous = _read_receipt(receipt_path)
    if not _project_skill_enabled(root):
        raise ValueError("Prax project skill is explicitly disabled; preserve the project configuration")
    if previous and previous.get("project_root") != str(root):
        raise ValueError("Prax project receipt belongs to another project; do not rebind it implicitly")
    before = {}
    for relative, data in payload.items():
        path = _safe_path(root, Path(relative))
        before[relative] = path.read_bytes() if path.exists() else None
        if path.exists():
            expected = previous.get("files", {}).get(relative) if previous else None
            if expected != _hash(before[relative]):
                raise ValueError(f"preserve existing or user-modified project skill: {path}")
    receipt = {
        "schemaVersion": 1, "scope": "project", "mechanism": MECHANISM,
        "project_root": str(root), "version": version, "source_sha256": source_sha256,
        "files": {name: _hash(data) for name, data in payload.items()},
    }
    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode()
    previous_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
    changes = [name for name, data in payload.items() if before[name] != data]
    if previous_receipt != receipt_bytes:
        changes.append(RECEIPT_RELATIVE.as_posix())
    if dry_run:
        return {"target": "codex", "scope": "project", "mechanism": MECHANISM,
                "dry_run": True, "status": "planned", "project_root": str(root),
                "skill_path": str(root / SKILL_RELATIVE), "receipt_path": str(receipt_path),
                "version": version, "changed_files": changes, "activation_verified": False,
                "text": f"Would install project-scoped Prax skill at {root / SKILL_RELATIVE}"}

    # Check API availability before writing anything. A missing CLI or an
    # unsupported read-only probe must not trigger a global fallback.
    capability = _probe(root, codex_bin, probe)
    if capability.get("verified") is not True:
        raise RuntimeError("Codex project discovery unavailable; no install performed: "
                           + "; ".join(map(str, capability.get("errors", []))))

    # Discovery can take time. Revalidate the entire write set before the
    # first mutation, so a receipt changed during probing does not leave an
    # upgraded skill paired with somebody else's (or a removed) receipt.
    _safe_path(root, RECEIPT_RELATIVE)
    if (receipt_path.read_bytes() if receipt_path.exists() else None) != previous_receipt:
        raise ValueError("project install receipt changed concurrently; preserve it")
    for relative in payload:
        path = _safe_path(root, Path(relative))
        current = path.read_bytes() if path.exists() else None
        if current != before[relative]:
            raise ValueError(f"project skill changed during install; preserve it: {path}")

    for relative, data in payload.items():
        path = _safe_path(root, Path(relative))
        current = path.read_bytes() if path.exists() else None
        if current != before[relative]:
            raise ValueError(f"project skill changed during install; preserve it: {path}")
        if current != data:
            _atomic_write(path, data)
    _safe_path(root, RECEIPT_RELATIVE)
    if (receipt_path.read_bytes() if receipt_path.exists() else None) != previous_receipt:
        raise ValueError("project install receipt changed concurrently; preserve it")
    if previous_receipt != receipt_bytes:
        _atomic_write(receipt_path, receipt_bytes)
    result = doctor_project_codex(project_root=root, codex_bin=codex_bin, probe=probe)
    result.update(dry_run=False, changed=bool(changes), changed_files=changes)
    return result


def doctor_project_codex(*, project_root: str | Path, codex_bin: str | None = None,
                         probe: Callable | None = None) -> dict:
    root = _project(project_root)
    payload, expected_version, expected_source = _assets()
    checks: dict[str, bool] = {}
    errors = []
    receipt = None
    inputs_before = None
    try:
        inputs_before = _snapshot_project_inputs(root, payload)
        checks["project_skill_enabled"] = _project_skill_enabled(root)
        receipt = _read_receipt(_safe_path(root, RECEIPT_RELATIVE))
        checks["receipt"] = receipt is not None
        checks["project_identity"] = bool(receipt and receipt.get("project_root") == str(root))
        checks["version"] = bool(receipt and receipt.get("version") == expected_version
                                 and receipt.get("source_sha256") == expected_source)
        for relative, data in payload.items():
            path = _safe_path(root, Path(relative))
            actual = _hash(path.read_bytes()) if path.is_file() else None
            checks[f"file:{relative}"] = bool(
                actual == _hash(data) and receipt and receipt["files"].get(relative) == actual)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        checks["project_files"] = False

    # No local artifact means no reason to inspect any user-global plugin.
    discovery = _probe(root, codex_bin, probe) if all(checks.values()) else {
        "verified": False, "skills": [], "errors": ["local files are missing or inconsistent"],
    }
    # A native probe can take several seconds. Do not combine old local
    # checks with discovery metadata after another window changed the input.
    if inputs_before is not None:
        try:
            checks["project_inputs_unchanged"] = (
                _snapshot_project_inputs(root, payload) == inputs_before)
            if not checks["project_inputs_unchanged"]:
                errors.append("Project inputs changed during diagnosis; rerun doctor against the current files.")
        except (OSError, ValueError) as exc:
            checks["project_inputs_unchanged"] = False
            errors.append(str(exc))
    expected_path = root / SKILL_RELATIVE
    matching = [entry for entry in discovery.get("skills", []) if isinstance(entry, dict)
                and entry.get("name") == SKILL_NAME and entry.get("scope") == "repo"
                and entry.get("enabled") is True and entry.get("pluginId") is None
                and entry.get("path") == str(expected_path)]
    checks["codex_repo_discovery"] = (discovery.get("verified") is True and len(matching) == 1
                                      and not discovery.get("errors"))
    errors.extend(str(error) for error in discovery.get("errors", []))
    missing = [name for name, passed in checks.items() if not passed]
    status = "ok" if not missing else "degraded"
    version = receipt.get("version") if receipt else None
    return {
        "target": "codex", "scope": "project", "mechanism": MECHANISM,
        "status": status, "project_root": str(root), "skill_path": str(expected_path),
        "receipt_path": str(root / RECEIPT_RELATIVE), "version": version,
        "expected_version": expected_version, "source_sha256": receipt.get("source_sha256") if receipt else None,
        "runtime": _runtime_identity(),
        "checks": checks, "missing": missing, "errors": errors,
        "codex_version": discovery.get("codex_version"), "discovery": discovery,
        "activation_verified": False,
        "text": (f"Codex project integration: {status}\nscope: project ({MECHANISM})\n"
                 f"skill_path: {expected_path}\nversion: {version or 'not-installed'}\n"
                 f"missing: {', '.join(missing) or 'none'}\n"
                 "Current-task activation is not verified; use this project in a new Codex task."),
    }
