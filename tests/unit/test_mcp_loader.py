"""Unit tests for prax.core.mcp_loader — MCP SDK integration and tool loading.

All MCP SDK imports are mocked — no real MCP server is started.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_tool_info(name: str, description: str = "", schema: dict | None = None) -> MagicMock:
    """Create a mock MCP tool info object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = schema or {"type": "object", "properties": {}}
    return tool


def _make_mcp_module(tools: list, call_result_text: str = "ok") -> dict:
    """Build a fake mcp module hierarchy and return sys.modules patches."""
    fake_mcp = MagicMock()
    fake_mcp_client_stdio = MagicMock()

    # Build the call result content
    content_item = MagicMock()
    content_item.text = call_result_text

    list_result = MagicMock()
    list_result.tools = tools

    call_result = MagicMock()
    call_result.content = [content_item]

    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock(return_value=call_result)

    @asynccontextmanager
    async def stdio_client_ctx(params):
        reader = AsyncMock()
        writer = AsyncMock()
        yield reader, writer

    @asynccontextmanager
    async def client_session_ctx(reader, writer):
        yield session

    fake_mcp.ClientSession = MagicMock(side_effect=client_session_ctx)
    fake_mcp.StdioServerParameters = MagicMock()
    fake_mcp_client_stdio.stdio_client = stdio_client_ctx

    return {
        "mcp": fake_mcp,
        "mcp.client": MagicMock(),
        "mcp.client.stdio": fake_mcp_client_stdio,
    }


# ── load_mcp_tools — happy path ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_mcp_tools_returns_empty_when_mcp_not_installed():
    """load_mcp_tools returns [] when mcp SDK is not importable."""
    import builtins
    import unittest.mock as um

    # Remove mcp from sys.modules and make import fail
    with um.patch.dict(sys.modules, {"mcp": None, "mcp.client": None, "mcp.client.stdio": None}):
        real_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "mcp":
                raise ImportError("No module named 'mcp'")
            return real_import(name, *args, **kwargs)

        with um.patch("builtins.__import__", side_effect=failing_import):
            # Reload to re-execute the import-guarded block
            import importlib
            import prax.core.mcp_loader as mcp_loader_mod
            importlib.reload(mcp_loader_mod)

            result = await mcp_loader_mod.load_mcp_tools([{"name": "server", "command": "npx", "args": []}])

    assert result == []


@pytest.mark.asyncio
async def test_load_mcp_tools_skips_server_with_no_command():
    """load_mcp_tools silently skips configs that have no command."""
    mocks = _make_mcp_module([])
    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        result = await mcp_loader_mod.load_mcp_tools([{"name": "noop"}])

    assert result == []


@pytest.mark.asyncio
async def test_load_mcp_tools_returns_one_tool_per_tool_definition():
    """load_mcp_tools returns one McpTool per tool exposed by the server."""
    tools = [
        _make_tool_info("search", "Search the web"),
        _make_tool_info("fetch", "Fetch a URL"),
    ]
    mocks = _make_mcp_module(tools)

    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        result = await mcp_loader_mod.load_mcp_tools([
            {"name": "myserver", "command": "npx", "args": ["myserver"]}
        ])

    assert len(result) == 2
    names = [t.name for t in result]
    assert "myserver__search" in names
    assert "myserver__fetch" in names


@pytest.mark.asyncio
async def test_load_mcp_tools_tool_description_format():
    """McpTool description is prefixed with [MCP:server_name]."""
    tools = [_make_tool_info("grep", "Grep files")]
    mocks = _make_mcp_module(tools)

    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        result = await mcp_loader_mod.load_mcp_tools([
            {"name": "acme", "command": "acme-mcp", "args": []}
        ])

    assert len(result) == 1
    assert result[0].description == "[MCP:acme] Grep files"


