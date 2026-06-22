"""Command verifier — adapts a bounded verify command into a loop ``Verifier``.

OrchestratorLoop takes a sync ``verifier() -> VerifyResult``. This adapter runs
a repository-local verification command (``pytest -q``, ``npm test``, ...),
reusing prax's existing allowlist (:func:`parse_verify_command`) so the verifier
can never run arbitrary shell, and maps the exit code to a VerifyResult:
**non-zero exit == failure** (borrowed from Aider's "exit code is the signal").

The subprocess runner is injectable so the mapping logic is unit-testable
without spawning a real (nested) test process.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..tools.verify_command import parse_verify_command
from .orchestrator_loop import VerifyResult

# (argv, cwd, timeout) -> (returncode, combined_output)
Runner = Callable[[list[str], str, int], "tuple[int, str]"]

_MAX_OUTPUT_CHARS = 4000

# Shell-composition tokens never allowed, even in the script escape hatch — the
# verifier always runs via execve (no shell), so these could only be a mistake
# or an injection attempt.
_SHELL_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "`"}


def resolve_repo_script(command: str, cwd: str) -> list[str]:
    """Operator escape hatch: a repo-local verify script run WITHOUT a shell.

    Scoped to the orchestrate ``--verify`` path (operator-set, never the agent):
    this is intentionally NOT wired into ``parse_verify_command`` /
    ``is_verify_command``, because those also classify agent (SandboxBash) calls
    as "safe verify" for risk scoring + the completion gate — broadening them
    would let the agent self-classify an arbitrary ``./script.sh`` as low-risk.

    Accepts a command whose program is an *explicitly relative* ``./`` path that:
    resolves INSIDE ``cwd`` (no ``..`` escape, not absolute), exists, is a
    regular file, and is executable. Extra args are allowed but may not contain
    shell-composition tokens. Returns the argv (absolute script path + args) to
    exec directly. Raises ``ValueError`` otherwise.
    """
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Invalid command syntax: {exc}") from exc
    if not parts:
        raise ValueError("Verification command must not be empty")
    if any(tok in _SHELL_TOKENS for tok in parts):
        raise ValueError("Shell composition is not allowed in a verify script command")

    script, extra = parts[0], parts[1:]
    rel = Path(script)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Verify script must be a relative repo path without '..'")

    root = Path(cwd).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Verify script must resolve inside the repository")
    if not target.is_file():
        raise ValueError(f"Verify script not found: {script}")
    if not os.access(target, os.X_OK):
        raise ValueError(f"Verify script is not executable (chmod +x {script})")

    return [str(target), *extra]


def _default_runner(argv: list[str], cwd: str, timeout: int) -> "tuple[int, str]":
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    out = proc.stdout or ""
    if proc.stderr:
        out = (out + ("\n" if out else "") + proc.stderr)
    return proc.returncode, out.strip()


class CommandVerifier:
    """Sync ``verifier()`` for :class:`OrchestratorLoop`.

    Validates *command* against prax's verify allowlist on construction (raises
    ``ValueError`` if it isn't an allowed pytest/npm/pnpm/cargo/go check), then
    runs it on each call and maps the result to a ``VerifyResult``.
    """

    def __init__(
        self,
        command: str,
        *,
        cwd: str,
        timeout: int = 120,
        runner: Runner | None = None,
    ) -> None:
        try:
            argv = parse_verify_command(command)  # strict test-runner allowlist
        except ValueError:
            # Operator escape hatch (only on an explicit ./path): a repo-local
            # verify script for acceptance checks the allowlist can't express
            # (e2e, curl probe, build-artifact check). Anything else re-raises
            # the original allowlist error.
            if command.strip().startswith("./"):
                argv = resolve_repo_script(command, cwd)
            else:
                raise
        # mirror VerifyCommandTool: fall back to `python -m pytest` if the
        # `pytest` console script isn't on PATH.
        if argv[0] == "pytest" and shutil.which("pytest") is None:
            argv = [sys.executable, "-m", "pytest", *argv[1:]]
        self._argv = argv
        self._cwd = cwd
        self._timeout = timeout
        self._runner = runner or _default_runner

    def __call__(self) -> VerifyResult:
        try:
            returncode, output = self._runner(self._argv, self._cwd, self._timeout)
        except subprocess.TimeoutExpired:
            return VerifyResult(
                passed=False,
                output=f"verification timed out after {self._timeout}s",
            )

        output = output or "(no output)"
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[-_MAX_OUTPUT_CHARS:]

        if returncode != 0:
            return VerifyResult(passed=False, output=f"{output}\nExit code: {returncode}")
        return VerifyResult(passed=True, output=output)
