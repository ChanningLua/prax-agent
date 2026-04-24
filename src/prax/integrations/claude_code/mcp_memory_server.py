# -*- coding: utf-8 -*-
"""MCP Memory Server — exposes prax.core.memory backend via MCP tools.

Runs as a stdio MCP server that Claude Code can spawn as a subprocess.
All tools operate on the current working directory (cwd) which defaults
to the directory where the server was started.

Usage::

    python3 -m prax.integrations.claude_code.mcp_memory_server
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from prax.core.memory import (
    Experience,
    Fact,
    MemoryContext,
    get_memory_backend,
    migrate_facts_to_kg,
)

mcp = FastMCP("prax-memory")

_cwd: str = os.getcwd()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backend():
    return get_memory_backend()


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def memory_store_fact(
    content: str,
    category: str = "context",
    confidence: float = 0.9,
    source: str = "claude",
) -> str:
    """Store a fact about the current project.

    Categories: preference, knowledge, context, behavior, goal, correction.
    """
    fact = Fact(
        id=f"fact_{uuid.uuid4().hex[:8]}",
        content=content,
        category=category,
        confidence=confidence,
        created_at=_now_iso(),
        source=source,
    )
    await _backend().store_fact(_cwd, fact)
    return json.dumps({"stored": True, "id": fact.id})


@mcp.tool()
async def memory_get_facts(limit: int = 20) -> str:
    """Retrieve stored facts about the current project, sorted by confidence."""
    facts = await _backend().get_facts(_cwd, limit=limit)
    return json.dumps([f.to_dict() for f in facts], ensure_ascii=False)


@mcp.tool()
async def memory_delete_fact(fact_id: str) -> str:
    """Delete a fact by its ID."""
    await _backend().delete_fact(_cwd, fact_id)
    return json.dumps({"deleted": True, "id": fact_id})


@mcp.tool()
async def memory_get_context() -> str:
    """Get the structured project context (work_context + top_of_mind)."""
    ctx = await _backend().get_context(_cwd)
    return json.dumps({
        "work_context": ctx.work_context,
        "top_of_mind": ctx.top_of_mind,
        "updated_at": ctx.updated_at,
    }, ensure_ascii=False)


@mcp.tool()
async def memory_save_context(
    work_context: str = "",
    top_of_mind: str = "",
) -> str:
    """Save structured project context. Empty strings are ignored (not overwritten)."""
    ctx = MemoryContext(
        work_context=work_context,
        top_of_mind=top_of_mind,
    )
    await _backend().save_context(_cwd, ctx)
    return json.dumps({"saved": True})


@mcp.tool()
async def memory_search(keyword: str, limit: int = 20) -> str:
    """Search facts by keyword (case-insensitive substring match)."""
    all_facts = await _backend().get_facts(_cwd, limit=200)
    kw = keyword.lower()
    matched = [f for f in all_facts if kw in f.content.lower()][:limit]
    return json.dumps([f.to_dict() for f in matched], ensure_ascii=False)


@mcp.tool()
async def memory_get_experiences(task_type: str = "general", limit: int = 10) -> str:
    """Get cross-project experiences relevant to a task type."""
    exps = await _backend().get_experiences(task_type, limit=limit)
    return json.dumps([e.to_dict() for e in exps], ensure_ascii=False)


@mcp.tool()
async def memory_store_experience(
    task_type: str,
    context: str,
    insight: str,
    outcome: str = "completed",
    tags: list[str] | None = None,
) -> str:
    """Store a cross-project experience record."""
    exp = Experience(
        id=f"exp_{uuid.uuid4().hex[:8]}",
        task_type=task_type,
        context=context,
        insight=insight,
        outcome=outcome,
        tags=tags or [],
        timestamp=_now_iso(),
        project=os.path.basename(_cwd),
    )
    await _backend().store_experience(exp)
    return json.dumps({"stored": True, "id": exp.id})


@mcp.tool()
async def memory_format_prompt(task_type: str = "general", max_facts: int = 15) -> str:
    """Format memory content as a markdown prompt injection section."""
    return await _backend().format_for_prompt(_cwd, task_type=task_type, max_facts=max_facts)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
