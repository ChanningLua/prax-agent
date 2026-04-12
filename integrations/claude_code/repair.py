from __future__ import annotations

from .archive import archive_install_state
from .mcp import backup_mcp_config, build_managed_mcp_config, load_mcp_config, merge_mcp_config, save_mcp_config
from .plugin import build_managed_plugin_settings
from .manifest import SUPPORTED_PROFILES, destination_path, resolve_claude_assets
from .settings import backup_settings, build_managed_settings, load_settings, merge_settings, save_settings
from .state import ClaudeInstallState, append_history, default_target_root, load_install_state, now_iso, save_install_state


def repair_claude_integration(
    *,
    target_root: str | None = None,
    dry_run: bool = False,
    plugin_repo: str | None = None,
    profile: str | None = None,
) -> dict:
    root = default_target_root(target_root)
    current_state = load_install_state(root)
    effective_profile = profile or (current_state.profile if current_state else "full")
    if effective_profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported Claude profile: {effective_profile}")
    resolved_assets = {asset.destination_relative_path: asset for asset in resolve_claude_assets(profile=effective_profile)}

    repaired: list[str] = []
    for rel_path, asset in resolved_assets.items():
        dest = destination_path(root, asset)
        expected_checksum = asset.to_managed_asset().checksum_sha256
        current_checksum = None
        if dest.exists():
            from .state import sha256_bytes

            current_checksum = sha256_bytes(dest.read_bytes())
        if current_checksum == expected_checksum:
            continue
        repaired.append(rel_path)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(asset.source_bytes)
            if dest.suffix == ".sh":
                dest.chmod(0o755)

    if not dry_run:
        managed_settings = merge_settings(
            {},
            build_managed_plugin_settings(plugin_repo=plugin_repo),
        )
        managed_settings = merge_settings(managed_settings, build_managed_settings(root))
        managed_mcp_config = build_managed_mcp_config()
        existing_backups = dict(current_state.backups) if current_state else {}
        backup = None if existing_backups.get("settings.json") else backup_settings(root)
        mcp_backup = None if existing_backups.get("mcp.json") else backup_mcp_config(root)
        save_settings(root, merge_settings(load_settings(root), managed_settings))
        save_mcp_config(root, merge_mcp_config(load_mcp_config(root), managed_mcp_config))
        state = ClaudeInstallState(
            schema_version=(current_state.schema_version if current_state else "openprax.claude_install.v1"),
            installed_at=(current_state.installed_at if current_state else now_iso()),
            last_validated_at=now_iso(),
            target_root=str(root),
            profile=effective_profile,
            assets=[asset.to_managed_asset() for asset in resolved_assets.values()],
            managed_settings=managed_settings,
            managed_mcp_config=managed_mcp_config,
            backups={
                **existing_backups,
                **({"settings.json": str(backup)} if backup else {}),
                **({"mcp.json": str(mcp_backup)} if mcp_backup else {}),
            },
            history=append_history(
                current_state,
                operation="repair",
                details={
                    "profile": effective_profile,
                    "plugin_repo": plugin_repo or "",
                    "asset_count": len(resolved_assets),
                    "dry_run": dry_run,
                    "repaired_assets": repaired,
                },
            ),
        )
        save_install_state(state)
        archive_path = archive_install_state(state, suffix="repair")
    else:
        archive_path = None

    return {
        "target": "claude",
        "target_root": str(root),
        "dry_run": dry_run,
        "repaired_assets": repaired,
        "profile": effective_profile,
        "archive_path": str(archive_path) if archive_path else None,
    }
