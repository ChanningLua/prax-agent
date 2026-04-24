from __future__ import annotations


def render_doctor_report(report: dict) -> str:
    lines = [
        "Prax Claude Integration Doctor",
        f"target_root: {report.get('target_root')}",
        f"status: {report.get('status')}",
    ]

    if report.get("profile"):
        lines.append(f"profile: {report.get('profile')}")

    summary = report.get("summary", {})
    if summary:
        lines.append(
            f"errors={summary.get('error_count', 0)} warnings={summary.get('warning_count', 0)}"
        )

    layers = report.get("layers", {})
    if layers:
        lines.append("layers:")
        for name in ("assets", "settings", "mcp", "backups"):
            layer = layers.get(name, {})
            lines.append(
                f"  - {name}: status={layer.get('status')} issues={layer.get('issues', 0)}"
            )

    ownership = report.get("ownership", {})
    if ownership:
        ownership_text = ", ".join(f"{name}={count}" for name, count in sorted(ownership.items()))
        lines.append(f"ownership: {ownership_text}")

    issues = report.get("issues", [])
    if issues:
        lines.append("issues:")
        for issue in issues:
            lines.append(
                f"  - [{issue.get('severity')}] {issue.get('code')}: {issue.get('message')}"
            )
    else:
        lines.append("issues: none")

    return "\n".join(lines)


def render_inventory_report(report: dict) -> str:
    lines = [
        "Prax Claude Integration Inventory",
        f"target_root: {report.get('target_root')}",
        f"installed: {report.get('installed')}",
    ]

    if report.get("profile"):
        lines.append(f"profile: {report.get('profile')}")

    if report.get("installed"):
        lines.append(f"assets: {report.get('asset_count', 0)}")
        settings_keys = report.get("managed_settings_keys", [])
        mcp_servers = report.get("managed_mcp_servers", [])
        lines.append(f"managed_settings: {', '.join(settings_keys) if settings_keys else '(none)'}")
        lines.append(f"managed_mcp_servers: {', '.join(mcp_servers) if mcp_servers else '(none)'}")
        ownership = report.get("ownership", {})
        if ownership:
            lines.append(
                "ownership: "
                + ", ".join(f"{name}={count}" for name, count in sorted(ownership.items()))
            )

    return "\n".join(lines)
