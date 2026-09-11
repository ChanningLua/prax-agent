"""Single-use, report-backed verification without a coding worker or repair loop.

The project adapter and verifier are trusted operator code. Filesystem guards
detect cooperative drift; they do not sandbox commands or attest remote state.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

from .harness_contract import control_fingerprint, guard, load_contract, snapshot

REPORT_SCHEMA = "prax-verification-v1"
CLEANUP_SECONDS = 12


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("invalid verification report fields")


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("verification report requires nonempty text without NUL")


def _safe_file(path: Path, directory: Path):
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("verification evidence must be an absolute path without symlinks")
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise ValueError("verification evidence escapes its execution directory") from exc
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("verification evidence must be a regular file")


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field in verification report")
        result[key] = value
    return result


def validate_report(path: Path, *, directory: Path, run_id: str, execution_id: str,
                    returncode: int | None) -> tuple[dict, dict]:
    """Validate fresh identity, explicit execution facts, and local evidence."""
    _safe_file(path, directory)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("verification report exceeds 4 MiB")
    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")
    report = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object,
                        parse_constant=invalid_constant)
    _fields(report, {"schemaVersion", "run_id", "execution_id", "status", "tests",
                     "coverage", "evidence", "context"}, {"message"})
    if type(report["schemaVersion"]) is not int or report["schemaVersion"] != 1:
        raise ValueError("unsupported verification report schemaVersion")
    if report["run_id"] != run_id or report["execution_id"] != execution_id:
        raise ValueError("verification report belongs to a different run or execution")
    status = report["status"]
    if status not in ("passed", "failed", "blocked"):
        raise ValueError("invalid verification report status")
    if type(returncode) is not int or returncode != {"passed": 0, "failed": 1, "blocked": 2}[status]:
        raise ValueError("verifier exit code does not match the structured report")
    tests = report["tests"]
    if not isinstance(tests, list) or len(tests) > 10000:
        raise ValueError("verification tests must be an array of at most 10000 items")
    counts = {"passed": 0, "failed": 0, "blocked": 0, "not_executed": 0}
    seen = set()
    for test in tests:
        _fields(test, {"id", "status"})
        _text(test["id"])
        if test["id"] in seen:
            raise ValueError("verification test IDs must be unique")
        seen.add(test["id"])
        if test["status"] not in ("passed", "failed", "blocked", "not-executed"):
            raise ValueError("invalid verification test status")
        counts[test["status"].replace("-", "_")] += 1
    if status == "passed" and (not tests or counts["passed"] != len(tests)):
        raise ValueError("passed verification requires nonempty, entirely passed tests")
    if status == "failed" and (not counts["failed"] or counts["blocked"]):
        raise ValueError("failed verification requires a failed test and no blocked tests")
    _fields(report["coverage"], {"status", "reason"})
    if report["coverage"]["status"] != "unknown":
        raise ValueError("verification cannot infer coverage from selected tests")
    _text(report["coverage"]["reason"])
    if not isinstance(report["context"], dict):
        raise ValueError("verification context must be an object")  # noqa: TRY004 — invalid persisted JSON
    if "message" in report:
        _text(report["message"])
    evidence = report["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 10000:
        raise ValueError("verification evidence must be an array of at most 10000 items")
    if status == "passed" and not evidence:
        raise ValueError("passed verification requires file-backed evidence")
    seen_paths = set()
    for item in evidence:
        _fields(item, {"path", "sha256"})
        _text(item["path"])
        if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]):
            raise ValueError("verification evidence requires a SHA-256 digest")
        artifact = Path(item["path"])
        _safe_file(artifact, directory)
        if artifact in seen_paths:
            raise ValueError("duplicate verification evidence path")
        seen_paths.add(artifact)
        if _digest(artifact) != item["sha256"].lower():
            raise ValueError("verification evidence digest mismatch")
    return report, {"total": len(tests), "completed": counts["passed"] + counts["failed"], **counts}


def _reserve(directory: Path, value: dict):
    """Persist before dispatch. Even a partial marker after a crash blocks reuse."""
    marker = directory / "verification-dispatch.json"
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute_verification(root: Path, run_dir: Path, contract: dict, *, execution_id="",
                         authorized=False) -> dict:
    # Imported lazily: the common supervisor routes here, never through its
    # code-check executor construction or repair loop.
    from .repository_harness import (
        BoundaryStop,
        CommandInterrupted,
        PauseRequested,
        adapter,
        command,
        pause_requested,
        read_json,
        run_directory,
        write_json,
    )
    if authorized is not True:
        raise ValueError("verification requires explicit authorization for this invocation")
    task = contract["task"]
    root = root.resolve()
    if run_dir != run_directory(root, task["id"]):
        raise ValueError("verification must use its task's canonical run directory")
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = run_dir / "verification-dispatch.json"
    if marker.exists():
        raise ValueError("verification was already dispatched; automatic retry and resume are forbidden")
    previous = read_json(run_dir / "result.json") if (run_dir / "result.json").exists() else {}
    if previous and previous.get("status") != "RUNNING":
        raise ValueError("verification run already has a result; use a new authorized task ID")
    execution_id = execution_id or uuid.uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", execution_id):
        raise ValueError("verification execution ID must be a fresh UUID hex identifier")
    if previous and previous.get("execution_id") != execution_id:
        raise ValueError("verification execution does not match its supervisor")
    frozen = run_dir / "contract.json"
    if frozen.exists() and read_json(frozen) != contract:
        raise ValueError("run contract changed; use a new task ID")
    if not frozen.exists():
        write_json(frozen, contract)
    started = time.monotonic()
    remaining = lambda: task["budget"]["wall_seconds"] - (time.monotonic() - started)
    checks = []
    outcome = {"status": "HARNESS-ERROR", "verified": False, "stop_reason": "verification_preflight"}
    fingerprint = before = None
    dispatches = 0
    attempt_reserved = False
    report_path = None
    try:
        load_contract(root, task, contract["profile"])
        fingerprint = control_fingerprint(root, contract)
        before = snapshot(root, task)
        identity_path = run_dir / "identity.json"
        if identity_path.exists():
            identity = read_json(identity_path)
            if identity["fingerprint"] != fingerprint:
                raise BoundaryStop("verification controls changed; use a new task ID")
            guard(identity["snapshot"], before, task, resume=True)
        else:
            write_json(identity_path, {"fingerprint": fingerprint, "snapshot": before})
        if pause_requested(run_dir):
            raise PauseRequested("verification cancelled before dispatch; resume is forbidden")
        output_root = run_dir / "verification"
        if output_root.is_symlink() or output_root.resolve() != output_root:
            raise ValueError("verification directory must not follow symbolic links")
        output_root.mkdir(exist_ok=True)
        evidence_dir = output_root / execution_id
        evidence_dir.mkdir(exist_ok=False)
        report_path = evidence_dir / "report.json"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("E2E_") and key not in {
                   "PRAX_RUN_ID", "PRAX_EXECUTION_ID", "PRAX_VERIFICATION_DIR",
                   "PRAX_VERIFICATION_REPORT", "PRAX_VERIFICATION_AUTHORIZED"}}
        env.update(PRAX_RUN_ID=task["id"], PRAX_EXECUTION_ID=execution_id,
                   PRAX_VERIFICATION_DIR=str(evidence_dir), PRAX_VERIFICATION_REPORT=str(report_path),
                   PRAX_VERIFICATION_AUTHORIZED="1")
        _reserve(run_dir, {"run_id": task["id"], "execution_id": execution_id,
                           "reserved_at": time.time(), "report": str(report_path)})
        attempt_reserved = True

        def check_source():
            if control_fingerprint(root, contract) != fingerprint:
                raise BoundaryStop("verification controls changed during execution")
            guard(before, snapshot(root, task), task, resume=True)

        def lifecycle(phase):
            if pause_requested(run_dir):
                raise PauseRequested("verification cancelled; resume is forbidden")
            if remaining() <= 0:
                raise BoundaryStop("verification wall budget exhausted")
            check_source()
            adapter(root, run_dir, contract, phase, remaining(), env=env)
            check_source()

        lifecycle("preflight")
        lifecycle("check")
        if report_path.exists():
            raise BoundaryStop("verification report existed before verifier dispatch")
        dispatches = 1
        result = command(task["verifier"]["argv"], root / task["verifier"]["cwd"],
                         min(task["verifier"]["timeout_seconds"], remaining()),
                         evidence_dir / "verifier.log", env=env, cleanup_seconds=CLEANUP_SECONDS)
        checks.append(result)
        lifecycle("check")
        report, summary = validate_report(report_path, directory=evidence_dir, run_id=task["id"],
                                          execution_id=execution_id, returncode=result.get("returncode"))
        check_source()
        outcome = {"status": {"passed": "PASS", "failed": "FEATURE-FAIL", "blocked": "BLOCKED"}[report["status"]],
                   "verified": report["status"] == "passed", "stop_reason": "verification_completed",
                   "verification_report": report, "summary": summary, "context": report["context"],
                   "coverage": report["coverage"], "report_sha256": _digest(report_path)}
    except PauseRequested as exc:
        outcome.update(status="BLOCKED", stop_reason="verification_cancelled", error=str(exc),
                       external_process_stop="unconfirmed")
    except (CommandInterrupted, KeyboardInterrupt) as exc:
        outcome.update(status="HARNESS-ERROR", stop_reason="verification_interrupted", error=str(exc),
                       external_process_stop="unconfirmed")
    except (BoundaryStop, Exception) as exc:  # noqa: BLE001 — fail closed, never dispatch a repair
        outcome.update(status="HARNESS-ERROR", stop_reason="verification_error", error=str(exc))
    finally:
        if before is not None and fingerprint is not None:
            try:
                if control_fingerprint(root, contract) != fingerprint:
                    raise ValueError("verification controls changed during execution")
                current = snapshot(root, task)
                guard(before, current, task, resume=True)
            except (OSError, ValueError, KeyError) as exc:
                outcome.update(status="HARNESS-ERROR", verified=False,
                               stop_reason="verification_drift", error=str(exc))
    outcome.update(schemaVersion=1, run_id=task["id"], execution_id=execution_id,
                   kind="verification", iterations=0, worker=None, verification_dispatches=dispatches,
                   attempt_reserved=attempt_reserved,
                   retry_allowed=False, resume_allowed=False, ownership="not-claimed",
                   elapsed_seconds=round(time.monotonic() - started, 3),
                   cleanup_budget_seconds=CLEANUP_SECONDS, verifier=task["verifier"], checks=checks,
                   evidence_directory=str(run_dir), publication="not-executed",
                   report_path=str(report_path) if report_path else None)
    write_json(run_dir / "result.json", outcome)
    return outcome
