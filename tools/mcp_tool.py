"""MCP tool wrapper — wraps MCP server tools as Prax Tool instances."""

from __future__ import annotations

import json
from typing import Any

from .base import PermissionLevel, Tool, ToolResult


class McpTool(Tool):
    """Wraps a single MCP tool as a Prax Tool."""

    permission_level = PermissionLevel.REVIEW

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        call_fn: Any,  # async callable(tool_name, params) -> str
    ) -> None:
        self.name = f"{server_name}__{tool_name}"
        self.description = f"[MCP:{server_name}] {description}"
        self.input_schema = input_schema
        self._call_fn = call_fn

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        try:
            result = await self._call_fn(params)
            if isinstance(result, str):
                return ToolResult(content=result)
            return ToolResult(content=json.dumps(result, ensure_ascii=False))
        except Exception as e:
            return ToolResult(content=f"MCP error: {e}", is_error=True)
