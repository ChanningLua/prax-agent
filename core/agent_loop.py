"""Agent Loop — the core orchestration cycle.

Sends messages to an LLM, parses tool calls, executes tools,
feeds results back, and repeats until the model returns final text.

Event emission is handled via EventBus.  Legacy on_xxx callbacks are
still accepted for backward compatibility and are wired to the bus
automatically via EventBus.from_callbacks().
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..tools.base import Tool, ToolCall, ToolResult
from .context import Context
from .error_recovery import ErrorTracker
from .event_bus import EventBus
from .governance import GovernanceConfig
from .llm_client import LLMClient, LLMResponse, ModelConfig
from .middleware import AgentMiddleware, RuntimeState
from .streaming_tool_executor import StreamingToolExecutor
from .stream_events import (
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    SpanEndEvent,
    SpanStartEvent,
    StreamEvent,
    ToolMatchEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from .trace import TraceContext
from ..tools.verify_command import is_verify_command


# Max iterations to prevent runaway loops
MAX_ITERATIONS = 25


@dataclass(frozen=True)
class AgentRunReport:
    stop_reason: str
    iterations: int
    had_tool_errors: bool
    only_permission_errors: bool
    usage: dict[str, int] | None = None
    verification_passed: bool = False


async def run_agent_loop(
    user_message: str,
    *,
    context: Context,
    llm_client: LLMClient,
    model_config: ModelConfig,
    tools: list[Tool],
    message_history: list[dict[str, Any]] | None = None,
    middlewares: list[AgentMiddleware] | None = None,
    # ── EventBus (preferred) ──────────────────────────────────────────────
    bus: EventBus | None = None,
    # ── Legacy callbacks (kept for backward compat, wired onto bus) ───────
    on_tool_call: Any | None = None,
    on_tool_result: Any | None = None,
    on_text: Any | None = None,
    on_complete: Any | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
    # ── Governance / budget ───────────────────────────────────────────────
    governance: GovernanceConfig | None = None,
    max_budget_tokens: int | None = None,  # kept for backward compat
    governance_path: str | None = None,
    # ── Trace ─────────────────────────────────────────────────────────────
    trace_ctx: TraceContext | None = None,
    # ── Other options ─────────────────────────────────────────────────────
    session_id: str | None = None,
) -> str:
    """Run the agent loop.

    Args:
        user_message: The user's task description.
        context: Execution context (cwd, project profile).
        llm_client: Configured LLM client.
        model_config: Which model to use.
        tools: Available tools.
        bus: EventBus instance.  If None, one is created automatically.
             Pass your own bus to observe events from the outside.
        on_text / on_tool_call / on_tool_result / on_complete / on_event:
             Legacy callbacks — still work, bridged onto *bus* internally.

    Returns:
        Final assistant text response.
    """
    # ── Build / merge EventBus ─────────────────────────────────────────────
    if bus is None:
        bus = EventBus()

    # Wire legacy callbacks onto the bus so the loop only calls bus.emit()
    legacy_bus = EventBus.from_callbacks(
        on_text=on_text,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_complete=on_complete,
        on_event=on_event,
    )
    # Merge: copy all legacy handlers into the caller's bus
    bus.merge(legacy_bus)

    # ── Resolve governance / budget / iterations ───────────────────────────
    effective_budget = max_budget_tokens  # legacy param wins if given
    effective_max_iter = MAX_ITERATIONS
    if governance is not None:
        if effective_budget is None and governance.max_budget_tokens is not None:
            effective_budget = governance.max_budget_tokens
        effective_max_iter = governance.max_iterations

    # ── Resolve trace context ──────────────────────────────────────────────
    root_trace = trace_ctx or (context.trace_ctx if context.trace_ctx else None)
    if root_trace is None:
        root_trace = TraceContext.new(metadata={"session_id": session_id})
    # Emit root span start
    await bus.emit(SpanStartEvent(
        trace_id=root_trace.trace_id,
        span_id=root_trace.span_id,
        span_name="agent_loop",
        parent_span_id=root_trace.parent_span_id,
    ))

    # ── Setup ──────────────────────────────────────────────────────────────
    system_prompt = context.build_system_prompt(task_type=getattr(context, "task_type", "general"))
    # Auto-inject GetSkillTool if context has a skill index
    if hasattr(context, "_skill_index") and context._skill_index is not None:
        from ..tools.get_skill import GetSkillTool
        skill_tool = GetSkillTool(context._skill_index)
        if not any(t.name == skill_tool.name for t in tools):
            tools = list(tools) + [skill_tool]
    tool_map = {t.name: t for t in tools}
    middlewares = middlewares or []
    middlewares = sorted(middlewares, key=lambda m: getattr(m, "priority", 100))
    messages: list[dict[str, Any]] = message_history if message_history is not None else []
    messages.append({"role": "user", "content": user_message})
    # 注册 Trajectory 保存 handler（messages 已定义）
    _register_trajectory_handler(bus, messages, session_id)

    tool_loop_counts: dict[str, int] = {}
    had_tool_errors = False
    only_permission_errors = True
    usage_totals: dict[str, int] = {}
    error_tracker = ErrorTracker()
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3
    verification_passed = False

    # ── Main loop ──────────────────────────────────────────────────────────
    iteration = 0
    while True:
        if iteration >= effective_max_iter:
            break

        await bus.emit(MessageStartEvent(session_id=session_id, iteration=iteration))

        # 热重载 governance（若传入 governance_path）
        if governance_path is not None:
            _fresh = GovernanceConfig.from_file_with_reload(governance_path)
            effective_max_iter = _fresh.max_iterations
            if max_budget_tokens is None and _fresh.max_budget_tokens is not None:
                effective_budget = _fresh.max_budget_tokens

        # Budget guard
        if effective_budget is not None:
            if sum(usage_totals.values()) >= effective_budget:
                report = AgentRunReport(
                    stop_reason="max_budget_reached",
                    iterations=iteration,
                    had_tool_errors=had_tool_errors,
                    only_permission_errors=only_permission_errors if had_tool_errors else False,
                    usage=usage_totals or None,
                    verification_passed=verification_passed,
                )
                await bus.emit(MessageStopEvent(
                    session_id=session_id,
                    stop_reason="max_budget_reached",
                    iterations=iteration,
                    usage=usage_totals or None,
                    had_tool_errors=had_tool_errors,
                    verification_passed=verification_passed,
                ))
                # Also fire AgentRunReport via MessageStopEvent subscribers
                # (on_complete is already bridged above)
                for h in bus._handlers.get(type(report), []):
                    await bus.emit(report)  # type: ignore[arg-type]
                await bus.emit(SpanEndEvent(
                    trace_id=root_trace.trace_id,
                    span_id=root_trace.span_id,
                    span_name="agent_loop",
                    duration_ms=root_trace.elapsed_ms(),
                    status="ok",
                    metadata={"stop_reason": "max_budget_reached"},
                ))
                return "[Prax] Budget limit reached. The task may be incomplete."

        # Middleware: before_model
        state = RuntimeState(
            messages=messages,
            context=context,
            iteration=iteration,
            tool_loop_counts=tool_loop_counts,
        )
        for middleware in middlewares:
            await middleware.before_model(state)

        # 应用动态模型覆盖（由 ModelFallbackMiddleware 等写入）
        _override_model = state.metadata.pop("dynamic_model_override", None)
        effective_model_config = (
            replace(model_config, model=_override_model) if _override_model else model_config
        )

        cache_enabled = bool(state.metadata.get("prompt_cache_enabled", False))

        # LLM call — emit span
        llm_span = root_trace.child(f"llm_call:{iteration}")
        await bus.emit(SpanStartEvent(
            trace_id=llm_span.trace_id,
            span_id=llm_span.span_id,
            span_name=f"llm_call:{iteration}",
            parent_span_id=llm_span.parent_span_id,
        ))

        llm_status = "ok"
        try:
            use_streaming = (on_text is not None) and effective_model_config.supports_streaming
            if use_streaming:
                response = None
                async for chunk in llm_client.stream_complete(
                    messages=messages,
                    tools=tools,
                    model_config=effective_model_config,
                    system_prompt=system_prompt,
                    thinking_enabled=context.thinking_enabled,
                    cache_enabled=cache_enabled,
                ):
                    if isinstance(chunk, str):
                        if on_text is not None:
                            on_text(chunk)
                    else:
                        response = chunk
                # Signal end-of-stream with None so the printer can close the line
                if on_text is not None:
                    on_text(None)  # type: ignore[arg-type]
                if response is None:
                    from .llm_client import LLMResponse as _LLMResponse
                    response = _LLMResponse(content=[{"type": "text", "text": ""}])
            else:
                response = await llm_client.complete(
                    messages=messages,
                    tools=tools,
                    model_config=effective_model_config,
                    system_prompt=system_prompt,
                    thinking_enabled=context.thinking_enabled,
                    reasoning_effort=context.reasoning_effort,
                    cache_enabled=cache_enabled,
                )
        except Exception as exc:
            llm_status = "error"
            consecutive_failures += 1
            await bus.emit(SpanEndEvent(
                trace_id=llm_span.trace_id,
                span_id=llm_span.span_id,
                span_name=f"llm_call:{iteration}",
                duration_ms=llm_span.elapsed_ms(),
                status="error",
            ))
            await _emit_lifecycle(middlewares, "OnError", {
                "error": str(exc),
                "iteration": iteration,
                "phase": "llm_call",
            })
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                await bus.emit(SpanEndEvent(
                    trace_id=root_trace.trace_id,
                    span_id=root_trace.span_id,
                    span_name="agent_loop",
                    duration_ms=root_trace.elapsed_ms(),
                    status="error",
                    metadata={"stop_reason": "circuit_breaker", "iterations": iteration},
                ))
                return f"[Prax] Circuit breaker triggered after {MAX_CONSECUTIVE_FAILURES} consecutive LLM failures. Last error: {exc}"
            iteration += 1
            continue
        finally:
            if llm_status == "ok":
                await bus.emit(SpanEndEvent(
                    trace_id=llm_span.trace_id,
                    span_id=llm_span.span_id,
                    span_name=f"llm_call:{iteration}",
                    duration_ms=llm_span.elapsed_ms(),
                    status=llm_status,
                ))

        if response.usage:
            for key, value in response.usage.items():
                if isinstance(value, int):
                    usage_totals[key] = usage_totals.get(key, 0) + value

        # Successful LLM call — reset failure counter
        consecutive_failures = 0

        # Middleware: after_model
        for middleware in middlewares:
            response = await middleware.after_model(state, response)

        if response.has_tool_calls:
            messages.append({"role": "assistant", "content": response.content})

            # Create executor for parallel/serial scheduling
            executor = StreamingToolExecutor(
                tools=tools,
                middlewares=middlewares,
                state=state,
                error_tracker=error_tracker,
            )

            # Submit phase: emit events and queue tool calls
            for tc in response.tool_calls:
                await bus.emit(ToolMatchEvent(
                    tool_name=tc.name, tool_id=tc.id, tool_input=tc.input
                ))
                await bus.emit(ToolStartEvent(tool_name=tc.name, tool_id=tc.id))
                await executor.submit(tc)

            # Execution phase: parallel + serial mixed execution
            call_results = await executor.drain()

            # Result processing phase: emit spans and tool result events
            tool_results: list[dict[str, Any]] = []
            for tcr in call_results:
                tc = tcr.tool_call
                result = tcr.result
                is_verification_tool = (
                    tc.name == "VerifyCommand"
                    or (tc.name == "SandboxBash" and is_verify_command(str(tc.input.get("command", "")).strip()))
                )
                if is_verification_tool:
                    verification_passed = not result.is_error

                # Tool execution span (based on actual execution time)
                tool_span = root_trace.child(f"tool_call:{tc.name}")
                await bus.emit(SpanStartEvent(
                    trace_id=tool_span.trace_id,
                    span_id=tool_span.span_id,
                    span_name=f"tool_call:{tc.name}",
                    parent_span_id=tool_span.parent_span_id,
                ))
                tool_status = "error" if result.is_error else "ok"
                await bus.emit(SpanEndEvent(
                    trace_id=tool_span.trace_id,
                    span_id=tool_span.span_id,
                    span_name=f"tool_call:{tc.name}",
                    duration_ms=tool_span.elapsed_ms(),
                    status=tool_status,
                ))

                if result.is_error:
                    had_tool_errors = True
                    if not result.content.startswith("Permission denied:"):
                        only_permission_errors = False

                content_preview = result.content[:200] if len(result.content) > 200 else result.content
                await bus.emit(ToolResultEvent(
                    tool_name=tc.name,
                    tool_id=tc.id,
                    is_error=result.is_error,
                    content_preview=content_preview,
                ))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result.content,
                    **({} if not result.is_error else {"is_error": True}),
                })

            messages.append({"role": "user", "content": tool_results})
            iteration += 1

        else:
            # Final text response
            text = response.text
            # Only emit MessageDeltaEvent in non-streaming mode;
            # in streaming mode the chunks were already emitted via on_text().
            if not use_streaming:
                await bus.emit(MessageDeltaEvent(text=text))

            stop_event = MessageStopEvent(
                session_id=session_id,
                stop_reason=response.stop_reason or "end_turn",
                iterations=iteration + 1,
                usage=usage_totals or None,
                had_tool_errors=had_tool_errors,
                verification_passed=verification_passed,
            )
            await bus.emit(stop_event)

            # Fire AgentRunReport for on_complete subscribers
            report = AgentRunReport(
                stop_reason=response.stop_reason or "end_turn",
                iterations=iteration + 1,
                had_tool_errors=had_tool_errors,
                only_permission_errors=only_permission_errors if had_tool_errors else False,
                usage=usage_totals or None,
                verification_passed=verification_passed,
            )
            await bus.emit(report)  # type: ignore[arg-type]

            # Lifecycle: OnComplete
            await _emit_lifecycle(middlewares, "OnComplete", {
                "stop_reason": response.stop_reason or "end_turn",
                "iterations": iteration + 1,
                "had_tool_errors": int(had_tool_errors),
            })

            # Root span end
            await bus.emit(SpanEndEvent(
                trace_id=root_trace.trace_id,
                span_id=root_trace.span_id,
                span_name="agent_loop",
                duration_ms=root_trace.elapsed_ms(),
                status="ok",
                metadata={"stop_reason": response.stop_reason or "end_turn", "iterations": iteration + 1},
            ))

            return text

    # Max iterations reached
    stop_event = MessageStopEvent(
        session_id=session_id,
        stop_reason="max_iterations",
        iterations=effective_max_iter,
        usage=usage_totals or None,
        had_tool_errors=had_tool_errors,
        verification_passed=verification_passed,
    )
    await bus.emit(stop_event)
    report = AgentRunReport(
        stop_reason="max_iterations",
        iterations=effective_max_iter,
        had_tool_errors=had_tool_errors,
        only_permission_errors=only_permission_errors if had_tool_errors else False,
        usage=usage_totals or None,
        verification_passed=verification_passed,
    )
    await bus.emit(report)  # type: ignore[arg-type]
    await bus.emit(SpanEndEvent(
        trace_id=root_trace.trace_id,
        span_id=root_trace.span_id,
        span_name="agent_loop",
        duration_ms=root_trace.elapsed_ms(),
        status="ok",
        metadata={"stop_reason": "max_iterations"},
    ))
    return "[Prax] Max iterations reached. The task may be incomplete."


async def _emit_lifecycle(
    middlewares: list[AgentMiddleware], event: str, context: dict
) -> None:
    """Emit a lifecycle event to all HookMiddleware instances in the chain."""
    from .middleware import HookMiddleware
    for mw in middlewares:
        if isinstance(mw, HookMiddleware):
            await mw._registry.execute_lifecycle_hooks(event, context)


def _register_trajectory_handler(
    bus: EventBus,
    messages: list[dict[str, Any]],
    session_id: str | None,
) -> None:
    """在 EventBus 上注册 MessageStopEvent handler，保存 ShareGPT 格式轨迹。"""

    async def on_stop(event: MessageStopEvent) -> None:
        try:
            await _save_trajectory(messages, event, session_id)
        except Exception as exc:
            logging.getLogger(__name__).warning("Trajectory save failed: %s", exc)

    bus.on(MessageStopEvent, on_stop)


async def _save_trajectory(
    messages: list[dict[str, Any]],
    event: MessageStopEvent,
    session_id: str | None,
) -> None:
    """将对话轨迹以 ShareGPT JSONL 格式写入 ~/.prax/trajectories/。"""
    traj_dir = Path(os.path.expanduser("~/.prax/trajectories"))
    traj_dir.mkdir(parents=True, exist_ok=True)

    # 转换为 ShareGPT conversations 格式
    conversations: list[dict[str, str]] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = "\n".join(text_parts)
        if not isinstance(content, str) or not content.strip():
            continue
        sharegpt_role = "human" if role == "user" else "gpt"
        conversations.append({"from": sharegpt_role, "value": content})

    record = {
        "conversations": conversations,
        "session_id": session_id or "",
        "stop_reason": event.stop_reason,
        "iterations": event.iterations,
        "completed": not event.had_tool_errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    line = json.dumps(record, ensure_ascii=False)

    if event.had_tool_errors:
        target = traj_dir / "failed_trajectories.jsonl"
    else:
        target = traj_dir / "trajectories.jsonl"

    with open(target, "a", encoding="utf-8") as f:
        f.write(line + "\n")
