"""MCP loader — connects to MCP servers and wraps their tools as Prax Tools."""

from __future__ import annotations

import os
from typing import Any

from ..tools.mcp_tool import McpTool


def _expand_env(value: str) -> str:
    return os.path.expandvars(value)


async def load_mcp_tools(server_configs: list[dict]) -> list[McpTool]:
    """Connect to each MCP server and return wrapped tools.

    Requires `mcp` SDK: pip install mcp
    Silently skips servers that fail to connect.
    """
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return []

    tools: list[McpTool] = []

    for cfg in server_configs:
        name = cfg.get("name", "mcp")
        command = cfg.get("command", "")
        args = cfg.get("args", [])
        env_raw = cfg.get("env", {})

        if not command:
            continue

        env = {k: _expand_env(str(v)) for k, v in env_raw.items()}

        try:
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env or None,
            )
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    for tool in result.tools:
                        schema = tool.inputSchema if hasattr(tool, "inputSchema") else {
                            "type": "object", "properties": {}, "additionalProperties": True
                        }

                        # Capture loop variables for closure
                        _session_cmd = command
                        _session_args = args
                        _session_env = env
                        _tool_name = tool.name

                        async def _call(
                            params: dict[str, Any],
                            _cmd: str = _session_cmd,
                            _a: list = _session_args,
                            _e: dict = _session_env,
                            _tn: str = _tool_name,
                        ) -> str:
                            sp = StdioServerParameters(command=_cmd, args=_a, env=_e or None)
                            async with stdio_client(sp) as (r, w):
                                async with ClientSession(r, w) as s:
                                    await s.initialize()
                                    res = await s.call_tool(_tn, params)
                                    parts = []
                                    for c in res.content:
                                        if hasattr(c, "text"):
                                            parts.append(c.text)
                                        else:
                                            parts.append(str(c))
                                    return "\n".join(parts)

                        tools.append(McpTool(
                            server_name=name,
                            tool_name=tool.name,
                            description=tool.description or "",
                            input_schema=schema,
                            call_fn=_call,
                        ))
        except Exception:
            continue

    return tools
