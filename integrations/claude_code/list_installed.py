from __future__ import annotations

from .report import render_inventory_report
from .state import default_target_root, load_install_state, ownership_summary


def list_installed_claude_assets(*, target_root: str | None = None) -> dict:
    root = default_target_root(target_root)
    state = load_install_state(root)
    if state is None:
        result = {
            "target": "claude",
            "target_root": str(root),
            "installed": False,
            "assets": [],
            "managed_settings_keys": [],
            "managed_mcp_servers": [],
            "backups": {},
            "ownership": {},
            "layers": {
                "assets": {"installed": False, "count": 0},
                "settings": {"managed_keys": []},
                "mcp": {"managed_servers": []},
                "backups": {"count": 0},
            },
        }
        result["text"] = render_inventory_report(result)
        return result

    result = {
        "target": "claude",
        "target_root": str(root),
        "installed": True,
        "installed_at": state.installed_at,
        "profile": state.profile,
        "asset_count": len(state.assets),
        "assets": [asset.destination_relative_path for asset in state.assets],
        "managed_settings_keys": sorted(state.managed_settings.keys()),
        "managed_mcp_servers": sorted(state.managed_mcp_config.get("mcpServers", {}).keys()),
        "backups": state.backups,
        "ownership": ownership_summary(state),
        "history_tail": [
            {
                "operation": entry.operation,
                "timestamp": entry.timestamp,
                "details": entry.details,
            }
            for entry in state.history[-5:]
        ],
        "layers": {
            "assets": {"installed": True, "count": len(state.assets)},
            "settings": {"managed_keys": sorted(state.managed_settings.keys())},
            "mcp": {"managed_servers": sorted(state.managed_mcp_config.get("mcpServers", {}).keys())},
            "backups": {"count": len(state.backups)},
        },
    }
    result["text"] = render_inventory_report(result)
    return result
