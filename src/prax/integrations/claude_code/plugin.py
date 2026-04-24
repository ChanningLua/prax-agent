from __future__ import annotations

from pathlib import Path
from typing import Any


def build_managed_plugin_settings(*, plugin_repo: str | None) -> dict[str, Any]:
    if not plugin_repo:
        return {}
    plugin_name = "prax"
    plugin_key = f"{plugin_name}@{plugin_name}"
    return {
        "extraKnownMarketplaces": {
            plugin_name: {
                "source": {
                    "source": "github",
                    "repo": plugin_repo,
                }
            }
        },
        "enabledPlugins": {
            plugin_key: True,
        },
    }


def collect_plugin_issues(current: dict[str, Any], managed: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not managed:
        return issues

    marketplaces = current.get("extraKnownMarketplaces", {})
    enabled_plugins = current.get("enabledPlugins", {})
    if not isinstance(marketplaces, dict):
        marketplaces = {}
    if not isinstance(enabled_plugins, dict):
        enabled_plugins = {}

    for name in managed.get("extraKnownMarketplaces", {}).keys():
        if name not in marketplaces:
            issues.append({
                "severity": "warning",
                "code": "missing_plugin_marketplace",
                "message": name,
            })

    for name, enabled in managed.get("enabledPlugins", {}).items():
        if enabled_plugins.get(name) != enabled:
            issues.append({
                "severity": "warning",
                "code": "missing_enabled_plugin",
                "message": name,
            })
    return issues
