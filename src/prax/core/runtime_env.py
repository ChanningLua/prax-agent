"""Runtime environment hydration helpers for Prax entrypoints."""

from __future__ import annotations

import os
from pathlib import Path


def collect_runtime_env_names(models_config: dict) -> list[str]:
    names: list[str] = []
    for provider_cfg in models_config.get("providers", {}).values():
        raw_env_names = provider_cfg.get("api_key_env", "")
        if isinstance(raw_env_names, list):
            candidates = [str(name) for name in raw_env_names if str(name).strip()]
        elif raw_env_names:
            candidates = [str(raw_env_names)]
        else:
            candidates = []

        base_url_env = provider_cfg.get("base_url_env")
        if base_url_env:
            candidates.append(str(base_url_env))

        for candidate in candidates:
            if candidate not in names:
                names.append(candidate)
    return names


def hydrate_runtime_env(models_config: dict, cwd: str | None = None) -> dict[str, str]:
    """Populate missing process env vars from Prax-friendly env files.

    Precedence:
      1. Existing process environment
      2. ~/.prax/.env
      3. {cwd}/.env
      4. {cwd}/.prax/.env
    """
    env_names = collect_runtime_env_names(models_config)
    if not env_names:
        return {}

    current_dir = Path(cwd or Path.cwd())
    overrides, sources = _load_env_file_overrides(current_dir)
    loaded: dict[str, str] = {}

    for name in env_names:
        if os.environ.get(name, ""):
            continue
        value = overrides.get(name, "")
        if not value:
            continue
        os.environ[name] = value
        loaded[name] = sources.get(name, "")

    return loaded


def _load_env_file_overrides(cwd: Path) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for path in _env_file_candidates(cwd):
        if not path.exists():
            continue
        for key, value in _parse_env_file(path).items():
            values[key] = value
            sources[key] = str(path)
    return values, sources


def _env_file_candidates(cwd: Path) -> list[Path]:
    return [
        Path.home() / ".prax" / ".env",
        cwd / ".env",
        cwd / ".prax" / ".env",
    ]


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        item = _parse_env_line(raw_line)
        if item is None:
            continue
        key, value = item
        parsed[key] = value
    return parsed


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if stripped.startswith("export "):
        stripped = stripped[len("export "):].strip()

    if "=" not in stripped:
        return None

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None

    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value
