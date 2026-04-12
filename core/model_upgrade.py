"""Helpers for model upgrade-chain retries."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .agent_loop import AgentRunReport
from .model_catalog import get_model_entry


@dataclass(frozen=True)
class UpgradeDecision:
    should_retry: bool
    reason: str = ""


def get_upgrade_path(initial_model: str, models_config: dict) -> list[str]:
    upgrade_chain = list(models_config.get("upgrade_chain", []))
    if not upgrade_chain:
        candidates = [initial_model]
    elif initial_model not in upgrade_chain:
        candidates = [initial_model, *[model for model in upgrade_chain if model != initial_model]]
    else:
        start_index = upgrade_chain.index(initial_model)
        candidates = upgrade_chain[start_index:]

    resolved: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        entry = get_model_entry(candidate, models_config)
        if entry is None:
            continue
        if not entry.available:
            continue
        resolved.append(candidate)
    return resolved


def should_upgrade_model(report: AgentRunReport, final_text: str) -> UpgradeDecision:
    normalized = final_text.strip()
    if not normalized:
        return UpgradeDecision(True, "empty_response")
    if report.verification_passed:
        return UpgradeDecision(False)
    if report.stop_reason in {"max_iterations", "safety_stop"}:
        return UpgradeDecision(True, report.stop_reason)
    if normalized.startswith("[Prax] Max iterations reached"):
        return UpgradeDecision(True, "max_iterations")
    if report.had_tool_errors and not report.only_permission_errors:
        return UpgradeDecision(True, "tool_error")
    return UpgradeDecision(False)


def get_exception_upgrade_reason(exc: Exception) -> str | None:
    if isinstance(exc, httpx.HTTPError):
        return "provider_http_error"
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        if "api error" in message or "timed out" in message or "connection" in message:
            return "provider_runtime_error"
    return None
