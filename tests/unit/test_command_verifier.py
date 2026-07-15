"""Unit tests for prax/core/command_verifier.py.

The subprocess runner is injectable so the verifier's real logic (allowlist
enforcement, exit-code mapping, timeout handling, output formatting, and the
loop Verifier contract) is exercised without spawning a nested pytest.
"""
from __future__ import annotations

import subprocess

import pytest

from prax.core.command_verifier import CommandVerifier, classify_verify_status
from prax.core.orchestrator_loop import (
    VERIFY_FEATURE_FAIL,
    VERIFY_HARNESS_ERROR,
    VERIFY_PASS,
    OrchestratorLoop,
    StepResult,
)
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

    def test_accepts_npm_run_build(self, tmp_path):
        # G1: build/lint scripts must be allowed, not just `npm test`
        v = CommandVerifier("npm run build", cwd=str(tmp_path), runner=_runner(0, "built"))
        assert v().passed is True

    def test_passing_command_maps_to_passed(self, tmp_path):
        v = CommandVerifier("pytest -q", cwd=str(tmp_path), runner=_runner(0, "5 passed"))
        result = v()
        assert result.passed is True
        assert result.status == VERIFY_PASS
        assert "5 passed" in result.output

    def test_failing_command_maps_to_failed_with_exit_code(self, tmp_path):
        v = CommandVerifier("pytest -q", cwd=str(tmp_path), runner=_runner(1, "1 failed"))
        result = v()
        assert result.passed is False
        assert result.status == VERIFY_FEATURE_FAIL
        assert "1 failed" in result.output
        assert "Exit code: 1" in result.output

    def test_timeout_maps_to_failed(self, tmp_path):
        def _timeout_runner(argv, cwd, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        v = CommandVerifier("pytest -q", cwd=str(tmp_path), timeout=7, runner=_timeout_runner)
        result = v()
        assert result.passed is False
        assert result.status == VERIFY_HARNESS_ERROR
        assert "timed out" in result.output

    def test_explicit_harness_marker_is_not_a_feature_failure(self, tmp_path):
        v = CommandVerifier(
            "pytest -q",
            cwd=str(tmp_path),
            runner=_runner(1, "HARNESS-ERROR: backend is not reachable"),
        )
        result = v()
        assert result.passed is False
        assert result.status == VERIFY_HARNESS_ERROR

    def test_exec_level_failure_is_harness_error(self, tmp_path):
        assert classify_verify_status(127, "command not found") == VERIFY_HARNESS_ERROR
        assert classify_verify_status(-9, "killed") == VERIFY_HARNESS_ERROR

    def test_plain_language_harness_error_is_not_an_explicit_marker(self):
        assert classify_verify_status(
            1, "assertion: harness error label should be visible"
        ) == VERIFY_FEATURE_FAIL
        assert classify_verify_status(
            1, "assert expected 'HARNESS-ERROR' in rendered documentation"
        ) == VERIFY_FEATURE_FAIL

    def test_explicit_marker_overrides_zero_exit(self):
        assert classify_verify_status(0, "HARNESS_ERROR: bootstrap incomplete") == (
            VERIFY_HARNESS_ERROR
        )

    def test_marker_before_long_output_is_classified_before_truncation(self, tmp_path):
        v = CommandVerifier(
            "pytest -q",
            cwd=str(tmp_path),
            runner=_runner(1, "HARNESS-ERROR: bootstrap failed\n" + "x" * 5000),
        )
        assert v().status == VERIFY_HARNESS_ERROR

    def test_os_error_from_runner_is_harness_error(self, tmp_path):
        def _missing(argv, cwd, timeout):
            raise FileNotFoundError("pytest")

        result = CommandVerifier("pytest -q", cwd=str(tmp_path), runner=_missing)()
        assert result.status == VERIFY_HARNESS_ERROR
        assert "harness failed" in result.output

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


class TestRepoScriptEscapeHatch:
    """Operator escape hatch: a repo-local ./script for non-test-runner checks."""

    def _make_script(self, tmp_path, rel="scripts/verify.sh", executable=True):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        if executable:
            p.chmod(0o755)
        return p

    def test_accepts_executable_repo_script(self, tmp_path):
        self._make_script(tmp_path)
        seen = {}

        def _r(argv, cwd, timeout):
            seen["argv"] = argv
            return 0, "ok"

        v = CommandVerifier("./scripts/verify.sh --quick", cwd=str(tmp_path), runner=_r)
        assert v().passed is True
        # resolved to an ABSOLUTE path inside the repo + extra arg preserved
        assert seen["argv"][0] == str((tmp_path / "scripts/verify.sh").resolve())
        assert seen["argv"][1] == "--quick"

    def test_rejects_absolute_path(self, tmp_path):
        with pytest.raises(ValueError):
            CommandVerifier("/etc/evil.sh", cwd=str(tmp_path))

    def test_rejects_parent_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="without '\\.\\.'|inside the repo"):
            CommandVerifier("./../escape.sh", cwd=str(tmp_path))

    def test_rejects_missing_script(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            CommandVerifier("./scripts/nope.sh", cwd=str(tmp_path))

    def test_rejects_non_executable_script(self, tmp_path):
        self._make_script(tmp_path, executable=False)
        with pytest.raises(ValueError, match="not executable"):
            CommandVerifier("./scripts/verify.sh", cwd=str(tmp_path))

    def test_rejects_shell_composition_in_script_command(self, tmp_path):
        self._make_script(tmp_path)
        with pytest.raises(ValueError, match="Shell composition"):
            CommandVerifier("./scripts/verify.sh && rm -rf /", cwd=str(tmp_path))

    def test_non_dotslash_typo_still_raises_allowlist_error(self, tmp_path):
        # a plain unsupported program keeps the helpful allowlist message,
        # not re-routed into script resolution
        with pytest.raises(ValueError, match="Unsupported verification command"):
            CommandVerifier("make test", cwd=str(tmp_path))
