"""Streaming Tool Executor — parallel scheduling for concurrency-safe tools.

Read/Grep/Glob tools run in parallel.
Edit/Write/Bash tools must run serially.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..tools.base import Tool, ToolCall, ToolInputValidationError, ToolResult

if TYPE_CHECKING:
    from .error_recovery import ErrorTracker
    from .middleware import AgentMiddleware, RuntimeState

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    tool_call: ToolCall
    result: ToolResult


class StreamingToolExecutor:
    """Execute tool calls with parallel scheduling for safe tools.

    Tools marked with `is_concurrency_safe = True` (e.g. Read, Grep, Glob)
    are launched immediately in parallel.  All other tools are queued and
    executed serially after any running parallel tasks finish.

    Usage::

        executor = StreamingToolExecutor(tools)
        # Feed tool calls as they arrive from the model:
        for tc in response.tool_calls:
            await executor.submit(tc)
        # Wait for everything to finish and get ordered results:
        results = await executor.drain()
    """

    def __init__(
        self,
        tools: list[Tool],
        middlewares: list["AgentMiddleware"],
        state: "RuntimeState",
        error_tracker: "ErrorTracker | None" = None,
    ) -> None:
        self._tool_map: dict[str, Tool] = {t.name: t for t in tools}
        self._middlewares = middlewares
        self._state = state
        self._error_tracker = error_tracker
        # Parallel bucket: tasks that can run concurrently
        self._parallel_tasks: list[asyncio.Task[ToolCallResult]] = []
        # Serial queue: tasks that must wait
        self._serial_queue: list[ToolCall] = []
        # Ordered results list (index matches submission order)
        self._results: list[ToolCallResult] = []
        self._submission_order: list[ToolCall] = []

    async def submit(self, tool_call: ToolCall) -> None:
        """Submit a tool call for execution.

        If the tool is concurrency-safe and no serial tasks are pending or
        running, it is launched immediately in a background task.
        Otherwise it is queued for serial execution.
        """
        self._submission_order.append(tool_call)
        tool = self._tool_map.get(tool_call.name)
        concurrency_safe = tool is not None and getattr(tool, "is_concurrency_safe", False)

        if concurrency_safe and not self._serial_queue:
            # Safe to launch in parallel
            task = asyncio.create_task(self._run_tool_with_middleware(tool_call))
            self._parallel_tasks.append(task)
        else:
            self._serial_queue.append(tool_call)

    async def drain(self) -> list[ToolCallResult]:
        """Wait for all submitted tool calls to complete.

        Returns results in submission order.
        """
        # First, collect all parallel results
        parallel_results: dict[str, ToolCallResult] = {}
        if self._parallel_tasks:
            done = await asyncio.gather(*self._parallel_tasks, return_exceptions=False)
            for r in done:
                parallel_results[r.tool_call.id] = r

        # Then, execute serial queue one by one
        serial_results: dict[str, ToolCallResult] = {}
        for tc in self._serial_queue:
            r = await self._run_tool_with_middleware(tc)
            serial_results[tc.id] = r

        # Merge in submission order
        all_results: dict[str, ToolCallResult] = {**parallel_results, **serial_results}
        return [all_results[tc.id] for tc in self._submission_order if tc.id in all_results]

    async def _run_tool_with_middleware(self, tool_call: ToolCall) -> ToolCallResult:
        """Execute a tool call with full middleware chain.

        Runs before_tool → tool execution → after_tool.
        before_tool can short-circuit by returning a ToolResult.
        """
        tool = self._tool_map.get(tool_call.name)

        # before_tool middleware chain (can short-circuit)
        result: ToolResult | None = None
        for mw in self._middlewares:
            result = await mw.before_tool(self._state, tool_call, tool)
            if result is not None:
                break  # Short-circuit (e.g., permission denied)

        if result is None:
            result = await self._run_tool(tool_call)

        # after_tool middleware chain
        for mw in self._middlewares:
            result = await mw.after_tool(self._state, tool_call, tool, result)

        return ToolCallResult(tool_call=tool_call, result=result)

    async def _run_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute the tool without middleware (internal use only)."""
        tool = self._tool_map.get(tool_call.name)
        if tool is None:
            logger.warning("Rejected unknown tool call: %s", tool_call.name)
            return ToolResult(
                content=f"Error: unknown tool '{tool_call.name}'.",
                is_error=True,
            )

        try:
            tool.validate_params(tool_call.input)
        except ToolInputValidationError as exc:
            return ToolResult(content=str(exc), is_error=True)

        try:
            result = await tool.execute(tool_call.input)
        except Exception as exc:
            from .error_recovery import classify_error, compute_recovery
            hint = ""
            if self._error_tracker is not None:
                try:
                    classification = classify_error(exc, tool_name=tool_call.name)
                    self._error_tracker.record(classification)
                    retry_count = self._error_tracker.get_retry_count_for_type(
                        classification.error_type
                    )
                    strategy = compute_recovery(classification, retry_count=retry_count - 1)
                    if strategy.tool_hint:
                        hint = f"\n[Recovery hint] {strategy.tool_hint}"
                    elif strategy.reason:
                        hint = f"\n[Recovery hint] {strategy.reason}"
                except Exception:
                    pass
            result = ToolResult(
                content=f"Error executing {tool_call.name}: {exc}{hint}",
                is_error=True,
            )
        return result
