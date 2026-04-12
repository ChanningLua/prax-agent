"""Unit tests for McpTool.

The async call_fn is mocked — no real MCP server connection.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from prax.tools.mcp_tool import McpTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


def _tool(call_fn=None, *, server="myserver", tool_name="search", description="Search tool"):
    if call_fn is None:
        call_fn = AsyncMock(return_value="default result")
    return McpTool(server, tool_name, description, _SCHEMA, call_fn)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_mcp_tool_name():
    tool = _tool(server="srv", tool_name="do_thing")
    assert tool.name == "srv__do_thing"


def test_mcp_tool_description_prefixed():
    tool = _tool(server="srv", description="Runs a query")
    assert tool.description == "[MCP:srv] Runs a query"


# ---------------------------------------------------------------------------
# execute — success with string result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_returns_string_result():
    call_fn = AsyncMock(return_value="search results here")
    tool = _tool(call_fn=call_fn)
    result = await tool.execute({"query": "prax"})
    assert not result.is_error
    assert result.content == "search results here"
    call_fn.assert_awaited_once_with({"query": "prax"})


# ---------------------------------------------------------------------------
# execute — success with dict result (JSON-encoded)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_returns_dict_as_json():
    payload = {"items": [1, 2, 3], "total": 3}
    call_fn = AsyncMock(return_value=payload)
    tool = _tool(call_fn=call_fn)
    result = await tool.execute({"query": "items"})
    assert not result.is_error
    decoded = json.loads(result.content)
    assert decoded == payload


# ---------------------------------------------------------------------------
# execute — call_fn raises exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_call_fn_raises_is_error():
    call_fn = AsyncMock(side_effect=RuntimeError("server unavailable"))
    tool = _tool(call_fn=call_fn)
    result = await tool.execute({"query": "x"})
    assert result.is_error
    assert "MCP error" in result.content
    assert "server unavailable" in result.content


# ---------------------------------------------------------------------------
# execute — empty params forwarded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_empty_params_forwarded():
    call_fn = AsyncMock(return_value="ok")
    tool = _tool(call_fn=call_fn)
    result = await tool.execute({})
    assert not result.is_error
    call_fn.assert_awaited_once_with({})
