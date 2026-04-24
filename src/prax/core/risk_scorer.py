"""Risk scorer — 4-axis risk assessment for tool calls.

Axes:
  1. tool_risk        — inherent risk of the tool itself (1-5)
  2. file_sensitivity — sensitivity of the target file (1-5)
  3. impact_scope     — breadth of potential impact (1-5)
  4. reversibility    — how hard it is to undo (1-5)

Total score 4-20:
  LOW    (4-8)   — proceed silently
  MEDIUM (9-14)  — log warning
  HIGH   (15-20) — require explicit confirmation
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..tools.verify_command import is_verify_command


@dataclass
class RiskScore:
    tool_risk: int
    file_sensitivity: int
    impact_scope: int
    reversibility: int

    @property
    def total(self) -> int:
        return self.tool_risk + self.file_sensitivity + self.impact_scope + self.reversibility

    @property
    def level(self) -> str:
        t = self.total
        if t <= 8:
            return "LOW"
        elif t <= 14:
            return "MEDIUM"
        return "HIGH"

    def summary(self) -> str:
        return (
            f"Risk {self.total}/20 ({self.level}) — "
            f"tool={self.tool_risk} file={self.file_sensitivity} "
            f"scope={self.impact_scope} reversibility={self.reversibility}"
        )


# Base tool risk scores (1-5)
_TOOL_RISK: dict[str, int] = {
    # Read-only
    "HashlineRead": 1,
    "Read": 1,
    "Glob": 1,
    "Grep": 1,
    "AstGrepSearch": 1,
    "WebSearch": 1,
    "WebCrawler": 1,
    # Write / edit
    "Write": 3,
    "Edit": 3,
    "HashlineEdit": 3,
    "AstGrepReplace": 3,
    "TodoWrite": 2,
    # Shell / process
    "TmuxBash": 5,
    "Bash": 5,
    # Task delegation
    "Task": 4,
    "StartTask": 4,
    "CheckTask": 1,
    "UpdateTask": 2,
    "CancelTask": 3,
    "ListTasks": 1,
}

# File sensitivity patterns: (regex, score 1-5)
_FILE_PATTERNS: list[tuple[str, int]] = [
    # Secrets / credentials
    (r"\.env$", 5),
    (r"\.env\.", 5),
    (r"credentials", 5),
    (r"secrets?[\./]", 5),
    (r"\.key$", 5),
    (r"\.pem$", 5),
    (r"\.p12$", 5),
    (r"\.pfx$", 5),
    # CI/CD and infra
    (r"\.github/workflows/", 4),
    (r"Dockerfile", 4),
    (r"docker-compose", 4),
    (r"terraform", 4),
    (r"k8s/", 4),
    # Config files
    (r"config\.(yaml|yml|json|toml)$", 4),
    (r"settings\.(py|json|yaml)$", 4),
    (r"pyproject\.toml$", 4),
    (r"package\.json$", 4),
    (r"requirements.*\.txt$", 4),
    # Source code
    (r"src/", 2),
    (r"lib/", 2),
    (r"prax/core/", 3),
    # Tests / docs
    (r"test[s]?/", 1),
    (r"__tests__/", 1),
    (r"\.test\.", 1),
    (r"\.spec\.", 1),
    (r"README", 1),
    (r"\.md$", 1),
]

# Destructive shell patterns
_DESTRUCTIVE_CMDS = [
    r"\brm\s+-rf\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\s+--force\b",
    r"\bDROP\s+TABLE\b",
    r"\btruncate\b",
    r"\bformat\b",
    r"\bdd\s+if=",
    r"\bmkfs\b",
]

_PUBLISH_CMDS = [
    r"\bgit\s+push\b",
    r"\bnpm\s+publish\b",
    r"\bpip\s+upload\b",
    r"\btwine\s+upload\b",
    r"\bdocker\s+push\b",
]

_REVERSIBLE_CMDS = [
    r"\bgit\s+commit\b",
    r"\bnpm\s+install\b",
    r"\bpip\s+install\b",
    r"\bpoetry\s+add\b",
]


class RiskScorer:
    """Compute a 4-axis risk score for a tool call."""

    def score(self, tool_name: str, params: dict) -> RiskScore:
        return RiskScore(
            tool_risk=self._tool_risk(tool_name),
            file_sensitivity=self._file_sensitivity(params),
            impact_scope=self._impact_scope(tool_name, params),
            reversibility=self._reversibility(tool_name, params),
        )

    # ------------------------------------------------------------------
    def _tool_risk(self, tool_name: str) -> int:
        return _TOOL_RISK.get(tool_name, 3)

    def _file_sensitivity(self, params: dict) -> int:
        path = params.get("file_path") or params.get("path") or ""
        if not path:
            return 1
        for pattern, score in _FILE_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return score
        return 2

    def _impact_scope(self, tool_name: str, params: dict) -> int:
        if tool_name in ("TmuxBash", "Bash", "SandboxBash"):
            cmd = params.get("command", "")
            if isinstance(cmd, str) and is_verify_command(cmd):
                return 1
            if any(re.search(p, cmd, re.IGNORECASE) for p in _DESTRUCTIVE_CMDS):
                return 5
            if any(re.search(p, cmd, re.IGNORECASE) for p in _PUBLISH_CMDS):
                return 4
            if any(re.search(p, cmd, re.IGNORECASE) for p in _REVERSIBLE_CMDS):
                return 3
            return 2
        if tool_name in ("Task", "StartTask"):
            return 3
        if tool_name in ("Write", "Edit", "HashlineEdit", "AstGrepReplace"):
            return 1
        return 1

    def _reversibility(self, tool_name: str, params: dict) -> int:
        if tool_name in ("TmuxBash", "Bash", "SandboxBash"):
            cmd = params.get("command", "")
            if isinstance(cmd, str) and is_verify_command(cmd):
                return 1
            if any(re.search(p, cmd, re.IGNORECASE) for p in _DESTRUCTIVE_CMDS):
                return 5
            if any(re.search(p, cmd, re.IGNORECASE) for p in _PUBLISH_CMDS):
                return 4
            if any(re.search(p, cmd, re.IGNORECASE) for p in _REVERSIBLE_CMDS):
                return 2
            return 1
        # File edits are reversible via git
        if tool_name in ("Write", "Edit", "HashlineEdit", "AstGrepReplace"):
            return 1
        return 2
