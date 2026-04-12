from __future__ import annotations

from pathlib import Path

from .archive import archive_install_state
from .mcp import load_mcp_config, mcp_path, remove_managed_mcp_config, save_mcp_config
from .manifest import destination_path
from .settings import build_managed_settings, load_settings, remove_managed_settings, save_settings, settings_path
from .state import append_history, default_target_root, install_state_path, load_install_state


def _prune_empty_dirs(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at and current.exists():
        if any(current.iterdir()):
            break
        current.rmdir()
        current = current.parent


def uninstall_claude_integration(*, target_root: str | None = None, dry_run: bool = False) -> dict:
    root = default_target_root(target_root)
    state = load_install_state(root)
    if state is None:
        return {
            "target": "claude",
            "target_root": str(root),
            "dry_run": dry_run,
            "removed_assets": [],
            "status": "not-installed",
        }

    removed: list[str] = []
    for asset in state.assets:
        dest = destination_path(root, asset)
        if not dest.exists():
            continue
        removed.append(asset.destination_relative_path)
        if not dry_run:
            dest.unlink()
            _prune_empty_dirs(dest.parent, root)

    if not dry_run:
        settings_file = settings_path(root)
        if settings_file.exists():
            cleaned = remove_managed_settings(load_settings(root), state.managed_settings or build_managed_settings(root))
            if cleaned:
                save_settings(root, cleaned)
            else:
                settings_file.unlink()

        mcp_file = mcp_path(root)
        if mcp_file.exists():
            cleaned_mcp = remove_managed_mcp_config(load_mcp_config(root), state.managed_mcp_config)
            if cleaned_mcp:
                save_mcp_config(root, cleaned_mcp)
            else:
                mcp_file.unlink()

    history = append_history(
        state,
        operation="uninstall",
        details={
            "dry_run": dry_run,
            "removed_assets": removed,
        },
    )

    state_path = install_state_path(root)
    if not dry_run and state_path.exists():
        archived_state = type(state)(
            schema_version=state.schema_version,
            installed_at=state.installed_at,
            last_validated_at=state.last_validated_at,
            target_root=state.target_root,
            profile=state.profile,
            assets=state.assets,
            managed_settings=state.managed_settings,
            managed_mcp_config=state.managed_mcp_config,
            backups=state.backups,
            history=history,
        )
        history_path = archive_install_state(archived_state, suffix="uninstall")
        state_path.unlink()
        _prune_empty_dirs(state_path.parent, root)
    else:
        history_path = None

    return {
        "target": "claude",
        "target_root": str(root),
        "dry_run": dry_run,
        "removed_assets": removed,
        "status": "ok",
        "archive_path": str(history_path) if history_path else None,
    }
