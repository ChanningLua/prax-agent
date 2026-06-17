"""Unit tests for prax/core/command_verifier.py.

The subprocess runner is injectable so the verifier's real logic (allowlist
enforcement, exit-code mapping, timeout handling, output formatting, and the
loop Verifier contract) is exercised without spawning a nested pytest.
"""
from __future__ import annotations

import subprocess

import pytest

from prax.core.command_verifier import CommandVerifier
from prax.core.orchestrator_loop import OrchestratorLoop, StepResult
from prax.core.run_journal import RunJournal


def _runner(rc: int, output: str):
    def _r(argv, cwd, timeout):
        return rc, output
    return _r


class TestCommandVerifier:
    def test_rejects_disallowed_command(self, tmp_path):
        # reuses prax's verify allowlist — arbitrary shell is rejected up front
        with pytest.raises(ValueError):
            CommandVerifier("rm -rf /", cwd=str(tmp_path))

    def test_passing_command_maps_to_passed(self, tmp_path):
        v = CommandVerifier("pytest -q", cwd=str(tmp_path), runner=_runner(0, "5 passed"))
        result = v()
        assert result.passed is True
        assert "5 passed" in result.output

    def test_failing_command_maps_to_failed_with_exit_code(self, tmp_path):
        v = CommandVerifier("pytest -q", cwd=str(tmp_path), runner=_runner(1, "1 failed"))
        result = v()
        assert result.passed is False
        assert "1 failed" in result.output
        assert "Exit code: 1" in result.output

    def test_timeout_maps_to_failed(self, tmp_path):
        def _timeout_runner(argv, cwd, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        v = CommandVerifier("pytest -q", cwd=str(tmp_path), timeout=7, runner=_timeout_runner)
        result = v()
        assert result.passed is False
        assert "timed out" in result.output

    def test_satisfies_loop_verifier_contract(self, tmp_path):
        # fail once then pass — must drive OrchestratorLoop's self-heal path
        calls = {"n": 0}

        def _flaky(argv, cwd, timeout):
            calls["n"] += 1
            return (1, "boom") if calls["n"] == 1 else (0, "ok")

        class _Exec:
            def __init__(self) -> None:
                self.instructions: list[str] = []

            def run(self, instruction, *, session_id=None):
                self.instructions.append(instruction)
                return StepResult(text="ran", session_id="s")

        ex = _Exec()
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "rv"),
            executor=ex,
            verifier=CommandVerifier("pytest -q", cwd=str(tmp_path), runner=_flaky),
            max_iterations=5,
        )
        outcome = loop.run("目标")

        assert outcome.verified is True
        assert outcome.iterations == 2
        assert "boom" in ex.instructions[1]  # verifier failure fed back into next step
