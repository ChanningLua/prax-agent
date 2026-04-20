from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import backups_dir

def settings_path(target_root: Path) -> Path:
    return target_root / "settings.json"


def load_settings(target_root: Path) -> dict[str, Any]:
    path = settings_path(target_root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_settings(target_root: Path, data: dict[str, Any]) -> Path:
    path = settings_path(target_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def backup_settings(target_root: Path) -> Path | None:
    source = settings_path(target_root)
    if not source.exists():
        return None
    backup_root = backups_dir(target_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = backup_root / f"settings.{timestamp}.json.bak"
    backup.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def build_managed_settings(target_root: Path) -> dict[str, Any]:
    session_start = str(target_root / "prax" / "hooks" / "session-start.sh")
    stop_hook = str(target_root / "prax" / "hooks" / "stop.sh")
    secret_scan = str(target_root / "prax" / "hooks" / "pre-write-secret-scan.sh")
    commit_quality = str(target_root / "prax" / "hooks" / "pre-commit-quality.sh")
    return {
        "permissions": {
            "allow": [
                "Bash(prax *)",
                "Bash(python3 -m prax.cli *)",
            ],
        },
        "enabledMcpjsonServers": [
            "prax-memory",
            "prax-sequential-thinking",
        ],
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume|compact",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash \"{session_start}\"",
                            "timeout": 5,
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash \"{secret_scan}\"",
                            "timeout": 5,
                        }
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash \"{commit_quality}\"",
                            "timeout": 5,
                        }
                    ],
                },
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash \"{stop_hook}\"",
                            "timeout": 5,
                        }
                    ],
                }
            ],
        },
    }


def _append_unique(existing: list[Any], required: list[Any]) -> list[Any]:
    merged = list(existing)
    existing_keys = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in existing}
    for item in required:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in existing_keys:
            continue
        merged.append(item)
        existing_keys.add(key)
    return merged


def merge_settings(existing: dict[str, Any], managed: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(existing))
    permissions = merged.setdefault("permissions", {})
    managed_permissions = managed.get("permissions", {})
    if isinstance(managed_permissions, dict):
        permissions["allow"] = _append_unique(
            permissions.get("allow", []) if isinstance(permissions.get("allow"), list) else [],
            managed_permissions.get("allow", []) if isinstance(managed_permissions.get("allow"), list) else [],
        )

    enabled_mcp = managed.get("enabledMcpjsonServers")
    if isinstance(enabled_mcp, list):
        merged["enabledMcpjsonServers"] = _append_unique(
            merged.get("enabledMcpjsonServers", []) if isinstance(merged.get("enabledMcpjsonServers"), list) else [],
            enabled_mcp,
        )

    marketplaces = managed.get("extraKnownMarketplaces")
    if isinstance(marketplaces, dict):
        merged_marketplaces = merged.setdefault("extraKnownMarketplaces", {})
        if isinstance(merged_marketplaces, dict):
            for key, value in marketplaces.items():
                merged_marketplaces[key] = value

    enabled_plugins = managed.get("enabledPlugins")
    if isinstance(enabled_plugins, dict):
        merged_enabled_plugins = merged.setdefault("enabledPlugins", {})
        if isinstance(merged_enabled_plugins, dict):
            for key, value in enabled_plugins.items():
                merged_enabled_plugins[key] = value

    hooks = merged.setdefault("hooks", {})
    managed_hooks = managed.get("hooks", {})
    if isinstance(managed_hooks, dict):
        for event_name, entries in managed_hooks.items():
            existing_entries = hooks.get(event_name, [])
            if not isinstance(existing_entries, list):
                existing_entries = []
            hooks[event_name] = _append_unique(existing_entries, entries if isinstance(entries, list) else [])

    return merged


