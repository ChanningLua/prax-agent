"""Declarative Agent specification loaded from .prax/agents/{name}.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentSpec:
    """Declarative specification for an agent."""

    name: str
    description: str = ""
    system_prompt: str = ""
    model: str | None = None
    allowed_tools: list[str] | None = None
    governance: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentSpec":
        return cls(
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            system_prompt=str(d.get("system_prompt", "")),
            model=d.get("model") or None,
            allowed_tools=d.get("allowed_tools") or None,
            governance=d.get("governance") or None,
        )

    @classmethod
    def from_yaml(cls, path: str) -> "AgentSpec":
        import yaml
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)

    def to_governance(self) -> "GovernanceConfig | None":
        from .governance import GovernanceConfig
        if self.governance:
            return GovernanceConfig.from_dict(self.governance)
        return None
