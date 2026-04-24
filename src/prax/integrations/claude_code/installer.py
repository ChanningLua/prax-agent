from __future__ import annotations

from pathlib import Path

from .archive import archive_install_state
from .mcp import backup_mcp_config, build_managed_mcp_config, load_mcp_config, save_mcp_config, merge_mcp_config
from .plugin import build_managed_plugin_settings
from .manifest import SUPPORTED_PROFILES, destination_path, resolve_claude_assets
from .settings import backup_settings, build_managed_settings, load_settings, merge_settings, save_settings
from .state import ClaudeInstallState, SCHEMA_VERSION, append_history, default_target_root, now_iso, save_install_state


def install_claude_integration(
    *,
    target_root: str | None = None,
    dry_run: bool = False,
    plugin_repo: str | None = None,
    profile: str = "full",
) -> dict:
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported Claude profile: {profile}")
    root = default_target_root(target_root)
    root.mkdir(parents=True, exist_ok=True)

    existing_state = None if dry_run else None
    if not dry_run:
        from .state import load_install_state

        existing_state = load_install_state(root)

    resolved_assets = resolve_claude_assets(profile=profile)
    for asset in resolved_assets:
        if dry_run:
            continue
        dest = destination_path(root, asset)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(asset.source_bytes)
        if dest.suffix == ".sh":
            dest.chmod(0o755)

    managed_settings = merge_settings(
        {},
        build_managed_plugin_settings(plugin_repo=plugin_repo),
    )
    managed_settings = merge_settings(managed_settings, build_managed_settings(root))
    managed_mcp_config = build_managed_mcp_config()
    backup = None if dry_run else backup_settings(root)
    mcp_backup = None if dry_run else backup_mcp_config(root)
    if not dry_run:
        save_settings(root, merge_settings(load_settings(root), managed_settings))
        save_mcp_config(root, merge_mcp_config(load_mcp_config(root), managed_mcp_config))

    state_path = None
    if not dry_run:
        state = ClaudeInstallState(
            schema_version=SCHEMA_VERSION,
            installed_at=(existing_state.installed_at if existing_state else now_iso()),
            last_validated_at=None,
            target_root=str(root),
            profile=profile,
            assets=[asset.to_managed_asset() for asset in resolved_assets],
            managed_settings=managed_settings,
            managed_mcp_config=managed_mcp_config,
            backups={
                **(existing_state.backups if existing_state else {}),
                **({"settings.json": str(backup)} if backup else {}),
                **({"mcp.json": str(mcp_backup)} if mcp_backup else {}),
            },
            history=append_history(
                existing_state,
                operation="install",
                details={
                    "profile": profile,
                    "plugin_repo": plugin_repo or "",
                    "asset_count": len(resolved_assets),
                    "dry_run": dry_run,
                },
            ),
        )
        state_path = save_install_state(state)
        archive_path = archive_install_state(state, suffix="install")
    else:
        archive_path = None
    return {
        "target": "claude",
        "target_root": str(root),
        "dry_run": dry_run,
        "install_state_path": str(state_path) if state_path else None,
        "assets_installed": len(resolved_assets),
        "settings_backup_path": str(backup) if backup else None,
        "mcp_backup_path": str(mcp_backup) if mcp_backup else None,
        "plugin_repo": plugin_repo,
        "profile": profile,
        "supported_profiles": list(SUPPORTED_PROFILES),
        "archive_path": str(archive_path) if archive_path else None,
    }
