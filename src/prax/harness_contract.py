"""Repository-neutral contracts and cooperative drift checks for the harness.

These checks detect collaboration mistakes; they are not a security sandbox.
Only explicitly declared repositories are inspected. A broad parent scope does
not authorize work in a nested repository, and does not monitor its contents.
No command here switches branches, writes the index, or creates worktrees.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


class HarnessContractError(ValueError):
    """An invalid contract, changed control input, or workspace drift."""


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_RUN_DIRS = (
    PurePosixPath(".prax/harness-runs"),
    PurePosixPath(".prax/runs"),
    PurePosixPath(".prax/logs/cron"),
)


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise HarnessContractError("contract must contain only JSON values") from exc


def _fields(value: Any, required: set[str], optional: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise HarnessContractError(f"{label} must be an object")
    missing, unknown = required - value.keys(), value.keys() - required - optional
    if missing or unknown:
        raise HarnessContractError(
            f"{label}: missing fields {sorted(missing)}, unknown fields {sorted(unknown)}"
        )


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise HarnessContractError(f"{label} must be a nonempty string without NUL")
    return value


def _integer(value: Any, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise HarnessContractError(f"{label} must be an integer in {low}..{high}")
    return value


def _relative(base: Path, value: Any, label: str) -> tuple[str, Path]:
    text = _text(value, label)
    # Contracts use portable POSIX relative paths even on other host systems.
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text or re.match(r"^[A-Za-z]:", text):
        raise HarnessContractError(f"{label} must be a relative path without '..'")
    normalized = path.as_posix()
    target = base / normalized
    try:
        target.resolve().relative_to(base.resolve())
    except (ValueError, RuntimeError, OSError) as exc:
        raise HarnessContractError(f"{label} escapes its root through a symlink") from exc
    return normalized, target


def _argv(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise HarnessContractError(f"{label} must be a nonempty argument array")
    for arg in value:
        _text(arg, label)
    return value


def _git(repo: Path, *args: str) -> bytes:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args], cwd=repo, env=env,
            capture_output=True, check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessContractError(f"cannot inspect repository {repo}: {exc}") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise HarnessContractError(f"git inspection failed in {repo}: {message}")
    return result.stdout


def _branch(repo: Path) -> str:
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
    if not branch:
        raise HarnessContractError(f"repository requires an attached branch: {repo}")
    return branch


def _gitlinks(repo: Path) -> list[str]:
    return [
        os.fsdecode(entry.split(b"\t", 1)[1])
        for entry in _git(repo, "ls-files", "--stage", "-z").split(b"\0")
        if entry.startswith(b"160000 ")
    ]


def _within(path: str, parent: str) -> bool:
    return parent == "." or path == parent or path.startswith(parent + "/")


def load_contract(root: Path, task: dict, profile: dict) -> dict:
    """Validate and detach a normalized JSON contract from the caller's input.

    The returned ``{task, profile}`` is the fixed run specification; callers must
    keep it unchanged and separately compare source configuration on resume.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise HarnessContractError("workspace root must be an existing directory")
    task, profile = _json_copy(task), _json_copy(profile)
    _fields(task, {"schemaVersion", "id", "goal", "kind", "repositories", "verifier", "budget"}, set(), "task")
    _fields(profile, {"schemaVersion"}, {"policy_files", "adapter", "worker", "model"}, "profile")
    for label, obj in (("task", task), ("profile", profile)):
        _integer(obj["schemaVersion"], 1, 1, label + ".schemaVersion")
    if not _ID.fullmatch(_text(task["id"], "task.id")):
        raise HarnessContractError("task.id must be a safe 1..80 character identifier")
    _text(task["goal"], "task.goal")
    if task["kind"] not in {"code-check", "verification"}:
        raise HarnessContractError("task.kind must be 'code-check' or 'verification'")
    verification_only = task["kind"] == "verification"
    if profile.setdefault("worker", "codex") not in {"codex", "claude"}:
        raise HarnessContractError("profile.worker must be 'codex' or 'claude'")
    if "model" in profile:
        _text(profile["model"], "profile.model")
        if profile["worker"] == "claude" and not profile["model"].startswith("claude-"):
            raise HarnessContractError("Claude profile.model must use an explicit claude- model ID")
    policies = profile.setdefault("policy_files", [])
    if not isinstance(policies, list):
        raise HarnessContractError("profile.policy_files must be an array")
    for i, value in enumerate(policies):
        normalized, file = _relative(root, value, "policy file")
        if not file.is_file():
            raise HarnessContractError(f"policy file is missing or not a file: {normalized}")
        policies[i] = normalized
    if "adapter" in profile:
        _fields(profile["adapter"], {"argv"}, set(), "profile.adapter")
        _argv(profile["adapter"]["argv"], "profile.adapter.argv")
    elif verification_only:
        raise HarnessContractError("verification tasks require a project adapter")

    repos = task["repositories"]
    if not isinstance(repos, list) or not repos:
        raise HarnessContractError("task.repositories must be a nonempty array")
    seen: set[str] = set()
    seen_roots: set[Path] = set()
    for repo in repos:
        _fields(repo, {"path", "branch", "scope"}, set(), "repository")
        name, folder = _relative(root, repo["path"], "repository.path")
        if name in seen:
            raise HarnessContractError(f"duplicate repository: {name}")
        seen.add(name)
        repo["path"] = name
        actual_root = Path(os.fsdecode(_git(folder, "rev-parse", "--show-toplevel")).strip()).resolve()
        if actual_root != folder.resolve():
            raise HarnessContractError(f"repository.path is not a Git repository root: {name}")
        if actual_root in seen_roots:
            raise HarnessContractError(f"duplicate repository through path alias: {name}")
        seen_roots.add(actual_root)
        expected = _text(repo["branch"], "repository.branch")
        if _branch(folder) != expected:
            raise HarnessContractError(f"repository branch does not match declaration: {name}")
        if not isinstance(repo["scope"], list):
            raise HarnessContractError("repository.scope must be an array")
        if verification_only and repo["scope"]:
            raise HarnessContractError("verification repository.scope must be empty; source editing is forbidden")
        if not verification_only and not repo["scope"]:
            raise HarnessContractError("repository.scope must be a nonempty array")
        repo["scope"] = [_relative(folder, item, "repository.scope")[0] for item in repo["scope"]]
    for repo in repos:
        for link in _gitlinks(root / repo["path"]):
            full = (PurePosixPath(repo["path"]) / link).as_posix()
            if full not in seen and any(_within(scope, link) for scope in repo["scope"]):
                raise HarnessContractError(f"scope enters undeclared nested repository: {full}")

    verifier = task["verifier"]
    _fields(verifier, {"argv", "cwd", "timeout_seconds"}, {"control_files", "report_schema"}, "task.verifier")
    if verification_only:
        if verifier.get("report_schema") != "prax-verification-v1":
            raise HarnessContractError("verification requires verifier.report_schema='prax-verification-v1'")
    elif "report_schema" in verifier:
        raise HarnessContractError("verifier.report_schema is only supported for verification tasks")
    _argv(verifier["argv"], "task.verifier.argv")
    verifier["cwd"], directory = _relative(root, verifier["cwd"], "task.verifier.cwd")
    if not directory.is_dir():
        raise HarnessContractError("task.verifier.cwd must be an existing directory")
    _integer(verifier["timeout_seconds"], 1, 7200, "task.verifier.timeout_seconds")
    if "control_files" in verifier:
        if not isinstance(verifier["control_files"], list) or len(verifier["control_files"]) > 200:
            raise HarnessContractError("verifier.control_files must be an array of at most 200 files")
        for i, value in enumerate(verifier["control_files"]):
            name, file = _relative(root, value, "verifier control file")
            if not file.is_file():
                raise HarnessContractError(f"verifier control file is missing: {name}")
            verifier["control_files"][i] = name
    budget = task["budget"]
    if verification_only:
        _fields(budget, {"wall_seconds"}, {"max_iterations", "stuck_after"}, "task.budget")
        _integer(budget.setdefault("max_iterations", 0), 0, 0, "verification budget.max_iterations")
        _integer(budget.setdefault("stuck_after", 0), 0, 0, "verification budget.stuck_after")
    else:
        _fields(budget, {"max_iterations", "wall_seconds", "stuck_after"}, set(), "task.budget")
        _integer(budget["max_iterations"], 1, 20, "task.budget.max_iterations")
        _integer(budget["stuck_after"], 2, 10, "task.budget.stuck_after")
    _integer(budget["wall_seconds"], 1, 7200, "task.budget.wall_seconds")
    return {"task": task, "profile": profile}


