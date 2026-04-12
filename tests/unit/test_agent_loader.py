"""
Pure unit tests for prax.agents.loader.

Tests AgentDefinition.from_markdown, AgentRegistry, and get_agent_registry.
Uses tmp_path to create markdown files with YAML frontmatter.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from prax.agents.loader import (
    AgentDefinition,
    AgentRegistry,
    get_agent_registry,
)


# ============================================================================
# Helpers
# ============================================================================

def _write_agent_md(path: Path, frontmatter: str, body: str = ""):
    """Write a markdown file with YAML frontmatter."""
    content = f"---\n{frontmatter}\n---\n{body}"
    path.write_text(content, encoding="utf-8")


# ============================================================================
# AgentDefinition.from_markdown
# ============================================================================

def test_from_markdown_minimal(tmp_path):
    """Parse minimal agent definition with required fields only."""
    md = tmp_path / "agent.md"
    _write_agent_md(md, "name: test-agent\ndescription: Test agent")

    defn = AgentDefinition.from_markdown(md)
    assert defn.name == "test-agent"
    assert defn.description == "Test agent"
    assert defn.model == "claude-sonnet-4-6"  # default
    assert defn.tools == []
    assert defn.max_iterations == 25  # default
    assert defn.system_prompt == ""
    assert defn.keywords == []


def test_from_markdown_full(tmp_path):
    """Parse agent definition with all fields."""
    md = tmp_path / "agent.md"
    frontmatter = """name: full-agent
description: Full featured agent
model: claude-3-5-sonnet-20241022
tools:
  - read
  - write
max_iterations: 10
keywords:
  - test
  - demo"""
    body = "# System Prompt\n\nYou are a test agent."
    _write_agent_md(md, frontmatter, body)

    defn = AgentDefinition.from_markdown(md)
    assert defn.name == "full-agent"
    assert defn.description == "Full featured agent"
    assert defn.model == "claude-3-5-sonnet-20241022"
    assert defn.tools == ["read", "write"]
    assert defn.max_iterations == 10
    assert defn.system_prompt == "# System Prompt\n\nYou are a test agent."
    assert defn.keywords == ["test", "demo"]


def test_from_markdown_no_frontmatter(tmp_path):
    """Raise error when frontmatter is missing."""
    md = tmp_path / "agent.md"
    md.write_text("# Just a heading\n\nNo frontmatter here.", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing YAML frontmatter"):
        AgentDefinition.from_markdown(md)


def test_from_markdown_missing_name(tmp_path):
    """Raise KeyError when name field is missing."""
    md = tmp_path / "agent.md"
    _write_agent_md(md, "description: Missing name")

    with pytest.raises(KeyError):
        AgentDefinition.from_markdown(md)


def test_from_markdown_optional_description(tmp_path):
    """Description is optional, defaults to empty string."""
    md = tmp_path / "agent.md"
    _write_agent_md(md, "name: test-agent")

    defn = AgentDefinition.from_markdown(md)
    assert defn.description == ""


def test_from_markdown_empty_tools(tmp_path):
    """Parse agent with empty tools list."""
    md = tmp_path / "agent.md"
    _write_agent_md(md, "name: agent\ndescription: Test\ntools: []")

    defn = AgentDefinition.from_markdown(md)
    assert defn.tools == []


def test_from_markdown_empty_keywords(tmp_path):
    """Parse agent with empty keywords list."""
    md = tmp_path / "agent.md"
    _write_agent_md(md, "name: agent\ndescription: Test\nkeywords: []")

    defn = AgentDefinition.from_markdown(md)
    assert defn.keywords == []


# ============================================================================
# AgentRegistry
# ============================================================================

def test_registry_load_single_agent(tmp_path):
    """Load a single agent from directory."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(agents_dir / "test.md", "name: test\ndescription: Test agent")

    registry = AgentRegistry(agents_dir)
    agents = registry.list_all()
    assert len(agents) == 1
    assert agents[0].name == "test"


