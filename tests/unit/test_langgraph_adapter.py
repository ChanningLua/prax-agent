"""Unit tests for LangGraph adapter (prax/core/langgraph_adapter.py).

Uses patch.dict(sys.modules) to simulate langgraph being installed or absent.
No network, no Docker, no real LLM calls.
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fake_langgraph():
    """Return a fake langgraph module hierarchy."""
    lg = types.ModuleType("langgraph")
    lg_graph = types.ModuleType("langgraph.graph")

    END_sentinel = object()

    class FakeStateGraph:
        def __init__(self, state_schema: Any = None) -> None:
            self._nodes: dict[str, Any] = {}
            self._entry: str | None = None
            self._edges: list[tuple[str, str]] = []
            self._cond_edges: list[Any] = []

        def add_node(self, name: str, fn: Any) -> None:
            self._nodes[name] = fn

        def set_entry_point(self, name: str) -> None:
            self._entry = name

        def add_conditional_edges(self, src: str, fn: Any, mapping: dict) -> None:
            self._cond_edges.append((src, fn, mapping))

        def add_edge(self, src: str, dst: str) -> None:
            self._edges.append((src, dst))

        def compile(self) -> "FakeCompiledGraph":
            return FakeCompiledGraph(self._nodes)

    class FakeCompiledGraph:
        def __init__(self, nodes: dict) -> None:
            self.nodes = nodes

        async def astream(self, input: Any):
            yield {"messages": []}

    lg_graph.StateGraph = FakeStateGraph  # type: ignore[attr-defined]
    lg_graph.END = END_sentinel  # type: ignore[attr-defined]
    lg.graph = lg_graph  # type: ignore[attr-defined]
    return lg, lg_graph


# ── is_available ─────────────────────────────────────────────────────────────


def test_is_available_when_langgraph_installed() -> None:
    lg, lg_graph = _make_fake_langgraph()

    with patch.dict(sys.modules, {"langgraph": lg, "langgraph.graph": lg_graph}):
        # Force reimport to pick up mocked modules
        if "prax.core.langgraph_adapter" in sys.modules:
            del sys.modules["prax.core.langgraph_adapter"]

        import importlib
        adapter = importlib.import_module("prax.core.langgraph_adapter")
        # Patch the flag directly since module was already imported
        with patch.object(adapter, "_LANGGRAPH_AVAILABLE", True):
            assert adapter.is_available() is True


def test_is_available_when_langgraph_not_installed() -> None:
    if "prax.core.langgraph_adapter" in sys.modules:
        del sys.modules["prax.core.langgraph_adapter"]

    with patch.dict(sys.modules, {"langgraph": None, "langgraph.graph": None}):
        import importlib
        adapter = importlib.import_module("prax.core.langgraph_adapter")
        with patch.object(adapter, "_LANGGRAPH_AVAILABLE", False):
            assert adapter.is_available() is False


# ── create_langgraph_agent ────────────────────────────────────────────────────


def test_create_langgraph_agent_returns_none_when_unavailable() -> None:
    from prax.core.langgraph_adapter import create_langgraph_agent
    with patch("prax.core.langgraph_adapter._LANGGRAPH_AVAILABLE", False):
        result = create_langgraph_agent(
            tools=[],
            llm_client=MagicMock(),
            model_config=MagicMock(name="gpt-4"),
        )
    assert result is None


def test_create_langgraph_agent_returns_compiled_graph() -> None:
    lg, lg_graph = _make_fake_langgraph()

    with patch("prax.core.langgraph_adapter._LANGGRAPH_AVAILABLE", True):
        with patch.dict(sys.modules, {"langgraph": lg, "langgraph.graph": lg_graph}):
            with patch("prax.core.langgraph_adapter.StateGraph", lg_graph.StateGraph):
                with patch("prax.core.langgraph_adapter.END", lg_graph.END):
                    from prax.core.langgraph_adapter import create_langgraph_agent

                    mock_tool = MagicMock()
                    mock_tool.name = "Bash"
                    mock_tool.to_claude_format.return_value = {"name": "Bash"}

                    mock_client = MagicMock()
                    mock_cfg = MagicMock()
                    mock_cfg.name = "gpt-4"

                    result = create_langgraph_agent(
                        tools=[mock_tool],
                        llm_client=mock_client,
                        model_config=mock_cfg,
                    )
                    assert result is not None


# ── _extract_tool_calls ───────────────────────────────────────────────────────


def test_extract_tool_calls_anthropic_format() -> None:
    from prax.core.langgraph_adapter import _extract_tool_calls

    message = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "thinking..."},
            {"type": "tool_use", "id": "tc1", "name": "Read", "input": {"path": "/foo"}},
        ],
    }
    calls = _extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0]["name"] == "Read"
    assert calls[0]["id"] == "tc1"
    assert calls[0]["input"] == {"path": "/foo"}


def test_extract_tool_calls_openai_format() -> None:
    import json
    from prax.core.langgraph_adapter import _extract_tool_calls

    message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "tc2",
                "function": {
                    "name": "Bash",
                    "arguments": json.dumps({"command": "ls"}),
                },
            }
        ],
    }
    calls = _extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0]["name"] == "Bash"
    assert calls[0]["input"] == {"command": "ls"}


def test_extract_tool_calls_plain_text_message() -> None:
    from prax.core.langgraph_adapter import _extract_tool_calls

    message = {"role": "assistant", "content": "Just a text reply."}
    calls = _extract_tool_calls(message)
    assert calls == []


def test_extract_tool_calls_empty_content_list() -> None:
    from prax.core.langgraph_adapter import _extract_tool_calls

    message = {"role": "assistant", "content": [{"type": "text", "text": "no tools"}]}
    calls = _extract_tool_calls(message)
    assert calls == []


# ── call_model (inside create_langgraph_agent) ────────────────────────────────


@pytest.mark.asyncio
async def test_call_model_invokes_llm_client() -> None:
    """call_model node should call llm_client.complete and return updated state."""
    lg, lg_graph = _make_fake_langgraph()

    with patch("prax.core.langgraph_adapter._LANGGRAPH_AVAILABLE", True):
        with patch.dict(sys.modules, {"langgraph": lg, "langgraph.graph": lg_graph}):
            with patch("prax.core.langgraph_adapter.StateGraph", lg_graph.StateGraph):
                with patch("prax.core.langgraph_adapter.END", lg_graph.END):
                    from prax.core.langgraph_adapter import create_langgraph_agent

                    mock_response = MagicMock()
                    mock_response.to_dict.return_value = {
                        "role": "assistant", "content": "hello"
                    }

                    mock_client = MagicMock()
                    mock_client.complete = AsyncMock(return_value=mock_response)

                    mock_cfg = MagicMock()
                    mock_cfg.name = "gpt-4"

                    graph = create_langgraph_agent(
                        tools=[],
                        llm_client=mock_client,
                        model_config=mock_cfg,
                    )

                    # Retrieve the call_model node function from compiled graph
                    call_model_fn = graph.nodes["model"]
                    state = {
                        "messages": [{"role": "user", "content": "hi"}],
                        "iteration": 0,
                        "completed": False,
                    }
                    result = await call_model_fn(state)

                    mock_client.complete.assert_called_once()
                    assert "messages" in result


# ── execute_tools — unknown tool ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tools_unknown_tool_returns_error() -> None:
    lg, lg_graph = _make_fake_langgraph()

    with patch("prax.core.langgraph_adapter._LANGGRAPH_AVAILABLE", True):
        with patch.dict(sys.modules, {"langgraph": lg, "langgraph.graph": lg_graph}):
            with patch("prax.core.langgraph_adapter.StateGraph", lg_graph.StateGraph):
                with patch("prax.core.langgraph_adapter.END", lg_graph.END):
                    from prax.core.langgraph_adapter import create_langgraph_agent

                    mock_client = MagicMock()
                    mock_cfg = MagicMock()
                    mock_cfg.name = "gpt-4"

                    graph = create_langgraph_agent(
                        tools=[],
                        llm_client=mock_client,
                        model_config=mock_cfg,
                    )

                    execute_tools_fn = graph.nodes["tools"]
                    state = {
                        "messages": [{
                            "role": "assistant",
                            "content": [
                                {"type": "tool_use", "id": "t1", "name": "MissingTool", "input": {}}
                            ],
                        }],
                        "iteration": 0,
                        "completed": False,
                    }

                    result = await execute_tools_fn(state)
                    # Should have a tool result with an error
                    assert any(
                        "Unknown tool" in m.get("content", "")
                        for m in result.get("messages", [])
                    )


# ── execute_tools — no tool calls → completed ─────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tools_no_tool_calls_sets_completed() -> None:
    lg, lg_graph = _make_fake_langgraph()

    with patch("prax.core.langgraph_adapter._LANGGRAPH_AVAILABLE", True):
        with patch.dict(sys.modules, {"langgraph": lg, "langgraph.graph": lg_graph}):
            with patch("prax.core.langgraph_adapter.StateGraph", lg_graph.StateGraph):
                with patch("prax.core.langgraph_adapter.END", lg_graph.END):
                    from prax.core.langgraph_adapter import create_langgraph_agent

                    mock_client = MagicMock()
                    mock_cfg = MagicMock()
                    mock_cfg.name = "gpt-4"

                    graph = create_langgraph_agent(
                        tools=[],
                        llm_client=mock_client,
                        model_config=mock_cfg,
                    )

                    execute_tools_fn = graph.nodes["tools"]
                    state = {
                        "messages": [{"role": "assistant", "content": "plain text"}],
                        "iteration": 0,
                        "completed": False,
                    }

                    result = await execute_tools_fn(state)
                    assert result["completed"] is True
