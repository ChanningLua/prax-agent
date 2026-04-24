"""Structured error recovery — classify errors and compute recovery strategies.

Replaces Ralph's naive retry+sleep with intelligent recovery:
- Classify errors by type (tool, model, timeout, permission, resource)
- Each error type has a specific recovery strategy
- Strategies compose: tool errors → switch approach, model errors → upgrade,
  timeouts → reduce scope, permission → skip or escalate
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ErrorType(str, Enum):
    """Classification of errors encountered during agent execution."""
    TOOL_ERROR = "tool_error"           # Tool execution failed
    MODEL_ERROR = "model_error"         # LLM API failure (rate limit, server error)
    TIMEOUT = "timeout"                 # Operation timed out
    PERMISSION_DENIED = "permission"    # Permission guard blocked the tool
    RESOURCE_EXHAUSTED = "resource"     # Budget, iteration limit, context overflow
    PARSE_ERROR = "parse_error"         # LLM returned unparseable output
    UNKNOWN = "unknown"                 # Unclassifiable


class RecoveryAction(str, Enum):
    """Actions the recovery system can recommend."""
    RETRY_SAME = "retry_same"           # Simple retry, same parameters
    SWITCH_TOOL = "switch_tool"         # Try alternative tool/approach
    UPGRADE_MODEL = "upgrade_model"     # Switch to a more capable model
    REDUCE_SCOPE = "reduce_scope"       # Simplify the task (fewer todos, smaller batch)
    SKIP_ITEM = "skip_item"             # Mark current todo as cancelled, move on
    WAIT_AND_RETRY = "wait_and_retry"   # Backoff then retry (rate limiting)
    ABORT = "abort"                     # Give up, no recovery possible


@dataclass
class ErrorClassification:
    """Result of classifying an error."""
    error_type: ErrorType
    message: str
    source: str = ""          # tool name, model name, etc.
    is_transient: bool = False  # True for rate limits, network blips
    raw_exception: Exception | None = None


@dataclass
class RecoveryStrategy:
    """Recommended recovery strategy for an error."""
    action: RecoveryAction
    reason: str
    delay_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    # For REDUCE_SCOPE: suggested max items per iteration
    suggested_batch_size: int | None = None
    # For UPGRADE_MODEL: suggested model name
    suggested_model: str | None = None
    # For SWITCH_TOOL: hint for the LLM about what to try instead
    tool_hint: str | None = None


def classify_error(
    error: Exception | str,
    *,
    source: str = "",
    tool_name: str | None = None,
) -> ErrorClassification:
    """Classify an error into a structured ErrorType.

    Args:
        error: The exception or error message string.
        source: Context about where the error originated.
        tool_name: If the error came from a tool, the tool's name.

    Returns:
        ErrorClassification with type and metadata.
    """
    msg = str(error).lower()

    # Permission denied (from PermissionGuardMiddleware)
    if "permission denied" in msg:
        return ErrorClassification(
            error_type=ErrorType.PERMISSION_DENIED,
            message=str(error),
            source=tool_name or source,
        )

    # Timeout patterns
    if any(kw in msg for kw in ("timed out", "timeout", "deadline exceeded", "read timeout")):
        return ErrorClassification(
            error_type=ErrorType.TIMEOUT,
            message=str(error),
            source=source,
            is_transient=True,
        )

    # Rate limiting / quota
    if any(kw in msg for kw in ("rate limit", "429", "quota exceeded", "too many requests")):
        return ErrorClassification(
            error_type=ErrorType.MODEL_ERROR,
            message=str(error),
            source=source,
            is_transient=True,
        )

    # Model/API errors
    if any(kw in msg for kw in (
        "api error", "500", "502", "503", "service unavailable",
        "internal server error", "overloaded", "capacity",
    )):
        return ErrorClassification(
            error_type=ErrorType.MODEL_ERROR,
            message=str(error),
            source=source,
            is_transient=True,
        )

    # Context/resource exhaustion
    if any(kw in msg for kw in (
        "context length", "max tokens", "token limit",
        "budget", "max iterations", "context_length_exceeded",
    )):
        return ErrorClassification(
            error_type=ErrorType.RESOURCE_EXHAUSTED,
            message=str(error),
            source=source,
        )

    # Parse errors (LLM returned garbage)
    if any(kw in msg for kw in ("json", "parse", "decode", "invalid format", "expecting")):
        if isinstance(error, (ValueError, KeyError)):
            return ErrorClassification(
                error_type=ErrorType.PARSE_ERROR,
                message=str(error),
                source=source,
                is_transient=True,
            )

    # Tool-specific errors
    if tool_name or "executing" in msg:
        return ErrorClassification(
            error_type=ErrorType.TOOL_ERROR,
            message=str(error),
            source=tool_name or source,
        )

    # Model errors from httpx or runtime
    if any(kw in msg for kw in ("connection", "httpx", "ssl", "dns")):
        return ErrorClassification(
            error_type=ErrorType.MODEL_ERROR,
            message=str(error),
            source=source,
            is_transient=True,
        )

    return ErrorClassification(
        error_type=ErrorType.UNKNOWN,
        message=str(error),
        source=source,
        raw_exception=error if isinstance(error, Exception) else None,
    )


def compute_recovery(
    classification: ErrorClassification,
    *,
    retry_count: int = 0,
    max_retries: int = 3,
    available_models: list[str] | None = None,
    current_model: str | None = None,
) -> RecoveryStrategy:
    """Compute the best recovery strategy given an error classification.

    Args:
        classification: The classified error.
        retry_count: How many times we've already retried.
        max_retries: Maximum retry attempts before abort.
        available_models: Models available for upgrade.
        current_model: Currently active model.

    Returns:
        RecoveryStrategy with the recommended action.
    """
    if retry_count >= max_retries:
        return RecoveryStrategy(
            action=RecoveryAction.ABORT,
            reason=f"Max retries ({max_retries}) exhausted for {classification.error_type.value}",
        )

    et = classification.error_type

    if et == ErrorType.PERMISSION_DENIED:
        return RecoveryStrategy(
            action=RecoveryAction.SKIP_ITEM,
            reason="Tool requires higher permission level than current mode allows",
            tool_hint="Try an alternative approach that doesn't require elevated permissions",
        )

    if et == ErrorType.TIMEOUT:
        delay = min(2.0 * (2 ** retry_count), 30.0)  # exponential backoff, max 30s
        return RecoveryStrategy(
            action=RecoveryAction.REDUCE_SCOPE,
            reason="Operation timed out — reduce task complexity",
            delay_seconds=delay,
            suggested_batch_size=max(1, 3 - retry_count),
        )

    if et == ErrorType.MODEL_ERROR:
        if classification.is_transient and retry_count < 2:
            delay = min(1.0 * (2 ** retry_count), 15.0)
            return RecoveryStrategy(
                action=RecoveryAction.WAIT_AND_RETRY,
                reason=f"Transient model error, backoff retry #{retry_count + 1}",
                delay_seconds=delay,
            )
        # After transient retries fail, try upgrading model
        next_model = _find_upgrade_model(current_model, available_models)
        if next_model:
            return RecoveryStrategy(
                action=RecoveryAction.UPGRADE_MODEL,
                reason=f"Model error persists after {retry_count} retries, upgrading",
                suggested_model=next_model,
            )
        return RecoveryStrategy(
            action=RecoveryAction.ABORT,
            reason="Model error with no upgrade path available",
        )

    if et == ErrorType.RESOURCE_EXHAUSTED:
        return RecoveryStrategy(
            action=RecoveryAction.REDUCE_SCOPE,
            reason="Resource limit reached — reduce task scope",
            suggested_batch_size=max(1, 2 - retry_count),
        )

    if et == ErrorType.PARSE_ERROR:
        return RecoveryStrategy(
            action=RecoveryAction.RETRY_SAME,
            reason="LLM output parse failed — retry with same parameters",
            delay_seconds=0.5,
        )

    if et == ErrorType.TOOL_ERROR:
        return RecoveryStrategy(
            action=RecoveryAction.SWITCH_TOOL,
            reason=f"Tool '{classification.source}' failed — try alternative approach",
            tool_hint=f"The tool '{classification.source}' encountered an error. "
                      f"Try a different tool or approach to accomplish the same goal.",
        )

    # UNKNOWN
    if classification.is_transient:
        return RecoveryStrategy(
            action=RecoveryAction.RETRY_SAME,
            reason="Unknown transient error — simple retry",
            delay_seconds=1.0,
        )
    return RecoveryStrategy(
        action=RecoveryAction.SKIP_ITEM,
        reason="Unknown non-transient error — skip and continue",
    )


def _find_upgrade_model(
    current: str | None,
    available: list[str] | None,
) -> str | None:
    """Find the next model in the upgrade chain after current."""
    if not available or not current:
        return None
    try:
        idx = available.index(current)
        if idx + 1 < len(available):
            return available[idx + 1]
    except ValueError:
        # current not in available list; return first available as fallback
        return available[0] if available else None
    return None


@dataclass
class ErrorTracker:
    """Track errors across Ralph iterations for pattern detection.

    Detects recurring patterns and adjusts strategy accordingly:
    - Same tool failing repeatedly → permanent skip
    - Same error type accumulating → escalate recovery
    """

    _history: list[ErrorClassification] = field(default_factory=list)
    _tool_failures: dict[str, int] = field(default_factory=dict)
    _type_counts: dict[str, int] = field(default_factory=dict)

    def record(self, classification: ErrorClassification) -> None:
        """Record an error occurrence."""
        self._history.append(classification)
        self._type_counts[classification.error_type.value] = (
            self._type_counts.get(classification.error_type.value, 0) + 1
        )
        if classification.source:
            self._tool_failures[classification.source] = (
                self._tool_failures.get(classification.source, 0) + 1
            )

    def get_retry_count_for_type(self, error_type: ErrorType) -> int:
        """How many times this error type has occurred."""
        return self._type_counts.get(error_type.value, 0)

    def is_tool_blacklisted(self, tool_name: str, threshold: int = 3) -> bool:
        """Check if a tool has failed too many times."""
        return self._tool_failures.get(tool_name, 0) >= threshold

    def get_dominant_error_type(self) -> ErrorType | None:
        """Return the most frequent error type, if any."""
        if not self._type_counts:
            return None
        top = max(self._type_counts, key=lambda k: self._type_counts[k])
        return ErrorType(top)

    @property
    def total_errors(self) -> int:
        return len(self._history)

    def summary(self) -> dict[str, Any]:
        """Produce a summary for checkpoint metadata."""
        return {
            "total_errors": self.total_errors,
            "type_counts": dict(self._type_counts),
            "tool_failures": dict(self._tool_failures),
        }
