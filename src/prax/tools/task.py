"""Task tool — delegate work to an isolated subagent run."""

from __future__ import annotations

from typing import Awaitable, Callable

from .base import PermissionLevel, Tool, ToolResult


TaskExecutor = Callable[[str, str, str, int | None, list[str] | None], Awaitable[str]]

VALID_SUBAGENT_TYPES: frozenset[str] = frozenset({"general-purpose", "plan", "explore", "code"})


class TaskTool(Tool):
    name = "Task"
    description = (
        "Delegate a complex task to a specialized subagent that runs in isolated context. "
        "Use for multi-step work, verbose exploration, or parallelizable subtasks."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "subagent_type": {
                "type": "string",
                "enum": sorted(VALID_SUBAGENT_TYPES),
                "default": "general-purpose",
            },
            "max_turns": {"type": "integer", "minimum": 1},
            "load_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of skill names to inject into the subagent prompt.",
            },
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(self, *, executor: TaskExecutor):
        self._executor = executor

    async def execute(self, params: dict[str, object]) -> ToolResult:
        description = params.get("description")
        prompt = params.get("prompt")
        subagent_type = params.get("subagent_type", "general-purpose")
        max_turns = params.get("max_turns")
        load_skills = params.get("load_skills")

        if not isinstance(description, str) or not description.strip():
            return ToolResult(content="Error: description must be a non-empty string", is_error=True)
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(content="Error: prompt must be a non-empty string", is_error=True)
        if not isinstance(subagent_type, str) or subagent_type not in VALID_SUBAGENT_TYPES:
            return ToolResult(
                content=f"Error: subagent_type must be one of {sorted(VALID_SUBAGENT_TYPES)}",
                is_error=True,
            )
        if max_turns is not None and (not isinstance(max_turns, int) or max_turns < 1):
            return ToolResult(content="Error: max_turns must be a positive integer", is_error=True)
        if load_skills is not None and not isinstance(load_skills, list):
            load_skills = None

        result = await self._executor(description, prompt, subagent_type, max_turns, load_skills)
        return ToolResult(content=result)