def test_registry_load_multiple_agents(tmp_path):
    """Load multiple agents from directory."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(agents_dir / "a.md", "name: agent-a\ndescription: Agent A")
    _write_agent_md(agents_dir / "b.md", "name: agent-b\ndescription: Agent B")
    _write_agent_md(agents_dir / "c.md", "name: agent-c\ndescription: Agent C")

    registry = AgentRegistry(agents_dir)
    agents = registry.list_all()
    assert len(agents) == 3
    names = {a.name for a in agents}
    assert names == {"agent-a", "agent-b", "agent-c"}


def test_registry_get_by_name_found(tmp_path):
    """Get agent by name when it exists."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(agents_dir / "test.md", "name: test-agent\ndescription: Test")

    registry = AgentRegistry(agents_dir)
    agent = registry.get_by_name("test-agent")
    assert agent is not None
    assert agent.name == "test-agent"


def test_registry_get_by_name_not_found(tmp_path):
    """Return None when agent name not found."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(agents_dir / "test.md", "name: test-agent\ndescription: Test")

    registry = AgentRegistry(agents_dir)
    agent = registry.get_by_name("nonexistent")
    assert agent is None


def test_registry_get_agent_by_name(tmp_path):
    """Get agent by name."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(agents_dir / "default.md", "name: default\ndescription: Default agent")

    registry = AgentRegistry(agents_dir)
    agent = registry.get("default")
    assert agent is not None
    assert agent.name == "default"


def test_registry_get_returns_none_for_unknown(tmp_path):
    """Return None when agent name not found via get()."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(agents_dir / "other.md", "name: other\ndescription: Other agent")

    registry = AgentRegistry(agents_dir)
    agent = registry.get("nonexistent")
    assert agent is None


def test_registry_select_for_task_keyword_match(tmp_path):
    """Select agent based on keyword match."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(
        agents_dir / "planner.md",
        "name: planner\ndescription: Planning agent\nkeywords:\n  - plan\n  - design"
    )
    _write_agent_md(
        agents_dir / "coder.md",
        "name: coder\ndescription: Coding agent\nkeywords:\n  - code\n  - implement"
    )

    registry = AgentRegistry(agents_dir)
    agent = registry.select_for_task("I need to plan the architecture")
    assert agent is not None
    assert agent.name == "planner"


def test_registry_select_for_task_no_match(tmp_path):
    """Return None when no agent matches task."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_md(
        agents_dir / "planner.md",
        "name: planner\ndescription: Planning agent\nkeywords:\n  - plan"
    )

    registry = AgentRegistry(agents_dir)
    agent = registry.select_for_task("I need to debug the database")
    assert agent is None


def test_registry_empty_directory(tmp_path):
    """Handle empty agents directory."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    registry = AgentRegistry(agents_dir)
    agents = registry.list_all()
    assert agents == []


# ============================================================================
# get_agent_registry
# ============================================================================

def test_get_agent_registry_caches(tmp_path, monkeypatch):
    """get_agent_registry caches registry per cwd."""
    import prax.agents.loader
    monkeypatch.setattr(prax.agents.loader, "_registry_cache", {})

    agents_dir = tmp_path / ".prax" / "agents"
    agents_dir.mkdir(parents=True)
    _write_agent_md(agents_dir / "test.md", "name: test\ndescription: Test")

    registry1 = get_agent_registry(tmp_path)
    registry2 = get_agent_registry(tmp_path)
    assert registry1 is registry2


def test_get_agent_registry_different_cwd(tmp_path, monkeypatch):
    """get_agent_registry creates separate registries for different cwds."""
    import prax.agents.loader
    monkeypatch.setattr(prax.agents.loader, "_registry_cache", {})

    cwd1 = tmp_path / "project1"
    cwd2 = tmp_path / "project2"

    agents1 = cwd1 / ".prax" / "agents"
    agents2 = cwd2 / ".prax" / "agents"
    agents1.mkdir(parents=True)
    agents2.mkdir(parents=True)

    _write_agent_md(agents1 / "a.md", "name: agent-a\ndescription: A")
    _write_agent_md(agents2 / "b.md", "name: agent-b\ndescription: B")

    registry1 = get_agent_registry(cwd1)
    registry2 = get_agent_registry(cwd2)

    assert registry1 is not registry2
    assert len(registry1.list_all()) == 1
    assert len(registry2.list_all()) == 1
    assert registry1.list_all()[0].name == "agent-a"
    assert registry2.list_all()[0].name == "agent-b"


def test_get_agent_registry_no_agents_dir(tmp_path, monkeypatch):
    """get_agent_registry creates empty registry when .prax/agents missing."""
    import prax.agents.loader
    monkeypatch.setattr(prax.agents.loader, "_registry_cache", {})

    registry = get_agent_registry(tmp_path)
    assert registry.list_all() == []