def _file_fingerprint(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    digest = hashlib.sha256()
    digest.update(str(stat.S_IFMT(info.st_mode)).encode() + b":" + str(info.st_mode & 0o111).encode() + b"\0")
    if stat.S_ISLNK(info.st_mode):
        digest.update(os.fsencode(os.readlink(path)))
    elif stat.S_ISREG(info.st_mode):
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    elif not stat.S_ISDIR(info.st_mode):
        raise HarnessContractError(f"unsupported workspace file type: {path}")
    return digest.hexdigest()


def _own_output(root: Path, repo: Path, filename: str) -> bool:
    relative = (repo / filename).relative_to(root)
    return any(relative == path or path in relative.parents for path in _RUN_DIRS)


def _metadata_fingerprint(path: Path) -> str:
    """Cheap cooperative drift evidence for unrelated untracked artifacts.

    Deliberately does not read file contents; this is not tamper-proof evidence.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    metadata = [info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]
    if stat.S_ISLNK(info.st_mode):
        metadata.append(os.readlink(path))
    return "metadata:" + hashlib.sha256(json.dumps(metadata).encode()).hexdigest()


def snapshot(root: Path, task: dict) -> list[dict]:
    """Hash declared repositories' Git identity, index entries, and dirty files.

    Git identifies changed/untracked files; clean file contents and undeclared
    repositories are never traversed. Root harness journals are excluded.
    Tracked dirty files and in-scope untracked files get content hashes; unrelated
    untracked artifacts get metadata fingerprints to keep large workspaces fast.
    """
    root = Path(root).resolve()
    result = []
    for declaration in task["repositories"]:
        name, repo = _relative(root, declaration["path"], "repository.path")
        branch = _branch(repo)
        head = _git(repo, "rev-parse", "HEAD").decode().strip()
        index = hashlib.sha256(_git(repo, "ls-files", "--stage", "-z")).hexdigest()
        tracked = set(_git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--ignore-submodules=all", "--name-only", "-z", "HEAD", "--").split(b"\0"))
        names = set(tracked)
        names.update(_git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
        dirty = {}
        for raw in sorted(names):
            if not raw:
                continue
            filename = os.fsdecode(raw).rstrip("/")
            if not _own_output(root, repo, filename):
                # Check parent containment without following a final symlink;
                # tracked symlinks are hashed as links, never read as targets.
                _relative(repo, str(PurePosixPath(filename).parent), "dirty file parent")
                in_scope = any(_within(filename, scope) for scope in declaration["scope"])
                fingerprint = _file_fingerprint if raw in tracked or in_scope else _metadata_fingerprint
                dirty[filename] = fingerprint(repo / filename)
        result.append({"path": name, "branch": branch, "head": head, "index": index, "dirty": dirty})
    return result


def guard(before: list[dict], after: list[dict], task: dict, *, resume: bool = False) -> None:
    """Refuse Git drift and changes outside the declared repository scopes."""
    previous = {item["path"]: item for item in before}
    current = {item["path"]: item for item in after}
    expected = {item["path"]: item for item in task["repositories"]}
    if (len(previous) != len(before) or len(current) != len(after)
            or set(previous) != set(expected) or set(current) != set(expected)):
        raise HarnessContractError("snapshot repository set changed or does not match task")
    for name, declaration in expected.items():
        old, new = previous[name], current[name]
        if old["branch"] != declaration["branch"] or new["branch"] != declaration["branch"]:
            raise HarnessContractError(f"branch drift in {name}")
        for field in ("head", "index"):
            if old[field] != new[field]:
                raise HarnessContractError(f"{field} drift in {name}")
        changed = sorted(
            path for path in set(old["dirty"]) | set(new["dirty"])
            if old["dirty"].get(path) != new["dirty"].get(path)
        )
        if resume and changed:
            raise HarnessContractError(f"workspace changed since checkpoint in {name}: {changed}")
        outside = [path for path in changed if not any(_within(path, scope) for scope in declaration["scope"])]
        if outside:
            raise HarnessContractError(f"out-of-scope changes in {name}: {outside}")


def _control_file(root: Path, filename: Path) -> tuple[str, str]:
    try:
        relative = filename.relative_to(root).as_posix()
    except ValueError as exc:
        raise HarnessContractError(f"control file is outside workspace: {filename}") from exc
    _, safe = _relative(root, relative, "control file")
    if not safe.is_file():
        raise HarnessContractError(f"control file is missing or not a file: {relative}")
    # Include link identity and target bytes, so retargeting or editing a policy
    # symlink inside the workspace invalidates the frozen controls.
    digest = hashlib.sha256(_file_fingerprint(safe).encode())
    if safe.is_symlink():
        digest.update(_file_fingerprint(safe.resolve()).encode())
    return relative, digest.hexdigest()


def control_fingerprint(root: Path, contract: dict) -> str:
    """Bind task/profile, policies, verifier scripts, and selected test inputs.

    Source task/profile files must additionally be compared by the caller. This
    is a reproducibility guard, not a restriction on arbitrary command effects.
    """
    root = Path(root).resolve()
    files = {}
    for name in contract["profile"].get("policy_files", []):
        _, path = _relative(root, name, "policy file")
        key, digest = _control_file(root, path)
        files[key] = digest
    # Bind the conventional profile when present, including whitespace/source
    # edits that have not yet been reloaded into the frozen object.
    profile_file = root / ".prax" / "harness.json"
    if profile_file.exists() or profile_file.is_symlink():
        key, digest = _control_file(root, profile_file)
        files[key] = digest
    verifier = contract["task"]["verifier"]
    for name in verifier.get("control_files", []):
        key, digest = _control_file(root, root / name)
        files[key] = digest
    _, cwd = _relative(root, verifier["cwd"], "verifier.cwd")
    argv = verifier["argv"]
    commands = [(cwd, argv)]
    if "adapter" in contract["profile"]:
        commands.append((root, contract["profile"]["adapter"]["argv"]))
    for command_cwd, command in commands:
        for token in command:
            if token.startswith("-"):
                continue
            candidate = command_cwd / token.split("::", 1)[0]
            if Path(token).is_absolute() and not candidate.is_relative_to(root):
                # System executables are not workspace control files.
                continue
            if candidate.is_file() or candidate.is_symlink():
                key, digest = _control_file(root, candidate)
                files[key] = digest
    is_pytest = Path(argv[0]).name == "pytest" or any(
        argv[i:i + 2] == ["-m", "pytest"] for i in range(len(argv) - 1)
    )
    if is_pytest:
        selections = []
        start = argv.index("pytest") + 1 if "pytest" in argv[1:] else 1
        for token in argv[start:]:
            if token.startswith("-"):
                continue
            candidate = cwd / token.split("::", 1)[0]
            if candidate.is_file() or candidate.is_dir():
                selections.append(candidate)
        if not selections:
            raise HarnessContractError("pytest verifier must name an explicit test file or directory")
        candidates = set()
        for selected in selections:
            # Resolve containment before traversal, including symlink targets.
            try:
                relative = selected.relative_to(root).as_posix()
            except ValueError as exc:
                raise HarnessContractError("pytest selection is outside workspace") from exc
            _relative(root, relative, "pytest selection")
            paths = selected.rglob("*.py") if selected.is_dir() else [selected]
            for path in paths:
                if path.is_file() or path.is_symlink():
                    candidates.add(path)
                    if len(candidates) > 200:
                        raise HarnessContractError("pytest selection exceeds 200 control files; narrow the verifier")
            folder = selected if selected.is_dir() else selected.parent
            while folder.is_relative_to(root):
                for name in ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
                    config = folder / name
                    if config.is_file() or config.is_symlink():
                        candidates.add(config)
                if folder == root:
                    break
                folder = folder.parent
        for path in sorted(candidates):
            key, digest = _control_file(root, path)
            files[key] = digest
    if "unittest" in argv and "discover" in argv:
        start, pattern = ".", "test*.py"
        for i, token in enumerate(argv):
            if token in ("-s", "--start-directory") and i + 1 < len(argv):
                start = argv[i + 1]
            elif token.startswith("--start-directory="):
                start = token.split("=", 1)[1]
            elif token in ("-p", "--pattern") and i + 1 < len(argv):
                pattern = argv[i + 1]
            elif token.startswith("--pattern="):
                pattern = token.split("=", 1)[1]
        if "/" in pattern or "\\" in pattern:
            raise HarnessContractError("unittest discovery pattern must be a filename pattern")
        _, directory = _relative(cwd, start, "unittest discovery directory")
        if not directory.is_dir():
            raise HarnessContractError("unittest discovery directory is missing")
        candidates = []
        for path in directory.rglob(pattern):
            if path.is_file() or path.is_symlink():
                candidates.append(path)
                if len(candidates) > 200:
                    raise HarnessContractError("unittest discovery exceeds 200 control files; narrow the verifier")
        if not candidates:
            raise HarnessContractError("unittest discovery selects no test files")
        for path in sorted(candidates):
            key, digest = _control_file(root, path)
            files[key] = digest
    payload = {"contract": contract, "files": files}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
