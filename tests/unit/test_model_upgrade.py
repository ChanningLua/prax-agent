"""Unit tests for prax.core.model_upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import httpx
import pytest

from prax.core.model_upgrade import (
    UpgradeDecision,
    get_exception_upgrade_reason,
    get_upgrade_path,
    should_upgrade_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeReport:
    stop_reason: str = "normal"
    had_tool_errors: bool = False
    only_permission_errors: bool = False
    verification_passed: bool = False


def _available_entry(name: str) -> MagicMock:
    entry = MagicMock()
    entry.available = True
    return entry


def _unavailable_entry(name: str) -> MagicMock:
    entry = MagicMock()
    entry.available = False
    return entry


# ---------------------------------------------------------------------------
# get_upgrade_path tests
# ---------------------------------------------------------------------------

# 1. Empty chain → [initial_model] if available
def test_get_upgrade_path_empty_chain_returns_initial():
    cfg = {}  # no upgrade_chain key
    with patch("prax.core.model_upgrade.get_model_entry") as mock_get:
        mock_get.return_value = _available_entry("modelA")
        result = get_upgrade_path("modelA", cfg)
    assert result == ["modelA"]


# 2. initial_model in chain → slice from that index
def test_get_upgrade_path_initial_in_chain():
    cfg = {"upgrade_chain": ["modelA", "modelB", "modelC"]}
    def mock_entry(name, _cfg):
        return _available_entry(name)
    with patch("prax.core.model_upgrade.get_model_entry", side_effect=mock_entry):
        result = get_upgrade_path("modelB", cfg)
    assert result == ["modelB", "modelC"]


# 3. initial_model not in chain → prepend
def test_get_upgrade_path_initial_not_in_chain():
    cfg = {"upgrade_chain": ["modelA", "modelB"]}
    def mock_entry(name, _cfg):
        return _available_entry(name)
    with patch("prax.core.model_upgrade.get_model_entry", side_effect=mock_entry):
        result = get_upgrade_path("modelX", cfg)
    assert result == ["modelX", "modelA", "modelB"]


# 4. Filters unavailable models
def test_get_upgrade_path_filters_unavailable():
    cfg = {"upgrade_chain": ["modelA", "modelB", "modelC"]}
    def mock_entry(name, _cfg):
        if name == "modelB":
            return _unavailable_entry(name)
        return _available_entry(name)
    with patch("prax.core.model_upgrade.get_model_entry", side_effect=mock_entry):
        result = get_upgrade_path("modelA", cfg)
    assert "modelB" not in result
    assert "modelA" in result
    assert "modelC" in result


# 5. Filters models with no entry (get_model_entry returns None)
def test_get_upgrade_path_filters_none_entries():
    cfg = {"upgrade_chain": ["modelA", "ghost", "modelC"]}
    def mock_entry(name, _cfg):
        if name == "ghost":
            return None
        return _available_entry(name)
    with patch("prax.core.model_upgrade.get_model_entry", side_effect=mock_entry):
        result = get_upgrade_path("modelA", cfg)
    assert "ghost" not in result
    assert result == ["modelA", "modelC"]


# ---------------------------------------------------------------------------
# should_upgrade_model tests
# ---------------------------------------------------------------------------

# 5. Empty response → retry
def test_should_upgrade_model_empty_response():
    report = FakeReport()
    decision = should_upgrade_model(report, "   ")
    assert decision.should_retry is True
    assert decision.reason == "empty_response"


# 6. max_iterations stop_reason → retry
def test_should_upgrade_model_max_iterations_stop_reason():
    report = FakeReport(stop_reason="max_iterations")
    decision = should_upgrade_model(report, "some text")
    assert decision.should_retry is True
    assert decision.reason == "max_iterations"


# 7. safety_stop → retry
def test_should_upgrade_model_safety_stop():
    report = FakeReport(stop_reason="safety_stop")
    decision = should_upgrade_model(report, "some text")
    assert decision.should_retry is True
    assert decision.reason == "safety_stop"


# 8. max iterations text → retry
def test_should_upgrade_model_max_iterations_text():
    report = FakeReport(stop_reason="normal")
    decision = should_upgrade_model(report, "[Prax] Max iterations reached after 25 steps")
    assert decision.should_retry is True
    assert decision.reason == "max_iterations"


# 9. tool_error (not permission) → retry
def test_should_upgrade_model_tool_error_not_permission():
    report = FakeReport(stop_reason="normal", had_tool_errors=True, only_permission_errors=False)
    decision = should_upgrade_model(report, "some output")
    assert decision.should_retry is True
    assert decision.reason == "tool_error"


# 10. Normal response → no retry
def test_should_upgrade_model_normal_no_retry():
    report = FakeReport(stop_reason="normal", had_tool_errors=False)
    decision = should_upgrade_model(report, "Task completed successfully.")
    assert decision.should_retry is False


# Extra: permission-only tool errors → no retry
def test_should_upgrade_model_only_permission_errors_no_retry():
    report = FakeReport(stop_reason="normal", had_tool_errors=True, only_permission_errors=True)
    decision = should_upgrade_model(report, "Permission denied output")
    assert decision.should_retry is False


def test_should_upgrade_model_skips_retry_when_verification_passed():
    report = FakeReport(
        stop_reason="normal",
        had_tool_errors=True,
        only_permission_errors=False,
        verification_passed=True,
    )
    decision = should_upgrade_model(report, "Fixed. Tests now pass.")
    assert decision.should_retry is False


# ---------------------------------------------------------------------------
# get_exception_upgrade_reason tests
# ---------------------------------------------------------------------------

# 11. httpx.HTTPError → "provider_http_error"
def test_get_exception_upgrade_reason_httpx_error():
    exc = httpx.HTTPStatusError(
        "404 Not Found",
        request=MagicMock(),
        response=MagicMock(),
    )
    assert get_exception_upgrade_reason(exc) == "provider_http_error"


def test_get_exception_upgrade_reason_httpx_connect_error():
    exc = httpx.ConnectError("Connection refused")
    assert get_exception_upgrade_reason(exc) == "provider_http_error"


# 12. RuntimeError with "api error" → "provider_runtime_error"
def test_get_exception_upgrade_reason_runtime_api_error():
    exc = RuntimeError("Upstream API error: 503 Service Unavailable")
    assert get_exception_upgrade_reason(exc) == "provider_runtime_error"


# 13. RuntimeError with "timed out" → "provider_runtime_error"
def test_get_exception_upgrade_reason_runtime_timed_out():
    exc = RuntimeError("Request timed out after 30s")
    assert get_exception_upgrade_reason(exc) == "provider_runtime_error"


# RuntimeError with "connection" → "provider_runtime_error"
def test_get_exception_upgrade_reason_runtime_connection():
    exc = RuntimeError("Connection refused by remote host")
    assert get_exception_upgrade_reason(exc) == "provider_runtime_error"


# 14. Other exception → None
def test_get_exception_upgrade_reason_other_exception():
    exc = ValueError("something else entirely")
    assert get_exception_upgrade_reason(exc) is None


def test_get_exception_upgrade_reason_runtime_unrelated():
    exc = RuntimeError("something completely different")
    assert get_exception_upgrade_reason(exc) is None


# ---------------------------------------------------------------------------
# UpgradeDecision dataclass
# ---------------------------------------------------------------------------

def test_upgrade_decision_defaults():
    d = UpgradeDecision(should_retry=False)
    assert d.should_retry is False
    assert d.reason == ""


def test_upgrade_decision_with_reason():
    d = UpgradeDecision(should_retry=True, reason="tool_error")
    assert d.should_retry is True
    assert d.reason == "tool_error"
