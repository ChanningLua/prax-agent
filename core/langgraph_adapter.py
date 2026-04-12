"""LangGraph adapter — wrap Prax agent loop as a compiled LangGraph graph.

Provides create_langgraph_agent() which returns a compiled StateGraph.
This enables:
  - LangGraph Studio visualization
  - Standard checkpointers (SQLite, Postgres)
  - Streaming via .astream()
  - Compatibility with LangGraph ecosystem tooling

Usage:
    from prax.core.langgraph_adapter import create_langgraph_agent

    graph = create_langgraph_agent(tools=tools, llm_client=client, model_config=cfg)
    async for event in graph.astream({"messages": [{"role": "user", "content": "..."}]}):
        print(event)
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import StateGraph, END
    from typing import TypedDict, Annotated
    import operator
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False


def is_available() -> bool:
    return _LANGGRAPH_AVAILABLE


def create_langgraph_agent(
    *,
    tools: list[Any],
    llm_client: Any,
    model_config: Any,
    max_iterations: int = 25,
    system_prompt: str = "",
) -> Any:
    """Return a compiled LangGraph StateGraph that wraps the Prax agent loop.

    Args:
        tools: List of Prax Tool instances.
        llm_client: Prax LLMClient instance.
        model_config: ModelConfig with .name attribute.
        max_iterations: Hard cap on tool-call iterations.
        system_prompt: Optional system prompt override.

    Returns:
        Compiled LangGraph graph, or None if langgraph is not installed.
    """
    if not _LANGGRAPH_AVAILABLE:
        logger.warning(
            "langgraph not installed — create_langgraph_agent() returns None. "
            "Install with: pip install langgraph"
        )
        return None

    from typing import TypedDict, Annotated
    import operator

    class AgentState(TypedDict):
        messages: Annotated[list[dict], operator.add]
        iteration: int
        completed: bool

    tool_map = {t.name: t for t in tools}
    tool_schemas = [t.to_claude_format() for t in tools]

    async def call_model(state: AgentState) -> AgentState:
        """Invoke the LLM with current message history."""
        msgs = state["messages"]
        if system_prompt and (not msgs or msgs[0].get("role") != "system"):
            msgs = [{"role": "system", "content": system_prompt}] + msgs

        response = await llm_client.complete(
            messages=msgs,
            model=model_config.name,
            tools=tool_schemas,
        )

        # Normalize response to dict
        if hasattr(response, "to_dict"):
            resp_dict = response.to_dict()
        else:
            resp_dict = {"role": "assistant", "content": str(response)}

        return {
            "messages": [resp_dict],
            "iteration": state["iteration"],
            "completed": state["completed"],
        }

    async def execute_tools(state: AgentState) -> AgentState:
        """Execute all tool calls from the last assistant message."""
        last = state["messages"][-1]
        tool_calls = _extract_tool_calls(last)

        if not tool_calls:
            return {**state, "completed": True}

        results = []
        for tc in tool_calls:
            tool = tool_map.get(tc.get("name", ""))
            if tool is None:
                results.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": f"Unknown tool: {tc.get('name')}",
                })
                continue
            try:
                result = await tool.execute(tc.get("input", {}))
                results.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result.content,
                    "is_error": result.is_error,
                })
            except Exception as exc:
                results.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": f"Tool error: {exc}",
                    "is_error": True,
                })

        return {
            "messages": results,
            "iteration": state["iteration"] + 1,
            "completed": False,
        }

    def should_continue(state: AgentState) -> str:
        if state.get("completed"):
            return "end"
        if state["iteration"] >= max_iterations:
            logger.warning("LangGraph agent hit max_iterations=%d", max_iterations)
            return "end"
        last = state["messages"][-1] if state["messages"] else {}
        if _extract_tool_calls(last):
            return "tools"
        return "end"

    # Build graph
    workflow = StateGraph(AgentState)
    workflow.add_node("model", call_model)
    workflow.add_node("tools", execute_tools)
    workflow.set_entry_point("model")
    workflow.add_conditional_edges(
        "model",
        should_continue,
        {"tools": "tools", "end": END},
    )
    workflow.add_edge("tools", "model")

    return workflow.compile()


def _extract_tool_calls(message: dict) -> list[dict]:
    """Extract tool calls from an assistant message (handles multiple formats)."""
    # Anthropic format: content is a list of blocks
    content = message.get("content", "")
    if isinstance(content, list):
        return [
            {"id": b.get("id", ""), "name": b.get("name", ""), "input": b.get("input", {})}
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
    # OpenAI format: tool_calls list
    tool_calls = message.get("tool_calls", [])
    if tool_calls:
        result = []
        for tc in tool_calls:
            import json as _json
            fn = tc.get("function", {})
            try:
                inp = _json.loads(fn.get("arguments", "{}"))
            except Exception:
                inp = {}
            result.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "input": inp})
        return result
    return []
