from __future__ import annotations

from pathlib import Path

from .state import backups_dir, default_target_root, load_install_state


def _discover_backup_files(target_root: Path) -> dict[str, list[dict[str, object]]]:
    root = backups_dir(target_root)
    discovered: dict[str, list[dict[str, object]]] = {
        "settings.json": [],
        "mcp.json": [],
    }
    if not root.exists():
        return discovered

    patterns = {
        "settings.json": "settings.*.json.bak",
        "mcp.json": "mcp.*.json.bak",
    }
    for artifact, pattern in patterns.items():
        for path in sorted(root.glob(pattern)):
            discovered[artifact].append(
                {
                    "name": path.name,
                    "path": str(path),
                    "exists": path.exists(),
                }
            )
    return discovered


def list_claude_backups(*, target_root: str | None = None) -> dict:
    root = default_target_root(target_root)
    state = load_install_state(root)
    if state is None:
        return {
            "target": "claude",
            "target_root": str(root),
            "installed": False,
            "backups": {},
            "available_versions": {},
            "text": f"Prax Claude backups\n"
            f"target_root: {root}\n"
            "installed: False\n"
            "backups: none",
        }

    backups = {}
    for name, path_str in state.backups.items():
        backups[name] = {
            "path": path_str,
            "exists": Path(path_str).exists(),
        }
    versions = _discover_backup_files(root)

    lines = [
        "Prax Claude backups",
        f"target_root: {root}",
        f"installed: True",
        f"backup_count: {len(backups)}",
    ]
    if backups:
        lines.append("backups:")
        for name, info in sorted(backups.items()):
            lines.append(f"  - {name}: exists={info['exists']} path={info['path']}")
    else:
        lines.append("backups: none")
    version_count = sum(len(items) for items in versions.values())
    lines.append(f"versioned_backups: {version_count}")

    return {
        "target": "claude",
        "target_root": str(root),
        "installed": True,
        "backups": backups,
        "available_versions": versions,
        "text": "\n".join(lines),
    }


def resolve_backup_path(
    *,
    target_root: Path,
    artifact: str,
    backup_name: str | None = None,
    backup_path: str | None = None,
) -> Path | None:
    if backup_path:
        return Path(backup_path).expanduser().resolve()

    discovered = _discover_backup_files(target_root).get(artifact, [])
    if backup_name:
        for entry in discovered:
            if entry["name"] == backup_name:
                return Path(str(entry["path"]))
        return None

    if discovered:
        return Path(str(discovered[-1]["path"]))
    return None
