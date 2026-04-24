"""YOLO Classifier — LLM-based risk assessment for Bash commands and tool calls.

Used in auto/YOLO permission mode to decide whether an operation is safe
to proceed without user confirmation.

Risk levels:
  LOW    — routine, reversible operations (ls, cat, grep, git status)
  MEDIUM — potentially impactful but scoped (npm install, git commit)
  HIGH   — destructive or wide-impact (rm -rf, git push --force, DROP TABLE)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class YoloDecision:
    risk: RiskLevel
    reason: str
    allow: bool  # True = auto-approve, False = require user confirmation


# Heuristic patterns for fast pre-classification without LLM call
_HIGH_RISK_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*)",  # rm -rf / rm -f
    r"\bdd\b",           # dd if=... of=...
    r"\b(?:mkfs|fdisk|parted)\b",  # disk operations
    r"\bchmod\s+777\b",  # world-writable
    r"\bchown\s+.*\s+/",  # chown root
    r">\s*/dev/sd",      # write to device
    r"\bdrop\s+(?:table|database)\b",  # SQL DROP
    r"git\s+push\s+.*--force",  # force push
    r"git\s+reset\s+--hard",    # hard reset
    r"\btruncate\b",     # truncate files/tables
    r":\(\)\{\s*:|:\s*&\s*\}",  # fork bomb
]

_LOW_RISK_PATTERNS = [
    r"^\s*(?:ls|pwd|cat|echo|head|tail|grep|find|wc|date|whoami|uname|env|printenv)\b",
    r"^\s*git\s+(?:status|log|diff|show|branch|remote\s+-v)\b",
    r"^\s*(?:python|python3|node|npm|pip)\s+(?:--version|-V)\b",
    r"^\s*which\b",
    r"^\s*curl\s+.*-I\b",  # HEAD request only
]


class YoloClassifier:
    """Classify tool calls for auto-approval in YOLO/auto permission mode.

    Fast path: regex heuristics for common patterns.
    Slow path: LLM call for ambiguous cases (optional, requires llm_client).
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        model_config: Any | None = None,
        use_llm_fallback: bool = True,
    ) -> None:
        self._llm_client = llm_client
        self._model_config = model_config
        self._use_llm_fallback = use_llm_fallback

    def classify_bash(self, command: str) -> YoloDecision:
        """Classify a Bash command using heuristics (sync, fast path)."""
        cmd_lower = command.lower().strip()

        # Check HIGH risk patterns first
        for pattern in _HIGH_RISK_PATTERNS:
            if re.search(pattern, cmd_lower, re.IGNORECASE):
                return YoloDecision(
                    risk=RiskLevel.HIGH,
                    reason=f"Matched high-risk pattern: {pattern}",
                    allow=False,
                )

        # Check LOW risk patterns
        for pattern in _LOW_RISK_PATTERNS:
            if re.match(pattern, cmd_lower, re.IGNORECASE):
                return YoloDecision(
                    risk=RiskLevel.LOW,
                    reason="Matched low-risk pattern (read-only/informational)",
                    allow=True,
                )

        # Default MEDIUM — require confirmation unless LLM says otherwise
        return YoloDecision(
            risk=RiskLevel.MEDIUM,
            reason="Unrecognized command pattern — defaulting to MEDIUM risk",
            allow=False,
        )

    async def classify_bash_async(self, command: str) -> YoloDecision:
        """Classify a Bash command, with optional LLM fallback for MEDIUM cases."""
        decision = self.classify_bash(command)

        if decision.risk == RiskLevel.HIGH:
            return decision

        if decision.risk == RiskLevel.LOW:
            return decision

        # MEDIUM — try LLM if available
        if self._use_llm_fallback and self._llm_client is not None:
            try:
                llm_decision = await self._llm_classify(command)
                return llm_decision
            except Exception as exc:
                logger.warning("LLM classification failed, using heuristic: %s", exc)

        return decision

    async def classify_tool_call(self, tool_name: str, tool_input: dict) -> YoloDecision:
        """Classify an arbitrary tool call."""
        # Map tool names to risk levels
        safe_tools = {"Read", "Glob", "Grep", "HashlineRead", "AstGrepSearch", "WebSearch"}
        write_tools = {"Write", "Edit", "HashlineEdit", "AstGrepReplace"}
        dangerous_tools = {"Bash", "InteractiveBash", "SandboxBash"}

        if tool_name in safe_tools:
            return YoloDecision(
                risk=RiskLevel.LOW,
                reason=f"Tool '{tool_name}' is read-only",
                allow=True,
            )
        if tool_name in dangerous_tools:
            command = tool_input.get("command", tool_input.get("tmux_command", ""))
            if command:
                return await self.classify_bash_async(str(command))
            return YoloDecision(
                risk=RiskLevel.MEDIUM,
                reason=f"Tool '{tool_name}' with no command specified",
                allow=False,
            )
        if tool_name in write_tools:
            return YoloDecision(
                risk=RiskLevel.MEDIUM,
                reason=f"Tool '{tool_name}' modifies files",
                allow=True,  # Write/Edit within workspace is generally acceptable
            )
        # Unknown tool
        return YoloDecision(
            risk=RiskLevel.MEDIUM,
            reason=f"Unknown tool '{tool_name}' — conservative classification",
            allow=False,
        )

    async def _llm_classify(self, command: str) -> YoloDecision:
        """Use LLM to classify a Bash command."""
        prompt = f"""Classify the risk level of this bash command for autonomous execution.

Command: {command}

Respond with exactly one of: LOW, MEDIUM, HIGH
Then a single sentence reason.

Format:
RISK: <level>
REASON: <reason>

Rules:
- LOW: read-only, informational, safe to run without confirmation
- MEDIUM: modifies files within the workspace, reversible
- HIGH: destructive, irreversible, or affects shared systems"""

        response = await self._llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            model_config=self._model_config,
            system_prompt="You are a security classifier. Be conservative.",
        )

        text = response.text.strip()
        risk = RiskLevel.MEDIUM
        reason = "LLM classification"

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("RISK:"):
                level_str = line.split(":", 1)[1].strip().upper()
                try:
                    risk = RiskLevel(level_str)
                except ValueError:
                    pass
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()

        allow = risk in (RiskLevel.LOW, RiskLevel.MEDIUM)
        return YoloDecision(risk=risk, reason=reason, allow=allow)
