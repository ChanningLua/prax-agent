"""Unit tests for error_recovery.py — classify_error, compute_recovery, ErrorTracker."""
from __future__ import annotations

import pytest

from prax.core.error_recovery import (
    ErrorClassification,
    ErrorTracker,
    ErrorType,
    RecoveryAction,
    classify_error,
    compute_recovery,
)


# ── classify_error ────────────────────────────────────────────────────────────

class TestClassifyError:
    def test_permission_denied(self):
        c = classify_error(Exception("permission denied"))
        assert c.error_type == ErrorType.PERMISSION_DENIED

    def test_timeout_keywords(self):
        for msg in ("timed out", "timeout", "deadline exceeded", "read timeout"):
            c = classify_error(Exception(msg))
            assert c.error_type == ErrorType.TIMEOUT
            assert c.is_transient is True

    def test_rate_limit(self):
        for msg in ("rate limit exceeded", "429", "quota exceeded", "too many requests"):
            c = classify_error(Exception(msg))
            assert c.error_type == ErrorType.MODEL_ERROR
            assert c.is_transient is True

    def test_server_error(self):
        for msg in ("500 internal server error", "503 service unavailable", "overloaded"):
            c = classify_error(Exception(msg))
            assert c.error_type == ErrorType.MODEL_ERROR

    def test_resource_exhausted(self):
        for msg in ("context length exceeded", "max tokens", "token limit", "budget"):
            c = classify_error(Exception(msg))
            assert c.error_type == ErrorType.RESOURCE_EXHAUSTED

    def test_parse_error_value_error(self):
        c = classify_error(ValueError("json decode error"))
        assert c.error_type == ErrorType.PARSE_ERROR

    def test_tool_error_with_tool_name(self):
        c = classify_error(Exception("something broke"), tool_name="ReadFileTool")
        assert c.error_type == ErrorType.TOOL_ERROR
        assert c.source == "ReadFileTool"

    def test_network_error(self):
        c = classify_error(Exception("httpx connection refused"))
        assert c.error_type == ErrorType.MODEL_ERROR
        assert c.is_transient is True

    def test_unknown_fallback(self):
        c = classify_error(Exception("something totally unexpected xyzzy"))
        assert c.error_type == ErrorType.UNKNOWN

    def test_source_propagated(self):
        c = classify_error(Exception("timed out"), source="ralph_iter_5")
        assert c.source == "ralph_iter_5"

    def test_string_error_accepted(self):
        c = classify_error("permission denied")
        assert c.error_type == ErrorType.PERMISSION_DENIED

    def test_raw_exception_stored_for_unknown(self):
        exc = Exception("xyzzy")
        c = classify_error(exc)
        assert c.raw_exception is exc


# ── compute_recovery ──────────────────────────────────────────────────────────

