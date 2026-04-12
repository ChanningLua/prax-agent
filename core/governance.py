"""Declarative governance constraints for agent execution.

Centralises budget_tokens, max_iterations, risk_threshold, and
permission settings into a single configuration object.

Load from dict, YAML file, or construct directly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# mtime-based cache: config_path → (mtime, GovernanceConfig)
_gov_cache: dict[str, tuple[float, "GovernanceConfig"]] = {}
_gov_cache_lock = threading.Lock()


@dataclass
class GovernanceConfig:
    """Unified governance constraints for an agent run."""

    max_budget_tokens: int | None = None
    max_iterations: int = 25
    max_tool_calls_per_tool: int | None = None
    risk_threshold: int = 15
    permission_mode: str = "workspace_write"
    require_approval_above_risk: int | None = None
    max_llm_calls_per_minute: int | None = None
    config_version: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GovernanceConfig":
        """Construct from a plain dict (e.g. loaded from YAML)."""
        known = {
            "max_budget_tokens",
            "max_iterations",
            "max_tool_calls_per_tool",
            "risk_threshold",
            "permission_mode",
            "require_approval_above_risk",
            "max_llm_calls_per_minute",
            "config_version",
        }
        kwargs: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in d.items():
            if k in known:
                kwargs[k] = v
            else:
                extra[k] = v
        if extra:
            kwargs["extra"] = extra
        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: str) -> "GovernanceConfig":
        """Load governance config from a YAML file."""
        import yaml

        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        return cls.from_dict(data)

    @classmethod
    def from_file_with_reload(cls, path: str) -> "GovernanceConfig":
        """Return a cached GovernanceConfig, refreshing when the file's mtime changes."""
        p = Path(path)
        try:
            current_mtime = p.stat().st_mtime
        except OSError:
            return cls.from_yaml(path) if p.exists() else cls()

        with _gov_cache_lock:
            cached = _gov_cache.get(path)
            if cached is not None:
                cached_mtime, cfg = cached
                if cached_mtime == current_mtime:
                    return cfg
            cfg = cls.from_yaml(path)
            _gov_cache[path] = (current_mtime, cfg)
            return cfg
