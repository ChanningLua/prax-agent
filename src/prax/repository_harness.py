"""Portable repository harness: frozen intent, bounded workers, independent evidence.

Project-specific policy belongs to an optional argv adapter, not this module.
Commands and repository tests are trusted operator code. Scope checks are
cooperative drift detection, not a security sandbox for a malicious worker.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path

from .acceptance_evidence import non_execution_reason
from .core.command_verifier import classify_verify_status
from .core.orchestrator_loop import OrchestratorLoop, VerifyResult
from .core.run_journal import RunJournal
from .harness_contract import control_fingerprint, guard, load_contract, snapshot


class BoundaryStop(BaseException):
    """Stop without allowing the legacy loop to retry an environment failure."""


class PauseRequested(BoundaryStop):
    """An operator pause is durable and must not be resumed by a scheduler."""


class SupervisorTerminated(BoundaryStop):
    """A parent termination signal must pass through process-group cleanup."""


class CommandInterrupted(BoundaryStop):
    """A command stopped without confirming its external process tree is clean."""


def run_directory(root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
        raise ValueError("invalid run ID")
    directory = root / ".prax/harness-runs" / run_id
    if directory.resolve() != directory or (root / ".prax").is_symlink():
        raise ValueError("run directory must not follow symbolic links")
    for name in ("contract.json", "identity.json", "result.json", "pause.json", "attempt.json",
                 "verification-dispatch.json"):
        if (directory / name).is_symlink():
            raise ValueError("run control files must not be symbolic links")
    return directory


def pause_requested(directory: Path) -> bool:
    return (directory / "pause.json").exists()


def outcome_exit_code(result: dict) -> int:
    if result.get("status") == "PASS" and result.get("verified") is True:
        return 0
    if result.get("status") == "FEATURE-FAIL":
        return 1
    if result.get("status") == "PAUSED":
        return 3
    return 2


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".next")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")  # noqa: TRY004 — invalid persisted JSON value
    return value


def test_runner(argv: list[str]) -> str | None:
    program = Path(argv[0]).name
    if program == "pytest":
        return "pytest"
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", program) and "-m" in argv:
        index = argv.index("-m")
        if "-c" not in argv[:index] and index + 1 < len(argv):
            return argv[index + 1]
    if argv[:4] == ["pnpm", "exec", "vitest", "run"]:
        return "vitest"
    if argv[:4] == ["node", "--import", "tsx", "--test"]:
        return "node-test"
    return None


def command(argv: list[str], cwd: Path, timeout: float, log: Path, *, env=None,
            cleanup_seconds: float = 0) -> dict:
    """Run argv without a shell; detached descendants require adapter cooperation."""
    if timeout <= 0:
        raise BoundaryStop("wall budget exhausted")
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                    **({"env": env} if env is not None else {}))
        except OSError as exc:
            return {"status": "HARNESS-ERROR", "returncode": None, "output": str(exc),
                    "command": argv, "log": str(log), "duration_seconds": 0}
        try:
            code = proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if cleanup_seconds:
                proc.terminate()
                try:
                    proc.wait(timeout=cleanup_seconds)
                except subprocess.TimeoutExpired:
                    proc.kill()
            else:
                proc.kill()
            proc.wait()
            # The supervisor also kills descendants even after this child exits.
            raise CommandInterrupted(f"command interrupted or timed out after {timeout:.1f}s: {argv[0]}")
    output = log.read_text(encoding="utf-8", errors="replace")
    status = classify_verify_status(code, output)
    # Common test runners distinguish a failed acceptance from an invalid or
    # empty collection. Zero executed tests cannot prove the declared goal.
    runner = test_runner(argv)
    missing_module = runner and re.search(
        r"(?m)^(?:[^\n]+:\s*)?No module named ['\"]?" + re.escape(runner) + r"['\"]?\s*$", output)
    if (missing_module
            or (runner == "pytest" and code not in (0, 1))
            or (runner == "unittest"
                and re.search(r"Ran 0 tests|_FailedTest", output))
            or (runner == "vitest" and ("No test files found" in output
                                        or 'Command "vitest" not found' in output))
            or (runner == "node-test" and (re.search(r"(?m)^(?:#|ℹ) tests 0\s*$", output)
                                            or "Cannot find package 'tsx'" in output))):
        status = "HARNESS-ERROR"
    no_execution = non_execution_reason(runner, argv, output)
    if no_execution:
        status = "HARNESS-ERROR"
    return {"status": status, "returncode": code,
            "output": output[-6000:], "command": argv, "log": str(log),
            "duration_seconds": round(time.monotonic() - started, 3),
            **({"evidence_error": no_execution} if no_execution else {})}


def adapter(root: Path, run_dir: Path, contract: dict, phase: str, remaining: float, *, env=None) -> None:
    config = contract["profile"].get("adapter")
    if not config:
        return
    result = command([*config["argv"], "--phase", phase, "--root", str(root),
                      "--run-dir", str(run_dir), "--contract", str(run_dir / "contract.json")],
                     root, min(remaining, 15), run_dir / f"adapter-{phase}.log",
                     **({"env": env} if env is not None else {}))
    if result["status"] != "PASS":
        raise BoundaryStop(f"project adapter {phase}: {result['output']}")


def execute(root: Path, run_dir: Path, contract: dict, *, executor=None,
            execution_id: str = "", authorize_verification=False) -> dict:
    """Run a window; caller must hold the run lock and supervise its process group.

    The injected executor is intended for offline contract tests. Production
    uses a frozen Claude/Codex worker choice with least-privilege defaults.
    """
    if contract["task"]["kind"] == "verification":
        from .verification_runtime import execute_verification
        return execute_verification(root, run_dir, contract, execution_id=execution_id,
                                    authorized=authorize_verification)
    task = contract["task"]
    run_dir.mkdir(parents=True, exist_ok=True)
    frozen_path = run_dir / "contract.json"
    if frozen_path.exists() and read_json(frozen_path) != contract:
        raise ValueError("run contract changed; use a new task ID")
    if not frozen_path.exists():
        write_json(frozen_path, contract)
    journal = RunJournal(str(run_dir), "worker")
    previous = read_json(run_dir / "result.json") if (run_dir / "result.json").exists() else {}
    started = time.monotonic()
    spent = float(previous.get("elapsed_seconds", 0))
    remaining = lambda: task["budget"]["wall_seconds"] - spent - (time.monotonic() - started)
    checks = []
    claimed = False
    before = None
    accepted_snapshot = None
    checkpoint_error = None
    outcome = {"status": "HARNESS-ERROR", "stop_reason": "preflight", "verified": False}
    dispatches = sum(e.get("step") == "dispatch" for e in journal.events())
    try:
        load_contract(root, task, contract["profile"])
        fingerprint = control_fingerprint(root, contract)
        before = snapshot(root, task)
        identity_path = run_dir / "identity.json"
        if identity_path.exists():
            identity = read_json(identity_path)
            if identity["fingerprint"] != fingerprint:
                raise BoundaryStop("task, verifier, or repository policy changed; create a new task ID")
            guard(identity["snapshot"], before, task, resume=True)
        else:
            write_json(identity_path, {"fingerprint": fingerprint, "snapshot": before})
        if previous.get("verified"):
            return previous
        if pause_requested(run_dir):
            raise PauseRequested("paused by operator; explicit resume required")
        if remaining() <= 0:
            raise BoundaryStop("wall budget exhausted; create a newly budgeted task explicitly")
        adapter(root, run_dir, contract, "preflight", remaining())
        adapter(root, run_dir, contract, "claim", remaining())
        claimed = True

        def checked_snapshot():
            if control_fingerprint(root, contract) != fingerprint:
                raise BoundaryStop("verifier or repository policy changed during execution")
            current = snapshot(root, task)
            guard(before, current, task)
            return current

        def check_boundary():
            nonlocal checkpoint_error
            if pause_requested(run_dir):
                raise PauseRequested("paused by operator; explicit resume required")
            try:
                prior_check = checked_snapshot()
                adapter(root, run_dir, contract, "check", remaining())
                after_check = checked_snapshot()
                # A lifecycle check may inspect ownership, but must not change
                # the source being accepted or its controls behind the verifier.
                guard(prior_check, after_check, task, resume=True)
                return after_check
            except (BoundaryStop, Exception) as exc:  # Failed checks cannot authorize checkpoints.
                checkpoint_error = str(exc)
                raise

        def verify():
            nonlocal accepted_snapshot, checkpoint_error
            before_verify = check_boundary()
            spec = task["verifier"]
            report = command(spec["argv"], root / spec["cwd"],
                             min(spec["timeout_seconds"], remaining()),
                             run_dir / f"verify-{len(journal.events())}.log")
            checks.append(report)
            journal.record("acceptance", output=report)
            after_verify = check_boundary()
            # A passing command must describe the same source it inspected;
            # even a trusted verifier can accidentally rewrite its inputs.
            try:
                guard(before_verify, after_verify, task, resume=True)
            except (BoundaryStop, Exception) as exc:  # Preserve the last valid checkpoint.
                checkpoint_error = str(exc)
                raise
            # Persist only a fully guarded checkpoint, never silently adopt drift.
            write_json(identity_path, {"fingerprint": fingerprint, "snapshot": after_verify})
            if report["status"] == "PASS":
                accepted_snapshot = after_verify
            return VerifyResult(passed=report["status"] == "PASS", status=report["status"],
                                output=report["output"] or f"exit={report['returncode']}")

        # Establish acceptance and environment health before dispatch/resume.
        preflight = verify()
        if preflight.status == "HARNESS-ERROR":
            outcome.update(stop_reason="harness_error")
        elif preflight.passed:
            outcome.update(status="PASS", stop_reason="verified", verified=True)
        else:
            # A previous inner-loop PASS may have been followed by a failed
            # project release. It must never override this window's fresh FAIL.
            journal.record("outcome", output={"stop_reason": "acceptance_invalidated",
                                              "verified": False, "iterations": dispatches})
            if executor is None:
                from .core.agent_host import build_step_executor
                _, executor = build_step_executor(
                    str(root), agent_host=contract["profile"]["worker"],
                    model=contract["profile"].get("model"),
                )

            class GuardedExecutor:
                def run(self, instruction, *, session_id=None):
                    nonlocal dispatches
                    check_boundary()
                    if dispatches >= task["budget"]["max_iterations"]:
                        raise BoundaryStop("worker iteration budget exhausted")
                    if remaining() <= 0:
                        raise BoundaryStop("wall budget exhausted")
                    dispatches += 1
                    journal.record("dispatch", output={"number": dispatches})
                    result = executor.run(instruction, session_id=session_id)
                    check_boundary()
                    return result

            def compose(goal, feedback, iteration):
                # Reload only the declared small policy documents, no global
                # memory or feature board from another task or project.
                policies = "\n\n".join((root / p).read_text(encoding="utf-8")
                                        for p in contract["profile"].get("policy_files", []))
                return ("Prax repository harness. Work only on this frozen task.\n"
                        "Read the scoped source before editing. Do not modify verifier, "
                        "policy, or run evidence. No Git mutations, temporary worktrees, "
                        "GUI/device/E2E execution, production actions, messaging, or scheduling. "
                        "The independent verifier determines acceptance. Report a blocker if "
                        "the task requires another scope or authority.\n"
                        f"Task contract:\n{json.dumps(task, ensure_ascii=False)}\n"
                        f"Repository policy:\n{policies}\nGoal: {goal}\n"
                        f"Last acceptance feedback:\n{feedback or preflight.output}")

            loop = OrchestratorLoop(journal=journal, executor=GuardedExecutor(), verifier=verify,
                                    compose=compose, max_iterations=task["budget"]["max_iterations"],
                                    stuck_after=task["budget"]["stuck_after"], error_limit=1)
            result = loop.run(task["goal"])
            outcome = {**asdict(result), "status": "PASS" if result.verified else
                       "FEATURE-FAIL" if result.stop_reason in {"max_iterations", "stuck_no_progress"}
                       else "HARNESS-ERROR"}
    except PauseRequested as exc:
        outcome = {"status": "PAUSED", "stop_reason": "paused", "verified": False,
                   "error": str(exc)}
        try:
            if before is not None:
                current = snapshot(root, task)
                guard(before, current, task)
                if control_fingerprint(root, contract) != fingerprint:
                    raise BoundaryStop("controls changed while pausing")
                write_json(identity_path, {"fingerprint": fingerprint, "snapshot": current})
        except (BoundaryStop, Exception) as error:  # noqa: BLE001 — pause cannot conceal an unsafe checkpoint
            outcome = {"status": "HARNESS-ERROR", "stop_reason": "boundary_stop",
                       "verified": False, "error": str(error)}
    except (BoundaryStop, Exception) as exc:  # noqa: BLE001 — failures must produce durable non-PASS evidence
        outcome = {"status": "HARNESS-ERROR", "stop_reason": "boundary_stop",
                   "verified": False, "error": str(exc)}
    finally:
        if claimed:
            try:
                # Retain ownership on drift; release may not conceal it.
                if checkpoint_error is not None:
                    raise BoundaryStop(checkpoint_error)
                before_release = checked_snapshot()
                if outcome.get("verified"):
                    if accepted_snapshot is None:
                        raise BoundaryStop("no accepted source checkpoint")
                    guard(accepted_snapshot, before_release, task, resume=True)
                adapter(root, run_dir, contract, "release", max(1, remaining()))
                after_release = checked_snapshot()
                guard(before_release, after_release, task, resume=True)
                # Preserve legal partial edits after a worker error without
                # upgrading them to acceptance. Resume still verifies first
                # and retains the same dispatch and wall-time budget.
                write_json(identity_path, {"fingerprint": fingerprint, "snapshot": after_release})
            except (BoundaryStop, Exception) as exc:  # noqa: BLE001 — never mask release failure with PASS
                outcome = {"status": "HARNESS-ERROR", "stop_reason": "ownership_retained",
                           "verified": False, "error": str(exc)}
    outcome.update(schemaVersion=1, run_id=task["id"], iterations=dispatches,
                   worker=contract["profile"]["worker"],
                   elapsed_seconds=round(spent + time.monotonic() - started, 3),
                   verifier=task["verifier"], checks=checks, evidence_directory=str(run_dir),
                   platform_validation="not-executed", publication="not-executed")
    write_json(run_dir / "result.json", outcome)
    return outcome


@contextmanager
def run_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("run already active; no concurrent resume") from exc
        yield handle.fileno()


@contextmanager
def repository_locks(root: Path, task: dict):
    """One cooperating harness task per Git common directory, across run IDs."""
    directories = set()
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    for repo in task["repositories"]:
        raw = subprocess.check_output(["git", "-C", str(root / repo["path"]), "rev-parse",
                                       "--path-format=absolute", "--git-common-dir"],
                                      text=True, timeout=15, env=env)
        directories.add(Path(raw.strip()).resolve())
    with ExitStack() as stack:
        descriptors = []
        for directory in sorted(directories):
            path = directory / "prax-harness.lock"
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            handle = stack.enter_context(os.fdopen(descriptor, "a"))
            descriptors.append(handle.fileno())
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f"repository already has an active harness task: {directory}") from exc
        # Close only; explicit LOCK_UN would also unlock the inherited child
        # descriptor after a supervisor shutdown.
        yield tuple(descriptors)


def terminate_group(proc, *, grace_seconds=0.15):
    """Bounded cleanup even if the worker parent has already exited."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            if grace_seconds == 0.15:
                time.sleep(0.15)
            else:
                deadline = time.monotonic() + grace_seconds
                while time.monotonic() < deadline:
                    proc.poll()  # Reap the parent before testing group liveness.
                    try:
                        os.killpg(proc.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    proc.wait()


def wait_for_runtime(proc, timeout: float, directory: Path) -> int:
    deadline = time.monotonic() + timeout
    while True:
        if pause_requested(directory):
            raise PauseRequested("paused")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("runtime", timeout)
        try:
            return proc.wait(timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            continue


@contextmanager
def termination_signals():
    """Route termination through cleanup, shielding TERM -> KILL -> wait."""
    if threading.current_thread() is not threading.main_thread():
        yield lambda: None
        return
    received = False
    cleaning = False

    def begin_cleanup():
        nonlocal cleaning
        cleaning = True

    def terminated(signum, _frame):
        nonlocal received
        if received or cleaning:
            return
        received = True
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SupervisorTerminated(f"terminated by {signal.Signals(signum).name}")

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        for sig in previous:
            signal.signal(sig, terminated)
        yield begin_cleanup
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _watch_parent(parent_fd: int) -> None:
    """A private pipe's EOF means the supervisor no longer owns this runtime.

    Only the parent owns its write end. On hard parent death, kill the entire
    dedicated runtime group immediately: TERM would kill this watcher before
    it could remove descendants that ignore TERM. Inherited lock FDs keep
    repository ownership until the runtime dies. Evidence stays RUNNING for
    inspection instead of claiming a crash-time checkpoint was verified.
    """
    if os.getpgrp() != os.getpid():
        raise RuntimeError("parent watchdog requires a dedicated runtime process group")
    os.set_inheritable(parent_fd, False)

    def watch():
        try:
            while os.read(parent_fd, 1):
                pass
        finally:
            os.killpg(os.getpid(), signal.SIGKILL)

    threading.Thread(target=watch, name="prax-parent-watch", daemon=True).start()


def supervise(root: Path, directory: Path, contract: dict, *, watch_pause=False,
              lock_fds: tuple[int, ...] = (), authorize_verification=False) -> dict:
    verification_only = contract["task"].get("kind") == "verification"
    previous = read_json(directory / "result.json") if (directory / "result.json").exists() else {}
    timeout = contract["task"]["budget"]["wall_seconds"] - previous.get("elapsed_seconds", 0)
    if timeout <= 0:
        raise ValueError("wall budget exhausted; create a newly budgeted task explicitly")
    started = time.monotonic()
    execution_id = uuid.uuid4().hex if watch_pause or verification_only else ""
    if watch_pause:
        write_json(directory / "result.json", {
            "schemaVersion": 1, "run_id": contract["task"]["id"], "status": "RUNNING",
            "verified": False, "execution_id": execution_id,
            "elapsed_seconds": previous.get("elapsed_seconds", 0),
        })
    parent_read, parent_write = os.pipe()
    proc = None
    reason = None
    try:
        with termination_signals() as begin_cleanup:
            try:
                with (directory / "runtime.log").open("a") as log:
                    proc = subprocess.Popen([sys.executable, "-c",
                                             ("import sys; from pathlib import Path; "
                                             "from prax.repository_harness import _child; "
                                             "_child(Path(sys.argv[1]), sys.argv[2], sys.argv[3], "
                                             "int(sys.argv[4]), tuple(map(int, sys.argv[6:])), "
                                             "authorize_verification=sys.argv[5] == '1')"),
                                             str(root), contract["task"]["id"], execution_id,
                                             str(parent_read), "1" if authorize_verification else "0",
                                             *(str(fd) for fd in lock_fds)],
                                            cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=True,
                                            pass_fds=(*lock_fds, parent_read))
                    # Keep both parent endpoints until final cleanup. Holding
                    # a read end does not delay EOF; avoiding an early close
                    # also removes the close/assignment signal race.
                    if watch_pause:
                        write_json(directory / "attempt.json", {"execution_id": execution_id, "pid": proc.pid})
                    code = wait_for_runtime(proc, timeout, directory) if watch_pause else proc.wait(timeout=timeout)
                    if code:
                        reason = f"runtime exited {code}"
            except OSError as exc:
                result = {"schemaVersion": 1, "run_id": contract["task"]["id"],
                          "status": "HARNESS-ERROR", "stop_reason": "runtime_start_failed",
                          "verified": False, "error": str(exc), "execution_id": execution_id,
                          "elapsed_seconds": previous.get("elapsed_seconds", 0) + time.monotonic() - started,
                          "evidence_directory": str(directory)}
                write_json(directory / "result.json", result)
                return result
            except PauseRequested:
                reason = "paused"
            except subprocess.TimeoutExpired:
                reason = "wall budget exhausted"
            except KeyboardInterrupt:
                reason = "interrupted"
            except SupervisorTerminated as exc:
                reason = str(exc)
            finally:
                begin_cleanup()
                if proc is not None:
                    if verification_only:
                        terminate_group(proc, grace_seconds=12)
                    else:
                        terminate_group(proc)
    finally:
        # Closing this endpoint also covers a signal during Popen before its
        # return value reaches proc: the inherited child sees EOF and stops.
        try:
            os.close(parent_read)
        finally:
            os.close(parent_write)
    if reason:
        result = {"schemaVersion": 1, "run_id": contract["task"]["id"],
                  "status": "PAUSED" if reason == "paused" else "HARNESS-ERROR",
                  "stop_reason": reason, "verified": False,
                  "elapsed_seconds": previous.get("elapsed_seconds", 0) + time.monotonic() - started,
                  "evidence_directory": str(directory), "ownership": "inspect-before-resume"}
        if verification_only:
            result.update(execution_id=execution_id, cleanup_budget_seconds=12,
                          retry_allowed=False, external_process_stop="unconfirmed",
                          ownership="not-claimed", resume_allowed=False)
            if reason == "paused":
                result["status"] = "BLOCKED"  # Single-use verification cannot be resumed.
        if reason == "paused":
            try:
                identity = read_json(directory / "identity.json")
                current = snapshot(root, contract["task"])
                guard(identity["snapshot"], current, contract["task"])
                if control_fingerprint(root, contract) != identity["fingerprint"]:
                    raise ValueError("controls changed while pausing")
                write_json(directory / "identity.json", {**identity, "snapshot": current})
            except (OSError, ValueError, KeyError) as exc:
                result.update(status="HARNESS-ERROR", stop_reason="unsafe_pause_checkpoint", error=str(exc))
        write_json(directory / "result.json", result)
        return result
    if not (directory / "result.json").exists():
        raise ValueError("runtime produced no result; inspect runtime.log")
    result = read_json(directory / "result.json")
    if watch_pause and (result.get("execution_id") != execution_id or result.get("status") == "RUNNING"):
        result = {"schemaVersion": 1, "run_id": contract["task"]["id"],
                  "status": "HARNESS-ERROR", "stop_reason": "missing_fresh_result", "verified": False,
                  "elapsed_seconds": previous.get("elapsed_seconds", 0) + time.monotonic() - started}
        if verification_only:
            result.update(execution_id=execution_id, retry_allowed=False, resume_allowed=False,
                          external_process_stop="unconfirmed", cleanup_budget_seconds=12)
        write_json(directory / "result.json", result)
    elif watch_pause:
        # Charge startup/import and cleanup time too, not just the child's
        # inner loop, so repeated windows cannot evade the wall budget.
        result["elapsed_seconds"] = round(max(
            result.get("elapsed_seconds", 0),
            previous.get("elapsed_seconds", 0) + time.monotonic() - started,
        ), 3)
        write_json(directory / "result.json", result)
    return result


def _child(root: Path, run_id: str, execution_id: str = "", parent_fd: int | None = None,
           lock_fds: tuple[int, ...] = (), *, authorize_verification=False) -> None:
    """Internal supervised entry; deliberately absent from the public CLI."""
    for descriptor in lock_fds:
        # Keep the lock in this runtime, not in arbitrary CLI/test execs.
        os.set_inheritable(descriptor, False)
    if parent_fd is not None:
        _watch_parent(parent_fd)
    directory = run_directory(root, run_id)
    contract = read_json(directory / "contract.json")
    current = load_contract(root, contract["task"], read_json(root / ".prax/harness.json"))
    if current != contract:
        raise ValueError("repository profile changed; use a new task ID")
    if contract["task"]["kind"] == "verification":
        result = execute(root, directory, contract, execution_id=execution_id,
                         authorize_verification=authorize_verification)
    else:
        result = execute(root, directory, contract)
    if execution_id:
        write_json(directory / "result.json", {**result, "execution_id": execution_id})


def run_contract(root: Path, contract: dict, *, resume_existing=False, explicit_resume=False,
                 authorize_verification=False) -> dict:
    """Shared Shift/harness entry: freeze, lock, validate, supervise, and report."""
    root = root.resolve()
    contract = load_contract(root, contract["task"], contract["profile"])
    verification_only = contract["task"]["kind"] == "verification"
    if verification_only:
        if resume_existing or explicit_resume:
            raise ValueError("verification is single-use and cannot resume; use a new authorized task ID")
        if authorize_verification is not True:
            raise ValueError("verification requires explicit --authorize-verification for this invocation")
    directory = run_directory(root, contract["task"]["id"])
    with run_lock(directory) as run_fd, repository_locks(root, contract["task"]) as repository_fds:
        if verification_only and (directory / "verification-dispatch.json").exists():
            raise ValueError("verification was already dispatched; inspect evidence and use a new authorized task ID")
        frozen = directory / "contract.json"
        if frozen.exists():
            if not resume_existing:
                raise ValueError("run ID already exists; use resume or a new task ID")
            if read_json(frozen) != contract:
                raise ValueError("task contract changed; use a new task ID")
        else:
            if explicit_resume:
                raise ValueError("cannot resume a missing run")
            write_json(frozen, contract)
        identity_path = directory / "identity.json"
        current = snapshot(root, contract["task"])
        fingerprint = control_fingerprint(root, contract)
        if identity_path.exists():
            identity = read_json(identity_path)
            if identity["fingerprint"] != fingerprint:
                raise ValueError("task, verifier, or repository policy changed; use a new task ID")
            guard(identity["snapshot"], current, contract["task"], resume=True)
        else:
            if (directory / "result.json").exists() or RunJournal(str(directory), "worker").path.exists():
                raise ValueError("run identity is missing; cannot adopt existing evidence")
            write_json(identity_path, {"fingerprint": fingerprint, "snapshot": current})
        previous = read_json(directory / "result.json") if (directory / "result.json").exists() else {}
        if previous.get("status") == "RUNNING":
            raise ValueError("previous supervisor did not finish; inspect its process and evidence before handoff")
        if previous.get("status") == "PASS" and previous.get("verified") is True:
            return previous
        if pause_requested(directory):
            if not explicit_resume:
                return {**previous, "status": "PAUSED", "stop_reason": "paused",
                        "run_id": contract["task"]["id"], "verified": False}
            (directory / "pause.json").unlink()
        return supervise(root, directory, contract, watch_pause=True,
                         lock_fds=(run_fd, *repository_fds),
                         **({"authorize_verification": True} if verification_only else {}))


def main(argv=None, *, prog="prax harness"):
    parser = argparse.ArgumentParser(
        prog=prog, description=__doc__,
        epilog='Legacy compatibility: prax shift "goal" --verify "command" (not contract-backed).',
    )
    parser.add_argument("action", choices=["plan", "run", "resume", "status", "pause"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--task", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--resume-existing", action="store_true",
                        help="continue the exact task if it exists; never unpause or renew its budget")
    parser.add_argument("--authorize-verification", action="store_true",
                        help="explicitly authorize this invocation of a single-use verification task")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.resume_existing and args.action != "run":
            raise ValueError("--resume-existing is only valid for run")
        if args.authorize_verification and args.action != "run":
            raise ValueError("--authorize-verification is only valid for run")
        if args.action in {"plan", "run"}:
            if args.task is None or args.run_id is not None:
                raise ValueError("plan/run requires --task; task.id owns the run ID")
            profile = read_json(root / ".prax/harness.json")
            task_path = args.task if args.task.is_absolute() else root / args.task
            contract = load_contract(root, read_json(task_path), profile)
            run_id = contract["task"]["id"]
            if args.action == "plan":
                print(json.dumps({"contract": contract, "repositories": snapshot(root, contract["task"]),
                                  "fingerprint": control_fingerprint(root, contract),
                                  "execution": "not-started"}, ensure_ascii=False, indent=2))
                return 0
        else:
            run_id = args.run_id or ""
            if args.task is not None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
                raise ValueError("resume/status/pause requires a valid --run-id and no --task")
        directory = run_directory(root, run_id)
        if args.action == "status":
            result = read_json(directory / "result.json")
            if pause_requested(directory) and not result.get("verified"):
                result = {**result, "pause_requested": True}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.action == "pause":
            read_json(directory / "contract.json")
            write_json(directory / "pause.json", {"requested_at": time.time()})
            print(json.dumps({"status": "PAUSE-REQUESTED", "run_id": run_id, "verified": False}))
            return 0
        if args.action == "resume":
            contract = read_json(directory / "contract.json")
            current = load_contract(root, contract["task"], read_json(root / ".prax/harness.json"))
            if current != contract:
                raise ValueError("repository profile changed; use a new task ID")
        result = run_contract(root, contract,
                              resume_existing=args.resume_existing or args.action == "resume",
                              explicit_resume=args.action == "resume",
                              authorize_verification=args.authorize_verification)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return outcome_exit_code(result)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "HARNESS-ERROR", "verified": False, "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
