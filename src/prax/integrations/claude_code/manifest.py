from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .state import ManagedAsset, sha256_bytes


ASSET_PACKAGE = "prax.assets.claude"
SUPPORTED_PROFILES = ("minimal", "developer", "full")


@dataclass(frozen=True)
class ResolvedAsset:
    source_relative_path: str
    destination_relative_path: str
    source_bytes: bytes
    profile: str

    def to_managed_asset(self) -> ManagedAsset:
        return ManagedAsset(
            source_relative_path=self.source_relative_path,
            destination_relative_path=self.destination_relative_path,
            checksum_sha256=sha256_bytes(self.source_bytes),
            profile=self.profile,
        )


def _iter_files(base) -> list:
    resolved = []
    for child in base.iterdir():
        if child.is_file():
            resolved.append(child)
        elif child.is_dir():
            resolved.extend(_iter_files(child))
    return resolved


def _profile_for_path(rel_path: str) -> str:
    if rel_path.startswith("commands/prax-status") or rel_path.startswith("commands/prax-doctor"):
        return "minimal"
    if rel_path.startswith("rules/prax/runtime") or rel_path.startswith("rules/prax/security"):
        return "minimal"
    if rel_path.startswith("skills/prax-native"):
        return "minimal"
    if rel_path.startswith("prax/hooks/") or rel_path.startswith("hooks/README"):
        return "minimal"
    if rel_path.startswith(".claude-plugin/"):
        return "developer"
    if rel_path.startswith("commands/") or rel_path.startswith("skills/") or rel_path.startswith("rules/prax/"):
        return "developer"
    return "full"


def _profile_allows(asset_profile: str, requested_profile: str) -> bool:
    order = {"minimal": 0, "developer": 1, "full": 2}
    return order[asset_profile] <= order[requested_profile]


def resolve_claude_assets(profile: str = "full") -> list[ResolvedAsset]:
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported Claude profile: {profile}")
    base = files(ASSET_PACKAGE)
    assets: list[ResolvedAsset] = []
    for asset in _iter_files(base):
        rel_path = str(asset.relative_to(base)).replace("\\", "/")
        parts = rel_path.split("/")
        if asset.name == "__init__.py" or "__pycache__" in parts:
            continue
        asset_profile = _profile_for_path(rel_path)
        if not _profile_allows(asset_profile, profile):
            continue
        assets.append(
            ResolvedAsset(
                source_relative_path=rel_path,
                destination_relative_path=rel_path,
                source_bytes=asset.read_bytes(),
                profile=asset_profile,
            )
        )
    return sorted(assets, key=lambda item: item.destination_relative_path)


def destination_path(target_root: Path, asset: ManagedAsset | ResolvedAsset) -> Path:
    return target_root / asset.destination_relative_path
