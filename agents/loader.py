"""Agent loader — load Agent definitions from Markdown + YAML frontmatter files.

Each .md file under .prax/agents/ defines one Agent:

    ---
    name: planner
    description: Strategic planning specialist
    model: claude-opus-4-6
    tools:
      - TodoWrite
      - Task
      - Read
    max_iterations: 15
    ---

    # Planner Agent
    ...system prompt markdown...

The registry selects the best Agent for a given task via keyword matching.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class AgentDefinition:
    name: str
    description: str
    model: str
    tools: list[str]
    max_iterations: int
    system_prompt: str
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, path: Path) -> "AgentDefinition":
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError(f"Missing YAML frontmatter in {path}")

        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Malformed frontmatter in {path}")

        meta = yaml.safe_load(parts[1]) or {}
        system_prompt = parts[2].strip()

        return cls(
            name=meta["name"],
            description=meta.get("description", ""),
            model=meta.get("model", "claude-sonnet-4-6"),
            tools=meta.get("tools", []),
            max_iterations=int(meta.get("max_iterations", 25)),
            system_prompt=system_prompt,
            keywords=meta.get("keywords", []),
        )


# Default keyword → agent routing table
_ROUTING: list[tuple[list[str], str]] = [
    (["plan", "design", "architect", "strategy", "breakdown", "split"], "planner"),
    (["review", "quality", "lint", "smell", "refactor"], "code-reviewer"),
    (["test", "tdd", "coverage", "spec", "unit", "integration"], "tdd-guide"),
    (["security", "vulnerability", "injection", "xss", "auth", "cve"], "security-reviewer"),
    (["architect", "structure", "pattern", "module", "layer"], "architect"),
    (["build", "compile", "error", "fix", "broken", "fail"], "build-error-resolver"),
    (["performance", "slow", "optimize", "profile", "latency"], "performance-optimizer"),
    (["doc", "readme", "comment", "changelog", "wiki"], "documentation-writer"),
    (["database", "sql", "query", "schema", "migration", "orm"], "database-expert"),
    (["frontend", "ui", "css", "react", "vue", "component"], "frontend-specialist"),
    (["api", "endpoint", "rest", "graphql", "openapi", "swagger"], "api-designer"),
    (["deploy", "ci", "cd", "docker", "k8s", "devops", "pipeline"], "devops-engineer"),
    (["bug", "debug", "trace", "reproduce", "crash", "exception"], "bug-hunter"),
    (["dependency", "package", "upgrade", "version", "npm", "pip"], "dependency-manager"),
]


class AgentRegistry:
    """Load and query Agent definitions from a directory of Markdown files."""

    def __init__(self, agents_dir: Path):
        self._agents: dict[str, AgentDefinition] = {}
        self._load(agents_dir)

    def _load(self, agents_dir: Path) -> None:
        if not agents_dir.exists():
            logger.debug("Agents dir not found: %s", agents_dir)
            return
        for md in sorted(agents_dir.glob("*.md")):
            try:
                defn = AgentDefinition.from_markdown(md)
                self._agents[defn.name] = defn
                logger.debug("Loaded agent: %s", defn.name)
            except Exception as exc:
                logger.warning("Failed to load agent %s: %s", md.name, exc)

    def get(self, name: str) -> AgentDefinition | None:
        return self._agents.get(name)

    def get_by_name(self, name: str) -> AgentDefinition | None:
        """Return agent definition by exact name match (alias for get)."""
        return self._agents.get(name)

    def list_all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def select_for_task(self, task: str) -> AgentDefinition | None:
        """Return the best-matching Agent for the given task description."""
        task_lower = task.lower()

        # 1. Score all agents by keyword matches (most specific wins)
        best: AgentDefinition | None = None
        best_score = 0
        for defn in self._agents.values():
            score = sum(1 for kw in defn.keywords if kw in task_lower)
            if score > best_score:
                best_score = score
                best = defn

        if best:
            return best

        # 2. Fall back to built-in routing table (stem/prefix matching)
        for keywords, agent_name in _ROUTING:
            if any(kw in task_lower for kw in keywords):
                agent = self._agents.get(agent_name)
                if agent:
                    return agent

        # 3. Default to "ralph" if available
        return self._agents.get("ralph")


_registry_cache: dict[str, AgentRegistry] = {}


def get_agent_registry(cwd: str) -> AgentRegistry:
    """Return (cached) AgentRegistry for the given working directory."""
    if cwd not in _registry_cache:
        agents_dir = Path(cwd) / ".prax" / "agents"
        _registry_cache[cwd] = AgentRegistry(agents_dir)
    return _registry_cache[cwd]
