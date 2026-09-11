"""Offline subprocess checks: exit zero is insufficient when every test skips."""
from __future__ import annotations

import sys

import pytest

from prax.acceptance_evidence import non_execution_reason
from prax.repository_harness import command


@pytest.fixture(autouse=True)
def isolated_pytest(monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)


def run(tmp_path, runner, *arguments):
    return command([sys.executable, "-B", "-m", runner, *arguments],
                   tmp_path, 20, tmp_path / "acceptance.log")


@pytest.mark.parametrize("case,expected", [
    ("skip", "HARNESS-ERROR"), ("pass", "PASS"), ("mixed", "PASS"),
])
@pytest.mark.parametrize("quiet", [[], ["-q"]])
def test_real_unittest_execution_evidence(tmp_path, case, expected, quiet):
    source = "import unittest\nclass Checks(unittest.TestCase):\n"
    if case in {"skip", "mixed"}:
        source += "    @unittest.skip('offline skip')\n    def test_skip(self):\n        self.fail('must not run')\n"
    if case in {"pass", "mixed"}:
        source += "    def test_pass(self):\n        self.assertEqual(2 + 2, 4)\n"
    (tmp_path / "test_acceptance.py").write_text(source, encoding="utf-8")
    result = run(tmp_path, "unittest", "test_acceptance", *quiet)
    # Recent Python versions can use exit 5 for an entirely skipped suite.
    assert result["returncode"] in ({0, 5} if case == "skip" else {0})
    assert result["status"] == expected
    if case == "skip":
        assert "skipped=1" in result["output"]
        assert "evidence_error" in result


@pytest.mark.parametrize("case,expected", [
    ("skip", "HARNESS-ERROR"), ("pass", "PASS"), ("mixed", "PASS"),
])
@pytest.mark.parametrize("quiet", [[], ["-q"], ["-qq"]])
def test_real_pytest_execution_evidence(tmp_path, case, expected, quiet):
    source = "import pytest\n"
    if case in {"skip", "mixed"}:
        source += "@pytest.mark.skip(reason='offline skip')\ndef test_skip():\n    assert False\n"
    if case in {"pass", "mixed"}:
        source += "def test_pass():\n    assert 2 + 2 == 4\n"
    (tmp_path / "test_acceptance.py").write_text(source, encoding="utf-8")
    result = run(tmp_path, "pytest", "test_acceptance.py", *quiet)
    assert result["returncode"] == 0
    assert result["status"] == expected


@pytest.mark.parametrize("option", ["--collect-only", "--co", "--setup-plan", "--fixtures-per-test", "--help", "--version"])
def test_real_pytest_non_executing_modes_are_not_acceptance(tmp_path, option):
    (tmp_path / "test_acceptance.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    result = run(tmp_path, "pytest", "test_acceptance.py", option, "-q")
    assert result["returncode"] == 0
    assert result["status"] == "HARNESS-ERROR"


def test_pytest_environment_collect_only_is_not_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    (tmp_path / "test_acceptance.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    result = run(tmp_path, "pytest", "test_acceptance.py", "-q")
    assert result["returncode"] == 0
    assert result["status"] == "HARNESS-ERROR"


def test_pytest_xfail_without_execution_is_not_acceptance(tmp_path):
    (tmp_path / "test_acceptance.py").write_text(
        "import pytest\n@pytest.mark.xfail(run=False)\ndef test_unused():\n    assert False\n", encoding="utf-8")
    result = run(tmp_path, "pytest", "test_acceptance.py", "-q")
    assert result["returncode"] == 0
    assert result["status"] == "HARNESS-ERROR"


def test_real_unittest_help_is_not_acceptance(tmp_path):
    result = run(tmp_path, "unittest", "--help")
    assert result["returncode"] == 0
    assert result["status"] == "HARNESS-ERROR"


def test_arbitrary_trusted_script_keeps_exit_status_semantics(tmp_path):
    result = command([sys.executable, "-c", "print('1 skipped in 0.01s')"],
                     tmp_path, 10, tmp_path / "script.log")
    assert result["status"] == "PASS"
    assert "evidence_error" not in result


@pytest.mark.parametrize("text", [
    "Ran 1 test in 0.001s\n\nOK (skipped=1)\n",
    "Ran 2 tests in 0.001s\n\nOK (skipped=2)\n",
])
def test_unittest_exit_zero_all_skip_report(text):
    assert non_execution_reason("unittest", ["python", "-m", "unittest"], text)


def test_ansi_pytest_summary_is_recognized():
    assert non_execution_reason("pytest", ["pytest"], "\x1b[33m1 skipped\x1b[0m in 0.01s\n")


def test_unknown_custom_report_is_not_guessed_to_be_empty():
    assert non_execution_reason("pytest", ["pytest"], "custom verification succeeded\n") is None


def test_pytest_passing_summary_outranks_incidental_progress_like_output():
    assert non_execution_reason("pytest", ["pytest", "-s"], "s\n1 passed in 0.01s\n") is None


def test_node_all_skip_report_is_not_acceptance():
    assert non_execution_reason("node-test", ["node", "--test"], "# tests 2\n# pass 0\n# fail 0\n# skipped 2\n")


def test_node_mixed_skip_and_pass_remains_valid():
    assert non_execution_reason("node-test", ["node", "--test"], "# tests 2\n# pass 1\n# fail 0\n# skipped 1\n") is None


@pytest.mark.parametrize("report,empty", [
    ("     Tests  2 skipped (2)\n", True),
    ("     Tests  1 skipped | 1 todo (2)\n", True),
    ("     Tests  1 passed | 1 skipped (2)\n", False),
])
def test_vitest_standard_summary(report, empty):
    assert bool(non_execution_reason("vitest", ["pnpm", "exec", "vitest", "run"], report)) is empty
