from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import ClaudeInstallState, history_dir


def archive_install_state(state: ClaudeInstallState, *, suffix: str = "snapshot") -> Path:
    target_root = Path(state.target_root)
    archive_root = history_dir(target_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = state.last_validated_at or state.installed_at
    safe_timestamp = (
        timestamp.replace(":", "").replace("-", "").replace(".", "").replace("+", "_")
    )
    path = archive_root / f"{safe_timestamp}-{suffix}.json"
    payload = {
        "schema_version": state.schema_version,
        "installed_at": state.installed_at,
        "last_validated_at": state.last_validated_at,
        "target_root": state.target_root,
        "profile": state.profile,
        "assets": [
            {
                "source_relative_path": asset.source_relative_path,
                "destination_relative_path": asset.destination_relative_path,
                "checksum_sha256": asset.checksum_sha256,
                "ownership": asset.ownership,
                "profile": asset.profile,
            }
            for asset in state.assets
        ],
        "managed_settings": state.managed_settings,
        "managed_mcp_config": state.managed_mcp_config,
        "backups": state.backups,
        "history": [
            {
                "operation": entry.operation,
                "timestamp": entry.timestamp,
                "details": entry.details,
            }
            for entry in state.history
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def list_archived_states(target_root: Path) -> list[dict[str, Any]]:
    archive_root = history_dir(target_root)
    if not archive_root.exists():
        return []
    entries = []
    for path in sorted(archive_root.glob("*.json")):
        entries.append(
            {
                "path": str(path),
                "name": path.name,
                "size": path.stat().st_size,
            }
        )
    return entries


def read_archived_state(target_root: Path, name: str | None = None) -> dict[str, Any] | None:
    entries = list_archived_states(target_root)
    if not entries:
        return None
    selected = entries[-1] if name is None else next((entry for entry in entries if entry["name"] == name), None)
    if selected is None:
        return None
    path = Path(str(selected["path"]))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_archive"] = selected
    return data
