from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import backups_dir

def mcp_path(target_root: Path) -> Path:
    return target_root / ".mcp.json"


def load_mcp_config(target_root: Path) -> dict[str, Any]:
    path = mcp_path(target_root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_mcp_config(target_root: Path, data: dict[str, Any]) -> Path:
    path = mcp_path(target_root)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def backup_mcp_config(target_root: Path) -> Path | None:
    source = mcp_path(target_root)
    if not source.exists():
        return None
    backup_root = backups_dir(target_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = backup_root / f"mcp.{timestamp}.json.bak"
    backup.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def build_managed_mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "prax-memory": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-memory"],
                "description": "Prax managed persistent memory server",
            },
            "prax-sequential-thinking": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                "description": "Prax managed sequential-thinking server",
            },
        }
    }


def merge_mcp_config(existing: dict[str, Any], managed: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(existing))
    servers = merged.setdefault("mcpServers", {})
    for name, config in managed.get("mcpServers", {}).items():
        servers[name] = config
    return merged


def remove_managed_mcp_config(existing: dict[str, Any], managed: dict[str, Any]) -> dict[str, Any]:
    cleaned = json.loads(json.dumps(existing))
    servers = cleaned.get("mcpServers")
    if isinstance(servers, dict):
        for name in managed.get("mcpServers", {}).keys():
            servers.pop(name, None)
        if not servers:
            cleaned.pop("mcpServers", None)
    return cleaned


def collect_mcp_issues(current: dict[str, Any], managed: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    current_servers = current.get("mcpServers", {})
    if not isinstance(current_servers, dict):
        current_servers = {}
    for name in managed.get("mcpServers", {}).keys():
        if name not in current_servers:
            issues.append({
                "severity": "error",
                "code": "missing_mcp_server",
                "message": name,
            })
    return issues