def remove_managed_settings(existing: dict[str, Any], managed: dict[str, Any]) -> dict[str, Any]:
    cleaned = json.loads(json.dumps(existing))
    permissions = cleaned.get("permissions")
    managed_permissions = managed.get("permissions", {})
    if isinstance(permissions, dict) and isinstance(managed_permissions, dict):
        allow = permissions.get("allow", [])
        managed_allow = managed_permissions.get("allow", [])
        if isinstance(allow, list) and isinstance(managed_allow, list):
            permissions["allow"] = [item for item in allow if item not in managed_allow]
        if not permissions.get("allow"):
            permissions.pop("allow", None)
        if not permissions:
            cleaned.pop("permissions", None)

    managed_mcp = managed.get("enabledMcpjsonServers", [])
    if isinstance(managed_mcp, list):
        enabled_mcp = cleaned.get("enabledMcpjsonServers", [])
        if isinstance(enabled_mcp, list):
            cleaned["enabledMcpjsonServers"] = [item for item in enabled_mcp if item not in managed_mcp]
            if not cleaned["enabledMcpjsonServers"]:
                cleaned.pop("enabledMcpjsonServers", None)

    marketplaces = cleaned.get("extraKnownMarketplaces")
    managed_marketplaces = managed.get("extraKnownMarketplaces", {})
    if isinstance(marketplaces, dict) and isinstance(managed_marketplaces, dict):
        for key in managed_marketplaces.keys():
            marketplaces.pop(key, None)
        if not marketplaces:
            cleaned.pop("extraKnownMarketplaces", None)

    enabled_plugins = cleaned.get("enabledPlugins")
    managed_plugins = managed.get("enabledPlugins", {})
    if isinstance(enabled_plugins, dict) and isinstance(managed_plugins, dict):
        for key in managed_plugins.keys():
            enabled_plugins.pop(key, None)
        if not enabled_plugins:
            cleaned.pop("enabledPlugins", None)

    hooks = cleaned.get("hooks")
    managed_hooks = managed.get("hooks", {})
    if isinstance(hooks, dict) and isinstance(managed_hooks, dict):
        for event_name, managed_entries in managed_hooks.items():
            existing_entries = hooks.get(event_name)
            if not isinstance(existing_entries, list):
                continue
            managed_keys = {
                json.dumps(item, sort_keys=True, ensure_ascii=False)
                for item in (managed_entries if isinstance(managed_entries, list) else [])
            }
            hooks[event_name] = [
                item
                for item in existing_entries
                if json.dumps(item, sort_keys=True, ensure_ascii=False) not in managed_keys
            ]
            if not hooks[event_name]:
                hooks.pop(event_name, None)
        if not hooks:
            cleaned.pop("hooks", None)

    return cleaned


def collect_settings_issues(current: dict[str, Any], managed: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    managed_permissions = managed.get("permissions", {})
    if isinstance(managed_permissions, dict):
        current_allow = current.get("permissions", {}).get("allow", [])
        if not isinstance(current_allow, list):
            current_allow = []
        for entry in managed_permissions.get("allow", []):
            if entry not in current_allow:
                issues.append({
                    "severity": "error",
                    "code": "missing_settings_permission",
                    "message": entry,
                })

    managed_hooks = managed.get("hooks", {})
    current_hooks = current.get("hooks", {})
    if isinstance(managed_hooks, dict) and isinstance(current_hooks, dict):
        for event_name, managed_entries in managed_hooks.items():
            existing_entries = current_hooks.get(event_name, [])
            if not isinstance(existing_entries, list):
                existing_entries = []
            existing_keys = {
                json.dumps(item, sort_keys=True, ensure_ascii=False)
                for item in existing_entries
            }
            for entry in managed_entries if isinstance(managed_entries, list) else []:
                key = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                if key not in existing_keys:
                    issues.append({
                        "severity": "warning",
                        "code": "missing_settings_hook",
                        "message": f"{event_name}:{entry.get('hooks', [{}])[0].get('command', '')}",
                    })

    managed_mcp = managed.get("enabledMcpjsonServers", [])
    current_enabled_mcp = current.get("enabledMcpjsonServers", [])
    if not isinstance(current_enabled_mcp, list):
        current_enabled_mcp = []
    for entry in managed_mcp if isinstance(managed_mcp, list) else []:
        if entry not in current_enabled_mcp:
            issues.append({
                "severity": "warning",
                "code": "missing_enabled_mcp_server",
                "message": entry,
            })

    marketplaces = managed.get("extraKnownMarketplaces", {})
    current_marketplaces = current.get("extraKnownMarketplaces", {})
    if not isinstance(current_marketplaces, dict):
        current_marketplaces = {}
    for entry in marketplaces.keys() if isinstance(marketplaces, dict) else []:
        if entry not in current_marketplaces:
            issues.append({
                "severity": "warning",
                "code": "missing_plugin_marketplace",
                "message": entry,
            })

    plugins = managed.get("enabledPlugins", {})
    current_plugins = current.get("enabledPlugins", {})
    if not isinstance(current_plugins, dict):
        current_plugins = {}
    for entry, expected in plugins.items() if isinstance(plugins, dict) else []:
        if current_plugins.get(entry) != expected:
            issues.append({
                "severity": "warning",
                "code": "missing_enabled_plugin",
                "message": entry,
            })

    return issues
