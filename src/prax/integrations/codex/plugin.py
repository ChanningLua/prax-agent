"""Install Prax as a personal Codex plugin.

The source plugin is bundled in ``prax.assets.codex``.  Installation copies it
to ``~/plugins/prax``, registers it in the default personal marketplace, and
uses ``codex plugin add`` so Codex loads the same artifact a user sees in the
Plugins UI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

PLUGIN_NAME = "prax"
ASSET_PACKAGE = "prax.assets.codex"
MARKETPLACE_DEFAULT_NAME = "personal"
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _source_root():
    return files(ASSET_PACKAGE).joinpath(PLUGIN_NAME)


def _copy_tree(source, destination: Path) -> list[str]:
    copied: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            copied.extend(_copy_tree(child, target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(child.read_bytes())
            copied.append(str(target))
    return copied


def _remove_legacy_assets(plugin_root: Path) -> None:
    """Remove Prax-owned paths superseded by renamed product surfaces."""
    shutil.rmtree(plugin_root / "skills" / "prax-7x24", ignore_errors=True)


def _manifest_path(plugin_root: Path) -> Path:
    return plugin_root / ".codex-plugin" / "plugin.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


def _cachebusted_version(manifest: dict[str, Any], now: datetime | None = None) -> str:
    base = str(manifest.get("version", "1.0.0")).split("+", 1)[0]
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"{base}+codex.local-{stamp}"


def _relative_marketplace_source(home: Path, plugin_root: Path) -> str:
    try:
        relative = plugin_root.resolve().relative_to(home.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Codex personal plugin root must stay inside {home}: {plugin_root}"
        ) from exc
    return "./" + relative.as_posix()


def _marketplace_payload(
    current: dict[str, Any],
    *,
    source_path: str,
) -> tuple[dict[str, Any], str]:
    payload = dict(current)
    marketplace_name = str(payload.get("name") or MARKETPLACE_DEFAULT_NAME)
    payload["name"] = marketplace_name
    interface = payload.get("interface")
    if not isinstance(interface, dict):
        interface = {}
    interface.setdefault("displayName", "Personal")
    payload["interface"] = interface

    raw_plugins = payload.get("plugins")
    plugins = list(raw_plugins) if isinstance(raw_plugins, list) else []
    entry = {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": source_path,
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }
    replaced = False
    next_plugins: list[Any] = []
    for item in plugins:
        if isinstance(item, dict) and item.get("name") == PLUGIN_NAME:
            next_plugins.append(entry)
            replaced = True
        else:
            next_plugins.append(item)
    if not replaced:
        next_plugins.append(entry)
    payload["plugins"] = next_plugins
    return payload, marketplace_name


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    kwargs.pop("check", None)
    return subprocess.run(*args, **kwargs, check=False)


def _resolve_codex_bin(codex_bin: str | None) -> str:
    resolved = codex_bin or shutil.which("codex")
    if not resolved:
        raise RuntimeError("Codex CLI is not installed or not on PATH")
    return resolved


def install_codex_integration(
    *,
    target_root: str | None = None,
    home: str | None = None,
    marketplace_path: str | None = None,
    codex_bin: str | None = None,
    dry_run: bool = False,
    activate: bool = True,
    runner: Runner | None = None,
    now: datetime | None = None,
    local: bool = False,
    project_root: str | None = None,
    probe: Callable | None = None,
) -> dict[str, Any]:
    if local:
        if target_root or home or marketplace_path or activate is False:
            raise ValueError("--local cannot be combined with user-plugin root/marketplace/activation overrides")
        from .project import install_project_codex
        return install_project_codex(project_root=project_root or Path.cwd(),
                                     dry_run=dry_run, codex_bin=codex_bin, probe=probe)
    home_path = Path(home).expanduser().resolve() if home else Path.home().resolve()
    plugin_root = (
        Path(target_root).expanduser().resolve()
        if target_root
        else home_path / "plugins" / PLUGIN_NAME
    )
    marketplace = (
        Path(marketplace_path).expanduser().resolve()
        if marketplace_path
        else home_path / ".agents" / "plugins" / "marketplace.json"
    )
    source_path = _relative_marketplace_source(home_path, plugin_root)
    existing_marketplace = _read_json(marketplace)
    next_marketplace, marketplace_name = _marketplace_payload(
        existing_marketplace,
        source_path=source_path,
    )

    if dry_run:
        return {
            "target": "codex",
            "scope": "user",
            "dry_run": True,
            "plugin_root": str(plugin_root),
            "marketplace_path": str(marketplace),
            "marketplace_name": marketplace_name,
            "activate": activate,
            "text": f"Would install Prax Codex plugin at {plugin_root}",
        }

    _remove_legacy_assets(plugin_root)
    copied = _copy_tree(_source_root(), plugin_root)
    manifest_path = _manifest_path(plugin_root)
    manifest = _read_json(manifest_path)
    if manifest.get("name") != PLUGIN_NAME:
        raise ValueError(f"Bundled Codex manifest name must be {PLUGIN_NAME!r}")
    version = _cachebusted_version(manifest, now=now)
    manifest["version"] = version
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    marketplace.parent.mkdir(parents=True, exist_ok=True)
    marketplace.write_text(
        json.dumps(next_marketplace, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    activation: dict[str, Any] | None = None
    if activate:
        command = [
            _resolve_codex_bin(codex_bin),
            "plugin",
            "add",
            f"{PLUGIN_NAME}@{marketplace_name}",
            "--json",
        ]
        completed = (runner or _default_runner)(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"codex plugin add failed ({completed.returncode}): {detail}"
            )
        try:
            activation = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            activation = {"stdout": completed.stdout.strip()}

    return {
        "target": "codex",
        "scope": "user",
        "dry_run": False,
        "plugin_root": str(plugin_root),
        "manifest_path": str(manifest_path),
        "marketplace_path": str(marketplace),
        "marketplace_name": marketplace_name,
        "version": version,
        "assets_installed": len(copied),
        "activation": activation,
        "text": (
            "Prax Codex plugin installed\n"
            f"plugin_root: {plugin_root}\n"
            f"marketplace: {marketplace_name}\n"
            f"version: {version}"
        ),
    }


def _codex_plugin_inventory(
    *,
    codex_bin: str | None,
    runner: Runner | None,
) -> list[dict[str, Any]]:
    command = [_resolve_codex_bin(codex_bin), "plugin", "list", "--json"]
    completed = (runner or _default_runner)(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return []
    installed = payload.get("installed", []) if isinstance(payload, dict) else []
    return [item for item in installed if isinstance(item, dict)]


def doctor_codex_install(
    *,
    target_root: str | None = None,
    home: str | None = None,
    marketplace_path: str | None = None,
    codex_bin: str | None = None,
    runner: Runner | None = None,
    local: bool | None = None,
    project_root: str | None = None,
    probe: Callable | None = None,
) -> dict[str, Any]:
    if local is True or (local is None and project_root is not None):
        if target_root or home or marketplace_path:
            raise ValueError("project diagnosis cannot inspect a user-plugin root/marketplace")
        from .project import doctor_project_codex
        return doctor_project_codex(project_root=project_root or Path.cwd(),
                                    codex_bin=codex_bin, probe=probe)
    home_path = Path(home).expanduser().resolve() if home else Path.home().resolve()
    plugin_root = (
        Path(target_root).expanduser().resolve()
        if target_root
        else home_path / "plugins" / PLUGIN_NAME
    )
    marketplace = (
        Path(marketplace_path).expanduser().resolve()
        if marketplace_path
        else home_path / ".agents" / "plugins" / "marketplace.json"
    )
    manifest_path = _manifest_path(plugin_root)
    skill_path = plugin_root / "skills" / "prax-shift" / "SKILL.md"
    marketplace_data = _read_json(marketplace)
    marketplace_entry = any(
        isinstance(item, dict) and item.get("name") == PLUGIN_NAME
        for item in marketplace_data.get("plugins", [])
    )
    inventory = _codex_plugin_inventory(codex_bin=codex_bin, runner=runner)
    installed_entry = next(
        (
            item
            for item in inventory
            if item.get("name") == PLUGIN_NAME and item.get("installed") is not False
        ),
        None,
    )
    checks = {
        "manifest": manifest_path.exists(),
        "skill": skill_path.exists(),
        "marketplace_entry": marketplace_entry,
        "codex_plugin_installed": installed_entry is not None,
    }
    status = "ok" if all(checks.values()) else "degraded"
    return {
        "target": "codex",
        "scope": "user",
        "status": status,
        "checks": checks,
        "plugin_root": str(plugin_root),
        "marketplace_path": str(marketplace),
        "installed_plugin": installed_entry,
        "version": _read_json(manifest_path).get("version"),
        "missing": [name for name, passed in checks.items() if not passed],
        "text": f"Codex integration: {status}\nscope: user\nplugin_root: {plugin_root}",
    }


def list_installed_codex_assets(
    *,
    target_root: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    home_path = Path(home).expanduser().resolve() if home else Path.home().resolve()
    plugin_root = (
        Path(target_root).expanduser().resolve()
        if target_root
        else home_path / "plugins" / PLUGIN_NAME
    )
    assets = (
        sorted(
            str(path.relative_to(plugin_root))
            for path in plugin_root.rglob("*")
            if path.is_file()
        )
        if plugin_root.exists()
        else []
    )
    return {
        "target": "codex",
        "plugin_root": str(plugin_root),
        "assets": assets,
        "text": "\n".join(assets) if assets else "(Prax Codex plugin not installed)",
    }


def uninstall_codex_integration(
    *,
    target_root: str | None = None,
    home: str | None = None,
    marketplace_path: str | None = None,
    codex_bin: str | None = None,
    dry_run: bool = False,
    runner: Runner | None = None,
) -> dict[str, Any]:
    home_path = Path(home).expanduser().resolve() if home else Path.home().resolve()
    plugin_root = (
        Path(target_root).expanduser().resolve()
        if target_root
        else home_path / "plugins" / PLUGIN_NAME
    )
    marketplace = (
        Path(marketplace_path).expanduser().resolve()
        if marketplace_path
        else home_path / ".agents" / "plugins" / "marketplace.json"
    )
    marketplace_data = _read_json(marketplace)
    marketplace_name = str(marketplace_data.get("name") or MARKETPLACE_DEFAULT_NAME)
    if dry_run:
        return {
            "target": "codex",
            "dry_run": True,
            "plugin_root": str(plugin_root),
            "marketplace_path": str(marketplace),
            "text": f"Would uninstall {PLUGIN_NAME}@{marketplace_name}",
        }

    codex = shutil.which("codex") if codex_bin is None else codex_bin
    if codex:
        (runner or _default_runner)(
            [codex, "plugin", "remove", f"{PLUGIN_NAME}@{marketplace_name}", "--json"],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
    if marketplace_data:
        marketplace_data["plugins"] = [
            item
            for item in marketplace_data.get("plugins", [])
            if not (isinstance(item, dict) and item.get("name") == PLUGIN_NAME)
        ]
        marketplace.write_text(
            json.dumps(marketplace_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    manifest = _read_json(_manifest_path(plugin_root))
    if plugin_root.exists() and manifest.get("name") == PLUGIN_NAME:
        shutil.rmtree(plugin_root)
    return {
        "target": "codex",
        "dry_run": False,
        "plugin_root": str(plugin_root),
        "marketplace_path": str(marketplace),
        "text": "Prax Codex plugin uninstalled",
    }
