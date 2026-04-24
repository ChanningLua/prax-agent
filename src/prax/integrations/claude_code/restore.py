from __future__ import annotations

from pathlib import Path

from .backups import resolve_backup_path
from .mcp import mcp_path
from .settings import settings_path
from .state import default_target_root, load_install_state


def restore_claude_backup(
    *,
    target_root: str | None = None,
    artifact: str,
    backup_name: str | None = None,
    backup_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    root = default_target_root(target_root)
    state = load_install_state(root)
    if state is None:
        return {
            "target": "claude",
            "target_root": str(root),
            "artifact": artifact,
            "status": "not-installed",
            "restored": False,
        }

    resolved_backup = resolve_backup_path(
        target_root=root,
        artifact=artifact,
        backup_name=backup_name,
        backup_path=backup_path,
    )
    if resolved_backup is None:
        return {
            "target": "claude",
            "target_root": str(root),
            "artifact": artifact,
            "status": "missing-backup",
            "restored": False,
        }

    backup_file = Path(resolved_backup)
    if not backup_file.exists():
        return {
            "target": "claude",
            "target_root": str(root),
            "artifact": artifact,
            "status": "missing-backup-file",
            "restored": False,
            "backup_path": str(backup_file),
        }

    destinations = {
        "settings.json": settings_path(root),
        "mcp.json": mcp_path(root),
    }
    destination = destinations.get(artifact)
    if destination is None:
        raise ValueError("artifact must be settings.json or mcp.json")

    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(backup_file.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "target": "claude",
        "target_root": str(root),
        "artifact": artifact,
        "status": "ok",
        "restored": True,
        "dry_run": dry_run,
        "backup_path": str(backup_file),
        "destination_path": str(destination),
    }
