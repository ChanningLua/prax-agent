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

import shutil
import subprocess
import sys
from typing import Callable

from ..tools.verify_command import parse_verify_command
from .orchestrator_loop import VerifyResult

# (argv, cwd, timeout) -> (returncode, combined_output)
Runner = Callable[[list[str], str, int], "tuple[int, str]"]

_MAX_OUTPUT_CHARS = 4000


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
        argv = parse_verify_command(command)  # raises ValueError if not allowed
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
