"""Helpers for loading Prax config files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .config_merge import load_merged_models_config

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .governance import GovernanceConfig
    from .agent_spec import AgentSpec


CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_models_config(cwd: str | None = None) -> dict:
    current_dir = Path(cwd or Path.cwd())

    # Load built-in global default
    global_cfg_path = CONFIG_DIR / "models.yaml"
    global_cfg: dict = {}
    if global_cfg_path.exists():
        global_cfg = yaml.safe_load(global_cfg_path.read_text(encoding="utf-8")) or {}

    # Load user global config (~/.prax/models.yaml)
    user_cfg_path = Path.home() / ".prax" / "models.yaml"
    if user_cfg_path.exists():
        try:
            user_cfg = yaml.safe_load(user_cfg_path.read_text(encoding="utf-8")) or {}
            global_cfg = load_merged_models_config(global_cfg, user_cfg)
        except Exception as e:
            logger.warning("Failed to load user models config %s: %s", user_cfg_path, e)

    # Load project local config (.prax/models.yaml)
    local_cfg_path = current_dir / ".prax" / "models.yaml"
    if local_cfg_path.exists():
        try:
            local_cfg = yaml.safe_load(local_cfg_path.read_text(encoding="utf-8")) or {}
            return load_merged_models_config(global_cfg, local_cfg)
        except Exception as e:
            logger.warning("Failed to load project models config %s: %s", local_cfg_path, e)

    return global_cfg if global_cfg else {"default_model": "gpt-4.1", "providers": {}}


def load_rules_config(cwd: str | None = None) -> dict:
    current_dir = Path(cwd or Path.cwd())
    local = current_dir / ".prax" / "rules.yaml"
    if local.exists():
        return yaml.safe_load(local.read_text(encoding="utf-8")) or {}

    global_cfg = CONFIG_DIR / "rules.yaml"
    if global_cfg.exists():
        return yaml.safe_load(global_cfg.read_text(encoding="utf-8")) or {}

    return {"rules": [], "tier_models": {}}


def load_mcp_config(cwd: str | None = None) -> list[dict]:
    """Load merged MCP server configurations from user and project config."""
    current_dir = Path(cwd or Path.cwd())
    merged: dict[str, dict] = {}
    order: list[str] = []

    for config_path in (Path.home() / ".prax" / "config.yaml", current_dir / ".prax" / "config.yaml"):
        for server in _read_mcp_servers(config_path):
            key = str(server.get("name") or f"server-{len(order)}")
            if key not in order:
                order.append(key)
            merged[key] = server

    return [merged[key] for key in order if key in merged]


def _read_mcp_servers(config_path: Path) -> list[dict]:
    if not config_path.exists():
        return []
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw_servers = config.get("mcp_servers", []) if config else []
        return [server for server in raw_servers if isinstance(server, dict)]
    except Exception as e:
        logger.warning("Failed to load MCP config %s: %s", config_path, e)
        return []


def load_memory_config(cwd: str | None = None) -> dict:
    """Load memory backend configuration.

    Merge order (lowest → highest priority):
      built-in defaults
      ~/.prax/config.yaml  [memory]
      {cwd}/.prax/config.yaml  [memory]

    Returns a dict suitable for passing to get_memory_backend().
    """
    defaults: dict = {
        "memory": {
            "backend": "local",
            "openviking": {
                "host": "localhost",
                "port": 50051,
                "ping_timeout_seconds": 2.0,
            },
            "local": {
                "max_facts": 100,
                "fact_confidence_threshold": 0.7,
                "max_experiences": 500,
            },
        }
    }

    def _read_memory_section(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return data.get("memory", {})
        except Exception as e:
            logger.warning("Failed to read memory config section %s: %s", path, e)
            return {}

    merged = dict(defaults["memory"])

    # User-global override
    user_section = _read_memory_section(Path.home() / ".prax" / "config.yaml")
    if user_section:
        merged.update({k: v for k, v in user_section.items() if v is not None})

    # Project-local override
    if cwd:
        local_section = _read_memory_section(
            Path(cwd) / ".prax" / "config.yaml"
        )
        if local_section:
            merged.update({k: v for k, v in local_section.items() if v is not None})

    return {"memory": merged}


def load_governance_config(cwd: str | None = None) -> "GovernanceConfig | None":
    """Load GovernanceConfig from .prax/governance.yaml if it exists.

    Returns None when the file is absent (callers use defaults).
    """
    from .governance import GovernanceConfig

    current_dir = Path(cwd or Path.cwd())
    config_path = current_dir / ".prax" / "governance.yaml"
    if not config_path.exists():
        return None
    try:
        return GovernanceConfig.from_yaml(str(config_path))
    except Exception as e:
        logger.warning("Failed to load governance config %s: %s", config_path, e)
        return None


def load_agent_spec(name: str, cwd: str | None = None) -> "AgentSpec | None":
    """Load AgentSpec from .prax/agents/{name}.yaml if it exists."""
    from .agent_spec import AgentSpec

    current_dir = Path(cwd or Path.cwd())
    path = current_dir / ".prax" / "agents" / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        return AgentSpec.from_yaml(str(path))
    except Exception as e:
        logger.warning("Failed to load agent spec %s: %s", path, e)
        return None


def list_agent_specs(cwd: str | None = None) -> list[str]:
    """Return names of all declared agents in .prax/agents/."""
    current_dir = Path(cwd or Path.cwd())
    agents_dir = current_dir / ".prax" / "agents"
    if not agents_dir.exists():
        return []
    return [p.stem for p in sorted(agents_dir.glob("*.yaml"))]
