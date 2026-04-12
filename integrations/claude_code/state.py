from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "openprax.claude_install.v1"


@dataclass(frozen=True)
class ManagedAsset:
    source_relative_path: str
    destination_relative_path: str
    checksum_sha256: str
    ownership: str = "openprax-managed"
    profile: str = "full"


@dataclass(frozen=True)
class InstallHistoryEntry:
    operation: str
    timestamp: str
    details: dict[str, object]


@dataclass(frozen=True)
class ClaudeInstallState:
    schema_version: str
    installed_at: str
    last_validated_at: str | None
    target_root: str
    profile: str
    assets: list[ManagedAsset]
    managed_settings: dict[str, object]
    managed_mcp_config: dict[str, object]
    backups: dict[str, str]
    history: list[InstallHistoryEntry]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_target_root(env_override: str | None = None) -> Path:
    if env_override:
        return Path(env_override).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def install_state_path(target_root: Path) -> Path:
    return target_root / "prax" / "install-state.json"


def state_dir(target_root: Path) -> Path:
    return target_root / "prax"


def backups_dir(target_root: Path) -> Path:
    return state_dir(target_root) / "backups"


def history_dir(target_root: Path) -> Path:
    return state_dir(target_root) / "history"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_install_state(target_root: Path) -> ClaudeInstallState | None:
    path = install_state_path(target_root)
    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8"))
    assets = [
        ManagedAsset(
            source_relative_path=item["source_relative_path"],
            destination_relative_path=item["destination_relative_path"],
            checksum_sha256=item["checksum_sha256"],
            ownership=item.get("ownership", "openprax-managed"),
            profile=item.get("profile", "full"),
        )
        for item in data.get("assets", [])
    ]
    return ClaudeInstallState(
        schema_version=data.get("schema_version", ""),
        installed_at=data.get("installed_at", ""),
        last_validated_at=data.get("last_validated_at"),
        target_root=data.get("target_root", str(target_root)),
        profile=data.get("profile", "full"),
        assets=assets,
        managed_settings=data.get("managed_settings", {}),
        managed_mcp_config=data.get("managed_mcp_config", {}),
        backups=data.get("backups", {}),
        history=[
            InstallHistoryEntry(
                operation=item["operation"],
                timestamp=item["timestamp"],
                details=item.get("details", {}),
            )
            for item in data.get("history", [])
        ],
    )


def save_install_state(state: ClaudeInstallState) -> Path:
    target_root = Path(state.target_root)
    path = install_state_path(target_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": state.schema_version,
        "installed_at": state.installed_at,
        "last_validated_at": state.last_validated_at,
        "target_root": state.target_root,
        "profile": state.profile,
        "assets": [asdict(asset) for asset in state.assets],
        "managed_settings": state.managed_settings,
        "managed_mcp_config": state.managed_mcp_config,
        "backups": state.backups,
        "history": [asdict(entry) for entry in state.history],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def validate_install_state(state: ClaudeInstallState) -> None:
    if state.schema_version != SCHEMA_VERSION:
        raise ValueError(f"Unexpected install-state schema: {state.schema_version!r}")
    if not state.target_root:
        raise ValueError("Install-state target_root must not be empty")
    if not state.profile:
        raise ValueError("Install-state profile must not be empty")


def ownership_summary(state: ClaudeInstallState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in state.assets:
        counts[asset.ownership] = counts.get(asset.ownership, 0) + 1
    return counts


def append_history(
    state: ClaudeInstallState | None,
    *,
    operation: str,
    details: dict[str, object],
) -> list[InstallHistoryEntry]:
    history = list(state.history) if state else []
    history.append(
        InstallHistoryEntry(
            operation=operation,
            timestamp=now_iso(),
            details=details,
        )
    )
    return history[-20:]
