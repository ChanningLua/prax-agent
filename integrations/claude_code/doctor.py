from __future__ import annotations

from pathlib import Path

from .archive import archive_install_state
from .mcp import collect_mcp_issues, load_mcp_config
from .manifest import destination_path
from .report import render_doctor_report
from .settings import collect_settings_issues, load_settings
from .state import (
    append_history,
    default_target_root,
    load_install_state,
    now_iso,
    ownership_summary,
    save_install_state,
    sha256_bytes,
    validate_install_state,
)


def _issue_layer(issue_code: str) -> str:
    if issue_code in {"missing_asset", "drifted_asset"}:
        return "assets"
    if issue_code.startswith("missing_mcp_"):
        return "mcp"
    if issue_code.startswith("missing_settings_") or issue_code.startswith("missing_enabled_") or issue_code.startswith("missing_plugin_"):
        return "settings"
    return "unknown"


def doctor_claude_install(*, target_root: str | None = None) -> dict:
    root = default_target_root(target_root)
    state = load_install_state(root)
    if state is None:
        result = {
            "target": "claude",
            "status": "not-installed",
            "target_root": str(root),
            "issues": [{"severity": "error", "code": "missing_install_state", "message": "Prax Claude integration is not installed."}],
            "summary": {"error_count": 1, "warning_count": 0},
            "layers": {
                "assets": {"status": "missing", "issues": 0},
                "settings": {"status": "missing", "issues": 0},
                "mcp": {"status": "missing", "issues": 0},
                "backups": {"status": "missing", "issues": 0},
            },
        }
        result["text"] = render_doctor_report(result)
        return result

    validate_install_state(state)

    issues: list[dict[str, str]] = []
    for asset in state.assets:
        dest = destination_path(root, asset)
        if not dest.exists():
            issues.append({
                "severity": "error",
                "code": "missing_asset",
                "message": asset.destination_relative_path,
            })
            continue
        current_checksum = sha256_bytes(dest.read_bytes())
        if current_checksum != asset.checksum_sha256:
            issues.append({
                "severity": "warning",
                "code": "drifted_asset",
                "message": asset.destination_relative_path,
            })

    issues.extend(collect_settings_issues(load_settings(root), state.managed_settings))
    issues.extend(collect_mcp_issues(load_mcp_config(root), state.managed_mcp_config))

    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] != "error")
    if error_count:
        status = "error"
    elif warning_count:
        status = "warning"
    else:
        status = "ok"

    grouped_issues: dict[str, list[dict[str, str]]] = {
        "assets": [],
        "settings": [],
        "mcp": [],
        "unknown": [],
    }
    for issue in issues:
        grouped_issues[_issue_layer(issue["code"])].append(issue)

    def _layer_status(name: str) -> str:
        layer_issues = grouped_issues.get(name, [])
        if any(issue["severity"] == "error" for issue in layer_issues):
            return "error"
        if layer_issues:
            return "warning"
        return "ok"

    refreshed = type(state)(
        schema_version=state.schema_version,
        installed_at=state.installed_at,
        last_validated_at=now_iso(),
        target_root=state.target_root,
        profile=state.profile,
        assets=state.assets,
        managed_settings=state.managed_settings,
        managed_mcp_config=state.managed_mcp_config,
        backups=state.backups,
        history=append_history(
            state,
            operation="doctor",
            details={
                "status": status,
                "error_count": error_count,
                "warning_count": warning_count,
            },
        ),
    )
    save_install_state(refreshed)
    archive_path = archive_install_state(refreshed, suffix="doctor")

    result = {
        "target": "claude",
        "status": status,
        "target_root": str(root),
        "profile": state.profile,
        "issues": issues,
        "asset_count": len(state.assets),
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
            for entry in refreshed.history[-5:]
        ],
        "issue_groups": grouped_issues,
        "layers": {
            "assets": {"status": _layer_status("assets"), "issues": len(grouped_issues["assets"])},
            "settings": {"status": _layer_status("settings"), "issues": len(grouped_issues["settings"])},
            "mcp": {"status": _layer_status("mcp"), "issues": len(grouped_issues["mcp"])},
            "backups": {"status": "ok" if state.backups else "missing", "issues": 0},
        },
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "archive_path": str(archive_path),
    }
    result["text"] = render_doctor_report(result)
    return result
