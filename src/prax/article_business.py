"""Shared schema helpers for connecting article signals to business decisions."""

from __future__ import annotations

from typing import Any

BUSINESS_BRIDGE_FIELDS = (
    "source_signal",
    "technical_delta",
    "affected_workflow",
    "business_decision",
    "validation_method",
    "success_metric",
    "no_fit_conditions",
)


def business_bridge_details(payload: Any) -> tuple[float, list[str]]:
    """Return completeness and missing fields for a business-bridge object."""

    if not isinstance(payload, dict):
        return 0.0, list(BUSINESS_BRIDGE_FIELDS)
    bridge_value = payload.get("business_bridge", payload)
    bridge = bridge_value if isinstance(bridge_value, dict) else {}
    missing = [
        field
        for field in BUSINESS_BRIDGE_FIELDS
        if not str(bridge.get(field, "")).strip()
    ]
    completeness = round(
        (len(BUSINESS_BRIDGE_FIELDS) - len(missing)) / len(BUSINESS_BRIDGE_FIELDS),
        2,
    )
    return completeness, missing
