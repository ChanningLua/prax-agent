from __future__ import annotations

import json
import shutil
from pathlib import Path

from .manifest import resolve_claude_assets


def _write_bundle(root: Path, *, profile: str) -> dict:
    assets = resolve_claude_assets(profile=profile)
    for asset in assets:
        dest = root / asset.destination_relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(asset.source_bytes)
        if dest.suffix == ".sh":
            dest.chmod(0o755)

    manifest = {
        "bundle_type": "prax-claude-plugin",
        "profile": profile,
        "asset_count": len(assets),
        "assets": [asset.destination_relative_path for asset in assets],
    }
    manifest_path = root / "prax-bundle.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "assets": assets,
        "manifest_path": manifest_path,
    }


def export_claude_plugin_bundle(*, out_dir: str, profile: str = "full") -> dict:
    root = Path(out_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundle = _write_bundle(root, profile=profile)

    result = {
        "out_dir": str(root),
        "profile": profile,
        "asset_count": len(bundle["assets"]),
        "manifest_path": str(bundle["manifest_path"]),
    }
    result["text"] = (
        "Prax Claude plugin bundle exported\n"
        f"out_dir: {result['out_dir']}\n"
        f"profile: {profile}\n"
        f"asset_count: {result['asset_count']}"
    )
    return result


def export_claude_marketplace_bundle(*, out_dir: str, profile: str = "full") -> dict:
    root = Path(out_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundle_dir = root / "prax-claude-marketplace"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    bundle = _write_bundle(bundle_dir, profile=profile)
    archive_base = root / f"prax-claude-marketplace-{profile}"
    archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=bundle_dir)

    result = {
        "out_dir": str(bundle_dir),
        "profile": profile,
        "asset_count": len(bundle["assets"]),
        "manifest_path": str(bundle["manifest_path"]),
        "archive_path": archive_path,
    }
    result["text"] = (
        "Prax Claude marketplace bundle exported\n"
        f"bundle_dir: {result['out_dir']}\n"
        f"archive_path: {archive_path}\n"
        f"profile: {profile}\n"
        f"asset_count: {result['asset_count']}"
    )
    return result
