"""Forked Agent — sub-agent that shares the parent's prompt cache.

A ForkedAgent is a restricted agent launched by the main agent loop to
handle "management" tasks (memory extraction, context compaction, etc.)
that should be cheap (cache-hit) and isolated (limited tool access).

Key properties:
  - Inherits the parent's system prompt verbatim → triggers prompt cache
  - Only allowed to use tools in its whitelist
  - Cannot launch further sub-agents (no nesting)
  - Results are returned synchronously or as background asyncio tasks

Typical usage::

    # Memory extraction after a conversation turn
    agent = ForkedAgent(
        parent_system_prompt=system_prompt,
        allowed_tools=["Write"],          # can only write MEMORY.md
        llm_client=llm_client,
        model_config=model_config,
        tools=all_tools,
    )
    result = await agent.run(
        "Extract key facts from this conversation and update MEMORY.md"
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..tools.base import Tool, ToolCall, ToolResult

logger = logging.getLogger(__name__)

# 全局并发上限：最多 3 个 ForkedAgent 同时运行（防止 rate limit 雪崩）
# 懒加载，避免在模块导入时创建（Python < 3.10 要求在运行中的 event loop 内创建）
_FORKED_SEMAPHORE: asyncio.Semaphore | None = None
_FORKED_TIMEOUT_SECONDS = 900  # 15 分钟


def _get_semaphore() -> asyncio.Semaphore:
    global _FORKED_SEMAPHORE
    if _FORKED_SEMAPHORE is None:
        _FORKED_SEMAPHORE = asyncio.Semaphore(3)
    return _FORKED_SEMAPHORE


class ForkedAgent:
    """A restricted sub-agent that shares the parent system prompt for cache efficiency.

    The parent system prompt is passed verbatim so that the LLM provider
    can serve the system prompt tokens from cache (no re-encoding cost).

    Tool access is restricted to `allowed_tools`.  Any tool call not in
    the whitelist is rejected before execution.
    """

    def __init__(
        self,
        parent_system_prompt: str,
        allowed_tools: list[str],
        llm_client: Any,
        model_config: Any,
        tools: list[Tool],
        max_iterations: int = 5,
    ) -> None:
        self._system_prompt = parent_system_prompt
        self._allowed = set(allowed_tools)
        self._llm_client = llm_client
        self._model_config = model_config
        self._tool_map: dict[str, Tool] = {
            t.name: t for t in tools if t.name in self._allowed
        }
        self._max_iterations = max_iterations

    async def run(
        self,
        task: str,
        extra_context: str | None = None,
    ) -> str:
        """Run the forked agent on `task` and return the final text response."""
        try:
            async with _get_semaphore():
                return await asyncio.wait_for(
                    self._run_inner(task, extra_context),
                    timeout=_FORKED_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            logger.error("ForkedAgent timed out after %ds", _FORKED_TIMEOUT_SECONDS)
            return f"[ForkedAgent] Timed out after {_FORKED_TIMEOUT_SECONDS}s"

    async def _run_inner(
        self,
        task: str,
        extra_context: str | None = None,
    ) -> str:
        system_prompt = self._system_prompt
        if extra_context:
            system_prompt = f"{system_prompt}\n\n{extra_context}"

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": task}
        ]

        for iteration in range(self._max_iterations):
            try:
                response = await self._llm_client.complete(
                    messages=messages,
                    tools=list(self._tool_map.values()),
                    model_config=self._model_config,
                    system_prompt=system_prompt,
                )
            except Exception as exc:
                logger.error("ForkedAgent LLM call failed (iteration %d): %s", iteration, exc)
                return f"[ForkedAgent error] {exc}"

            if not response.has_tool_calls:
                return response.text

            messages.append({"role": "assistant", "content": response.content})

            tool_results: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                result = await self._execute_tool(tc)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result.content,
                    **({} if not result.is_error else {"is_error": True}),
                })

            messages.append({"role": "user", "content": tool_results})

        return "[ForkedAgent] Max iterations reached."

    async def _execute_tool(self, tc: ToolCall) -> ToolResult:
        if tc.name not in self._allowed:
            return ToolResult(
                content=f"Permission denied: ForkedAgent cannot use tool '{tc.name}'",
                is_error=True,
            )
        tool = self._tool_map.get(tc.name)
        if tool is None:
            return ToolResult(
                content=f"Tool '{tc.name}' not available in ForkedAgent",
                is_error=True,
            )
        try:
            return await tool.execute(tc.input)
        except Exception as exc:
            return ToolResult(
                content=f"Error executing {tc.name}: {exc}",
                is_error=True,
            )


async def run_memory_extraction(
    messages: list[dict[str, Any]],
    parent_system_prompt: str,
    llm_client: Any,
    model_config: Any,
    tools: list[Tool],
    memory_file_path: str,
) -> None:
    """Background task: extract key facts from messages and update MEMORY.md.

    Designed to be launched as a fire-and-forget asyncio task after
    each conversation turn when the message count hits a threshold.
    """
    # Build a condensed conversation summary for the extraction task
    conversation_parts: list[str] = []
    for msg in messages[-20:]:  # Only look at last 20 messages
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            conversation_parts.append(f"{role.upper()}: {content[:300]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        conversation_parts.append(f"{role.upper()}: {text[:300]}")

    conversation_summary = "\n".join(conversation_parts)

    task = f"""Review this recent conversation and update {memory_file_path} with any
important new facts, decisions, or patterns worth remembering across sessions.

Only add information that is:
- Project-specific (frameworks, conventions, architecture)
- User preferences (coding style, tool preferences)
- Recurring solutions to common problems

Do NOT add: temporary context, task status, or session-specific details.

Recent conversation:
{conversation_summary}

Read the current MEMORY.md first, then update it if needed."""

    agent = ForkedAgent(
        parent_system_prompt=parent_system_prompt,
        allowed_tools=["HashlineRead", "Write"],
        llm_client=llm_client,
        model_config=model_config,
        tools=tools,
        max_iterations=3,
    )

    try:
        await agent.run(task)
        logger.debug("Memory extraction completed for %s", memory_file_path)
    except Exception as exc:
        logger.warning("Memory extraction failed: %s", exc)
