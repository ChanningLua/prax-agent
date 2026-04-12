"""GetSkillTool — restricted skill content reader for the agent loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..core.skills_loader import SkillIndex


class GetSkillTool(Tool):
    name = "GetSkill"
    description = "Read the full content of a skill by name."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "Exact skill name"}
        },
        "required": ["skill_name"],
    }

    def __init__(self, index: "SkillIndex"):
        self._index = index

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        skill = self._index.get(params["skill_name"])
        if skill is None:
            available = ", ".join(self._index.list_names())
            return ToolResult(
                content=f"Skill '{params['skill_name']}' not found. Available: {available}",
                is_error=True,
            )
        return ToolResult(content=skill.content)