class TestComputeRecovery:
    def _cls(self, error_type: ErrorType, is_transient: bool = False, source: str = "") -> ErrorClassification:
        return ErrorClassification(
            error_type=error_type,
            message="test",
            source=source,
            is_transient=is_transient,
        )

    def test_max_retries_exhausted_returns_abort(self):
        c = self._cls(ErrorType.MODEL_ERROR, is_transient=True)
        s = compute_recovery(c, retry_count=3, max_retries=3)
        assert s.action == RecoveryAction.ABORT

    def test_permission_denied_returns_skip(self):
        c = self._cls(ErrorType.PERMISSION_DENIED)
        s = compute_recovery(c)
        assert s.action == RecoveryAction.SKIP_ITEM

    def test_timeout_returns_reduce_scope(self):
        c = self._cls(ErrorType.TIMEOUT, is_transient=True)
        s = compute_recovery(c, retry_count=0)
        assert s.action == RecoveryAction.REDUCE_SCOPE
        assert s.delay_seconds > 0
        assert s.suggested_batch_size is not None

    def test_timeout_batch_size_decreases_with_retries(self):
        c = self._cls(ErrorType.TIMEOUT)
        s0 = compute_recovery(c, retry_count=0)
        s1 = compute_recovery(c, retry_count=1)
        assert s0.suggested_batch_size >= s1.suggested_batch_size

    def test_transient_model_error_wait_and_retry(self):
        c = self._cls(ErrorType.MODEL_ERROR, is_transient=True)
        s = compute_recovery(c, retry_count=0)
        assert s.action == RecoveryAction.WAIT_AND_RETRY
        assert s.delay_seconds > 0

    def test_model_error_upgrade_when_upgrade_available(self):
        c = self._cls(ErrorType.MODEL_ERROR, is_transient=True)
        s = compute_recovery(c, retry_count=2, available_models=["glm-4-flash", "claude-sonnet-4-7"], current_model="glm-4-flash")
        assert s.action == RecoveryAction.UPGRADE_MODEL
        assert s.suggested_model == "claude-sonnet-4-7"

    def test_model_error_abort_when_no_upgrade(self):
        c = self._cls(ErrorType.MODEL_ERROR, is_transient=True)
        s = compute_recovery(c, retry_count=2, available_models=None, current_model=None)
        assert s.action == RecoveryAction.ABORT

    def test_resource_exhausted_reduce_scope(self):
        c = self._cls(ErrorType.RESOURCE_EXHAUSTED)
        s = compute_recovery(c)
        assert s.action == RecoveryAction.REDUCE_SCOPE

    def test_parse_error_retry_same(self):
        c = self._cls(ErrorType.PARSE_ERROR, is_transient=True)
        s = compute_recovery(c)
        assert s.action == RecoveryAction.RETRY_SAME

    def test_tool_error_switch_tool(self):
        c = self._cls(ErrorType.TOOL_ERROR, source="BashTool")
        s = compute_recovery(c)
        assert s.action == RecoveryAction.SWITCH_TOOL
        assert s.tool_hint is not None

    def test_unknown_transient_retry(self):
        c = self._cls(ErrorType.UNKNOWN, is_transient=True)
        s = compute_recovery(c)
        assert s.action == RecoveryAction.RETRY_SAME

    def test_unknown_non_transient_skip(self):
        c = self._cls(ErrorType.UNKNOWN, is_transient=False)
        s = compute_recovery(c)
        assert s.action == RecoveryAction.SKIP_ITEM

    def test_delay_caps_at_30s(self):
        c = self._cls(ErrorType.TIMEOUT, is_transient=True)
        s = compute_recovery(c, retry_count=10)
        assert s.delay_seconds <= 30.0


# ── ErrorTracker ──────────────────────────────────────────────────────────────

class TestErrorTracker:
    def _cls(self, error_type: ErrorType, source: str = "") -> ErrorClassification:
        return ErrorClassification(error_type=error_type, message="x", source=source)

    def test_starts_empty(self):
        t = ErrorTracker()
        assert t.total_errors == 0

    def test_record_increments_total(self):
        t = ErrorTracker()
        t.record(self._cls(ErrorType.TOOL_ERROR))
        assert t.total_errors == 1

    def test_get_retry_count_for_type(self):
        t = ErrorTracker()
        t.record(self._cls(ErrorType.MODEL_ERROR))
        t.record(self._cls(ErrorType.MODEL_ERROR))
        t.record(self._cls(ErrorType.TIMEOUT))
        assert t.get_retry_count_for_type(ErrorType.MODEL_ERROR) == 2
        assert t.get_retry_count_for_type(ErrorType.TIMEOUT) == 1
        assert t.get_retry_count_for_type(ErrorType.UNKNOWN) == 0

    def test_tool_blacklisted_after_threshold(self):
        t = ErrorTracker()
        for _ in range(3):
            t.record(self._cls(ErrorType.TOOL_ERROR, source="BadTool"))
        assert t.is_tool_blacklisted("BadTool") is True

    def test_tool_not_blacklisted_below_threshold(self):
        t = ErrorTracker()
        for _ in range(2):
            t.record(self._cls(ErrorType.TOOL_ERROR, source="MehTool"))
        assert t.is_tool_blacklisted("MehTool") is False

    def test_get_dominant_error_type(self):
        t = ErrorTracker()
        t.record(self._cls(ErrorType.TIMEOUT))
        t.record(self._cls(ErrorType.MODEL_ERROR))
        t.record(self._cls(ErrorType.MODEL_ERROR))
        assert t.get_dominant_error_type() == ErrorType.MODEL_ERROR

    def test_get_dominant_returns_none_when_empty(self):
        assert ErrorTracker().get_dominant_error_type() is None

    def test_summary_structure(self):
        t = ErrorTracker()
        t.record(self._cls(ErrorType.TOOL_ERROR, source="X"))
        s = t.summary()
        assert "total_errors" in s
        assert "type_counts" in s
        assert "tool_failures" in s
        assert s["total_errors"] == 1
