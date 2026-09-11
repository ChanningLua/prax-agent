"""Recognize explicit no-execution reports from supported test runners.

These checks supplement a trusted verifier's exit status; they are not a
general parser for every test reporter and do not apply to arbitrary scripts.
Unknown/custom output keeps its existing exit-status semantics. In particular,
absence of a familiar summary alone is not evidence that no test ran.
"""
from __future__ import annotations

import re


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PYTEST_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b")
_PYTEST_SUMMARY = re.compile(
    r"^\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)"
    r"(?:, \d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?))*"
    r"(?: in \d+(?:\.\d+)?s(?: \([^\n]*\))?)?$"
)
_PYTEST_NO_RUN_FLAGS = frozenset({
    "--collect-only", "--co", "--setup-plan", "--fixtures", "--funcargs",
    "--fixtures-per-test", "--markers", "--help", "-h", "--version", "-V",
})


def _options(argv: list[str]) -> list[str]:
    """Do not mistake a positional path after ``--`` for a runner option."""
    return argv[:argv.index("--")] if "--" in argv else argv


def non_execution_reason(runner: str | None, argv: list[str], output: str) -> str | None:
    """Explain a known non-executing test invocation, otherwise return None."""
    text = _ANSI.sub("", output).replace("\r", "\n")
    options = _options(argv)
    if runner == "unittest":
        if {"-h", "--help"}.intersection(options):
            return "unittest help does not execute acceptance tests"
        totals = re.findall(r"(?m)^Ran (\d+) tests? in [^\n]+$", text)
        if totals:
            total = int(totals[-1])
            summaries = re.findall(r"(?m)^OK(?: \(([^\n]*)\))?\s*$", text)
            skipped = re.search(r"\bskipped=(\d+)\b", summaries[-1]) if summaries else None
            if total == 0 or (skipped and int(skipped.group(1)) >= total):
                return "unittest reported no confirmed non-skipped acceptance tests"

    elif runner == "pytest":
        if _PYTEST_NO_RUN_FLAGS.intersection(options):
            return "pytest inspection/collection mode does not execute acceptance tests"
        summaries = []
        for line in text.splitlines():
            normalized = line.strip().strip("=").strip()
            if _PYTEST_SUMMARY.fullmatch(normalized):
                summaries.append({name: int(count) for count, name in _PYTEST_COUNT.findall(normalized)})
        if summaries:
            counts = summaries[-1]
            # An all-xfailed suite can include xfail(run=False), which never
            # executes the test body. It cannot establish passing acceptance.
            if (not any(counts.get(name, 0) for name in ("passed", "failed", "error", "errors", "xpassed"))
                    and any(counts.get(name, 0) for name in ("skipped", "deselected", "xfailed"))):
                return "pytest reported only skipped, deselected, or expected-failure tests"
        if not summaries and re.search(
            r"(?m)^(?:=+\s*)?(?:\d+ tests? collected|no tests collected|no tests ran)\b", text
        ):
            return "pytest collected tests without executing acceptance"
        # -qq suppresses the summary but retains the terse progress report.
        progress = re.findall(r"(?m)^([.sFxXE]+)\s*(?:\[\s*\d+%\])?\s*$", text)
        if not summaries and progress and set("".join(progress)) <= {"s", "x"}:
            return "pytest progress contains only skipped or expected-failure tests"

    elif runner == "node-test":
        if {"--help", "-h", "--version", "-v"}.intersection(options):
            return "node inspection mode does not execute acceptance tests"
        counts = {name: int(count) for name, count in re.findall(
            r"(?m)^(?:#|ℹ) (tests|pass|fail|skipped|todo) (\d+)\s*$", text
        )}
        if "tests" in counts and counts.get("skipped", 0) + counts.get("todo", 0) >= counts["tests"]:
            return "node test runner executed no non-skipped acceptance tests"

    elif runner == "vitest":
        if {"--help", "-h", "--version", "-v"}.intersection(options):
            return "vitest inspection mode does not execute acceptance tests"
        summaries = re.findall(r"(?m)^\s*Tests\s+([^\n]+?)\s*\((\d+)\)\s*$", text)
        if summaries:
            values, total = summaries[-1]
            counts = {name: int(count) for count, name in re.findall(
                r"(\d+)\s+(passed|failed|skipped|todo)\b", values
            )}
            if counts.get("skipped", 0) + counts.get("todo", 0) >= int(total):
                return "vitest reported no non-skipped acceptance tests"

    return None