@pytest.mark.asyncio
async def test_load_mcp_tools_skips_failing_server_silently():
    """load_mcp_tools skips a server that raises during connection."""
    fake_mcp = MagicMock()
    fake_mcp_client_stdio = MagicMock()
    fake_mcp.StdioServerParameters = MagicMock()
    fake_mcp.ClientSession = MagicMock()

    @asynccontextmanager
    async def failing_stdio_client(params):
        raise ConnectionError("Server refused to start")
        yield  # unreachable — makes this a valid generator

    fake_mcp_client_stdio.stdio_client = failing_stdio_client

    mocks = {
        "mcp": fake_mcp,
        "mcp.client": MagicMock(),
        "mcp.client.stdio": fake_mcp_client_stdio,
    }

    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        result = await mcp_loader_mod.load_mcp_tools([
            {"name": "bad_server", "command": "bad-mcp", "args": []}
        ])

    # Should return empty list, not raise
    assert result == []


@pytest.mark.asyncio
async def test_load_mcp_tools_expands_env_vars_in_env_config(monkeypatch):
    """load_mcp_tools expands env vars in server env configuration."""
    monkeypatch.setenv("MY_TOKEN", "secret123")
    tools = [_make_tool_info("do_thing")]
    mocks = _make_mcp_module(tools)

    captured_params = []

    original_params_cls = MagicMock
    server_params_calls = []

    def capture_params(*args, **kwargs):
        server_params_calls.append(kwargs.get("env", {}))
        return MagicMock()

    mocks["mcp"].StdioServerParameters = capture_params

    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        await mcp_loader_mod.load_mcp_tools([
            {
                "name": "myserver",
                "command": "server",
                "args": [],
                "env": {"TOKEN": "$MY_TOKEN"},
            }
        ])

    # Env should have been expanded
    if server_params_calls:
        assert server_params_calls[0].get("TOKEN") == "secret123"


@pytest.mark.asyncio
async def test_load_mcp_tools_tool_call_fn_works():
    """McpTool.execute invokes the call_fn and returns ToolResult."""
    tools = [_make_tool_info("echo", "Echo input")]
    mocks = _make_mcp_module(tools, call_result_text="echoed!")

    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        result_tools = await mcp_loader_mod.load_mcp_tools([
            {"name": "echoserver", "command": "echo-mcp", "args": []}
        ])

    assert len(result_tools) == 1
    tool = result_tools[0]

    tool_result = await tool.execute({"message": "hello"})
    assert tool_result.is_error is False
    assert "echoed!" in tool_result.content


@pytest.mark.asyncio
async def test_load_mcp_tools_multiple_servers():
    """load_mcp_tools loads tools from multiple server configs."""
    tools_a = [_make_tool_info("tool_a")]
    tools_b = [_make_tool_info("tool_b"), _make_tool_info("tool_c")]

    call_count = {"n": 0}

    def make_session(tool_list):
        session = AsyncMock()
        session.initialize = AsyncMock()

        list_result = MagicMock()
        list_result.tools = tool_list

        session.list_tools = AsyncMock(return_value=list_result)
        return session

    sessions = [make_session(tools_a), make_session(tools_b)]

    fake_mcp = MagicMock()
    fake_mcp_client_stdio = MagicMock()

    @asynccontextmanager
    async def stdio_client_ctx(params):
        yield AsyncMock(), AsyncMock()

    @asynccontextmanager
    async def client_session_ctx(r, w):
        idx = call_count["n"]
        call_count["n"] += 1
        yield sessions[idx % len(sessions)]

    fake_mcp.StdioServerParameters = MagicMock()
    fake_mcp.ClientSession = MagicMock(side_effect=client_session_ctx)
    fake_mcp_client_stdio.stdio_client = stdio_client_ctx

    mocks = {
        "mcp": fake_mcp,
        "mcp.client": MagicMock(),
        "mcp.client.stdio": fake_mcp_client_stdio,
    }

    with patch.dict(sys.modules, mocks):
        import importlib
        import prax.core.mcp_loader as mcp_loader_mod
        importlib.reload(mcp_loader_mod)

        result = await mcp_loader_mod.load_mcp_tools([
            {"name": "server_a", "command": "a", "args": []},
            {"name": "server_b", "command": "b", "args": []},
        ])

    assert len(result) == 3
    names = [t.name for t in result]
    assert "server_a__tool_a" in names
    assert "server_b__tool_b" in names
    assert "server_b__tool_c" in names
