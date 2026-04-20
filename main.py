"""Prax CLI — unified orchestration engine entry point."""

from __future__ import annotations

import asyncio
import copy
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Callable

from .core.claude_cli_executor import ClaudeCliExecutor, is_available as claude_cli_available
from .core.agent_loop import AgentRunReport, run_agent_loop
from .core.governance import GovernanceConfig
from .core.stream_events import MessageStopEvent, ToolMatchEvent, ToolResultEvent
from .core.classifier import Classifier
from .commands.handlers import CommandContext, run_command
from .commands.registry import command_map, format_help, parse_command_tokens, parse_slash_command
from .core.config_files import load_models_config, load_mcp_config
from .core.mcp_loader import load_mcp_tools
from .core.model_catalog import get_model_entry
from .core.model_upgrade import get_exception_upgrade_reason, get_upgrade_path, should_upgrade_model
from .core.context import Context
from .core.llm_client import LLMClient
from .core.runtime_paths import OPENPRAX_CLAUDE_DEBUG_BRIDGE, OPENPRAX_NATIVE, build_last_run_metadata
from .core.memory_middleware import MemoryExtractionMiddleware
from .core.middleware import EvaluatorMiddleware, HookMiddleware, LoopDetectionMiddleware, PromptCacheMiddleware, QualityGateMiddleware, RunBoundaryReminderMiddleware, TodoReminderMiddleware, VerificationGuidanceMiddleware
from .core.permission_guard import PermissionGuardMiddleware
from .core.permissions import ExecutionPolicy, PermissionMode
from .core.session_store import FileSessionStore, SessionData
from .core.summarization import SummarizationMiddleware
from .core.background_store import BackgroundTaskStore
from .repl import run_repl
from .tools.base import ToolCall, ToolResult
from .tools.background_task import (
    CancelTaskTool,
    CheckTaskTool,
    ListTasksTool,
    StartTaskTool,
    UpdateTaskTool,
)
from .tools.task import TaskTool
from .tools.todo_write import TodoWriteTool
from .tools.ast_grep import AstGrepSearchTool, AstGrepReplaceTool
from .tools.tmux_bash import TmuxBashTool
from .tools.hashline_read import HashlineReadTool
from .tools.hashline_edit import HashlineEditTool
from .tools.web_search import WebSearchTool, WebCrawlerTool
from .tools.sandbox_bash import SandboxBashTool
from .tools.verify_command import VerifyCommandTool
from .agents.loader import get_agent_registry


CONFIG_DIR = Path(__file__).parent.parent / "config"

def _print_tool_call(event: Any) -> None:
    """Print tool call for user visibility."""
    if isinstance(event, ToolMatchEvent):
        name = event.tool_name
        params_preview = str(event.tool_input or {})
    else:
        # Legacy ToolCall object
        name = getattr(event, 'name', str(event))
        params_preview = str(getattr(event, 'input', ''))
    if len(params_preview) > 120:
        params_preview = params_preview[:120] + "..."
    print(f"\n\033[36m▶ {name}\033[0m {params_preview}", flush=True)


def _print_tool_result(event_or_call: Any, result: Any = None) -> None:
    """Print tool result summary."""
    if isinstance(result, ToolResultEvent):
        preview = result.content_preview or ""
        is_error = result.is_error
    elif result is not None:
        # Legacy ToolResult object
        content = getattr(result, 'content', '')
        preview = content if isinstance(content, str) else str(content)
        is_error = getattr(result, 'is_error', False)
    elif isinstance(event_or_call, ToolResultEvent):
        preview = event_or_call.content_preview or ""
        is_error = event_or_call.is_error
    else:
        preview = ""
        is_error = False
    if len(preview) > 200:
        preview = preview[:200] + "..."
    status = "\033[31m✗\033[0m" if is_error else "\033[32m✓\033[0m"
    print(f"  {status} {preview}", flush=True)


_stream_started = False


def _print_text(text: str | None) -> None:
    """Print streaming text chunk or final assistant response.

    Pass None to signal end-of-stream (closes the color escape and newline).
    """
    global _stream_started
    if text is None:
        # End-of-stream sentinel
        if _stream_started:
            print("\033[0m", flush=True)
            _stream_started = False
        return
    if not _stream_started:
        print(f"\n\033[37m", end="", flush=True)
        _stream_started = True
    print(text, end="", flush=True)


def _handle_slash_command(task: str) -> bool:
    if not task.startswith("/"):
        return False

    try:
        parts = shlex.split(task)
    except ValueError:
        parts = task.split()
    command = parts[0] if parts else task

    if command == "/ralph-loop":
        print(
            "\033[31mError:\033[0m `/ralph-loop` is not a Prax command.\n"
            "Upstream Ralph runs through `ralph.sh`, not a `/ralph-loop` slash command.\n"
            "Run `./ralph.sh --tool claude [max_iterations]` from the directory that contains "
            "`ralph.sh`, `CLAUDE.md`, and `prd.json`.",
            flush=True,
        )
        return True

    if command == "/ralph":
        print(
            "\033[31mError:\033[0m `/ralph` is not a Prax command.\n"
            "In Ralph, `/ralph` is a skill used to generate `prd.json`. Execution is done by `ralph.sh`.",
            flush=True,
        )
        return True

    if command[1:] not in command_map():
        print(
            f"\033[31mError:\033[0m prax does not support slash commands like `{command}`.\n"
            "Pass a natural-language task, or run the underlying shell command directly.",
            flush=True,
        )
    return True


async def _run_with_model_upgrades(
    task: str,
    *,
    context: Context,
    llm_client: LLMClient,
    models_config: dict,
    initial_model: str,
    tools: list[Any],
    middlewares: list[Any],
    base_history: list[dict[str, Any]],
    on_tool_call: Callable[[ToolCall], None] | None = None,
    on_tool_result: Callable[[ToolCall, ToolResult], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    run_loop: Callable[..., Any] = run_agent_loop,
    governance: Any = None,
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, str]], AgentRunReport]:
    upgrade_path = get_upgrade_path(initial_model, models_config)
    if not upgrade_path:
        raise RuntimeError("No configured models are currently available. Check provider credentials.")
    upgrade_events: list[dict[str, str]] = []
    usage_totals: dict[str, int] = {}

    model_config = llm_client.resolve_model(upgrade_path[0], models_config)

    for attempt_index, attempt_model in enumerate(upgrade_path, start=1):
        if attempt_model != model_config.model:
            model_config = llm_client.resolve_model(attempt_model, models_config)

        context.model = attempt_model
        attempt_history = copy.deepcopy(base_history)
        report_box: dict[str, AgentRunReport] = {}

        print(
            f"\033[90m[prax] attempt={attempt_index} model={attempt_model}\033[0m",
            flush=True,
        )

        def _capture_report(value: Any) -> None:
                if isinstance(value, AgentRunReport):
                    report_box.setdefault("report", value)
                elif isinstance(value, MessageStopEvent):
                    report_box.setdefault("report", AgentRunReport(
                        stop_reason=value.stop_reason,
                        iterations=value.iterations,
                        had_tool_errors=value.had_tool_errors,
                        only_permission_errors=False,
                        usage=value.usage,
                        verification_passed=value.verification_passed,
                    ))

        try:
            result_text = await run_loop(
                task,
                context=context,
                llm_client=llm_client,
                model_config=model_config,
                tools=tools,
                message_history=attempt_history,
                middlewares=middlewares,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                on_text=on_text,
                on_complete=_capture_report,
                governance=governance,
            )
        except Exception as exc:
            reason = get_exception_upgrade_reason(exc)
            if reason is None or attempt_index == len(upgrade_path):
                raise
            next_model = upgrade_path[attempt_index]
            upgrade_events.append({"from": attempt_model, "to": next_model, "reason": reason})
            print(
                f"\033[33m[prax] upgrading model {attempt_model} -> {next_model} "
                f"(reason={reason})\033[0m",
                flush=True,
            )
            continue

        report = report_box.get("report")
        if report is None:
            synthesized_iterations = max(
                1,
                sum(1 for message in attempt_history if message.get("role") == "assistant"),
            )
            report = AgentRunReport(
                stop_reason="end_turn",
                iterations=synthesized_iterations,
                had_tool_errors=False,
                only_permission_errors=False,
                usage=usage_totals or None,
                verification_passed=False,
            )
            print(
                "\033[33m[prax] warning=no completion report; using synthesized fallback report\033[0m",
                flush=True,
            )
        for key, value in (report.usage or {}).items():
            if isinstance(value, int):
                usage_totals[key] = usage_totals.get(key, 0) + value

        decision = should_upgrade_model(report, result_text)
        if not decision.should_retry or attempt_index == len(upgrade_path):
            final_report = AgentRunReport(
                stop_reason=report.stop_reason,
                iterations=report.iterations,
                had_tool_errors=report.had_tool_errors,
                only_permission_errors=report.only_permission_errors,
                usage=usage_totals or report.usage,
            )
            return attempt_model, result_text, attempt_history, upgrade_events, final_report

        next_model = upgrade_path[attempt_index]
        event = {
            "from": attempt_model,
            "to": next_model,
            "reason": decision.reason,
        }
        upgrade_events.append(event)
        print(
            f"\033[33m[prax] upgrading model {attempt_model} -> {next_model} "
            f"(reason={decision.reason})\033[0m",
            flush=True,
        )

    raise RuntimeError("Model upgrade attempts exhausted without completion")


def _merge_usage(existing: dict[str, int] | None, latest: dict[str, int] | None) -> dict[str, int]:
    merged = dict(existing or {})
    for key, value in (latest or {}).items():
        if isinstance(value, int):
            merged[key] = merged.get(key, 0) + value
    return merged


def _build_command_context(
    *,
    cwd: str,
    models_config: dict,
    session_id: str | None,
    permission_mode: PermissionMode,
    output_format: str,
) -> CommandContext:
    return CommandContext(
        cwd=cwd,
        models_config=models_config,
        session_store=FileSessionStore(str(Path(cwd) / ".prax" / "sessions")),
        session_id=session_id,
        permission_mode=permission_mode,
        output_format=output_format,
    )


def _print_command_result(result: Any, *, output_format: str) -> None:
    print(result.render(output_format), flush=True)


def _parse_global_args(args: list[str]) -> tuple[dict[str, Any], list[str]]:
    options: dict[str, Any] = {
        "model_override": None,
        "permission_mode": None,
        "session_id": None,
        "output_format": "text",
        "tui": False,
        "runtime_path": "auto",
    }
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--model" and i + 1 < len(args):
            options["model_override"] = args[i + 1]
            i += 2
        elif arg == "--permission-mode" and i + 1 < len(args):
            _PERMISSION_ALIASES = {"dangerous": "danger-full-access"}
            raw = args[i + 1]
            raw = _PERMISSION_ALIASES.get(raw, raw)
            try:
                options["permission_mode"] = PermissionMode(raw)
            except ValueError:
                options["permission_mode"] = None
            i += 2
        elif arg == "--session-id" and i + 1 < len(args):
            options["session_id"] = args[i + 1]
            i += 2
        elif arg == "--output-format" and i + 1 < len(args):
            options["output_format"] = args[i + 1]
            i += 2
        elif arg == "--tui":
            options["tui"] = True
            i += 1
        elif arg == "--runtime-path" and i + 1 < len(args):
            value = args[i + 1]
            if value not in ("native", "bridge", "auto"):
                raise ValueError(f"Invalid --runtime-path value: {value!r}. Must be native, bridge, or auto.")
            options["runtime_path"] = value
            i += 2
        else:
            positional.append(arg)
            i += 1
    return options, positional


def _run_task_sync(
    task: str,
    *,
    model_override: str | None,
    permission_mode: Any | None,
    session_id: str | None,
    runtime_path: str = "auto",
) -> None:
    asyncio.run(
        _run(
            task,
            model_override=model_override,
            permission_mode=permission_mode,
            session_id=session_id,
            runtime_path=runtime_path,
        )
    )


def _build_tools(
    *,
    cwd: str,
    task_executor: Callable[[str, str, str, int | None], Any] | None,
    include_task_tool: bool,
) -> tuple[list[Any], dict[str, bool]]:
    """Build tools list — only orchestration-layer tools (CC provides file/bash tools)."""
    tools: list[Any] = [
        TodoWriteTool(cwd=cwd),
    ]

    tool_flags: dict[str, bool] = {}

    if include_task_tool and task_executor is not None:
        tools.append(TaskTool(executor=task_executor))
        store = BackgroundTaskStore(cwd=cwd)
        tools.extend([
            StartTaskTool(store=store, executor=task_executor),
            CheckTaskTool(store=store),
            UpdateTaskTool(store=store),
            CancelTaskTool(store=store),
            ListTasksTool(store=store),
        ])

    # AstGrep — semantic code search/replace
    if AstGrepSearchTool.is_available():
        tools.append(AstGrepSearchTool(cwd=cwd))
        tools.append(AstGrepReplaceTool(cwd=cwd))
        tool_flags["has_ast_grep"] = True

    # TmuxBash — persistent shell sessions for TUI apps
    if TmuxBashTool.is_available():
        tools.append(TmuxBashTool(cwd=cwd))
        tool_flags["has_tmux_bash"] = True

    # Hashline — hash-anchored file read/edit for improved accuracy
    tools.append(HashlineReadTool())
    tools.append(HashlineEditTool())
    tool_flags["has_hashline"] = True

    # Web search / crawl — real-time information retrieval
    if WebSearchTool.is_available():
        tools.append(WebSearchTool())
        tool_flags["has_web_search"] = True
    if WebCrawlerTool.is_available():
        tools.append(WebCrawlerTool())
        tool_flags["has_web_crawler"] = True

    # VerifyCommand — restricted repo-local test/build validation
    tools.append(VerifyCommandTool(cwd=cwd))
    tool_flags["has_verify_command"] = True

    # SandboxBash — isolated command execution (docker or local fallback)
    tools.append(SandboxBashTool(cwd=cwd))
    tool_flags["has_sandbox_bash"] = True

    return tools, tool_flags


def _make_task_executor(
    *,
    cwd: str,
    models_config: dict,
    permission_mode: Any,
    parent_model: str,
) -> Callable[[str, str, str, int | None], Any]:
    def _resolve_delegated_cwd(prompt: str) -> str:
        import re as _re

        workspace_root = Path(cwd).resolve()
        effective_permission_mode = (
            permission_mode if isinstance(permission_mode, PermissionMode)
            else PermissionMode.WORKSPACE_WRITE
        )
        policy = ExecutionPolicy(str(workspace_root), effective_permission_mode)

        for line in prompt.splitlines():
            match = _re.match(r"^Working directory:\s*(.+)$", line.strip())
            if not match:
                continue

            candidate = Path(match.group(1).strip()).expanduser()
            if not candidate.is_dir():
                break

            resolved = candidate.resolve()
            if effective_permission_mode == PermissionMode.DANGER_FULL_ACCESS:
                return str(resolved)

            decision = policy.authorize_path(str(resolved), write=True)
            if decision.allowed:
                return str(resolved)
            break

        return str(workspace_root)

    async def _executor(description: str, prompt: str, subagent_type: str, max_turns: int | None, load_skills: list | None = None) -> str:
        effective_permission_mode = (
            permission_mode if isinstance(permission_mode, PermissionMode)
            else PermissionMode.WORKSPACE_WRITE
        )
        sub_cwd = _resolve_delegated_cwd(prompt)
        sub_context = Context(
            cwd=sub_cwd,
            model=parent_model,
        )
        sub_middlewares = [
            PermissionGuardMiddleware(permission_mode=effective_permission_mode),
            LoopDetectionMiddleware(hard_limit=max_turns or 5),
            TodoReminderMiddleware(cwd=sub_cwd),
        ]
        client = LLMClient()
        try:
            sub_tools, _ = _build_tools(cwd=sub_cwd, task_executor=None, include_task_tool=False)
            model_hint = parent_model
            # Route subagent_type to agent definition (system prompt + model)
            registry = get_agent_registry(sub_cwd)
            selected_agent = registry.get_by_name(subagent_type) or registry.select_for_task(description)
            agent_system_prompt: str | None = None
            if selected_agent:
                if selected_agent.model and get_model_entry(selected_agent.model, models_config):
                    model_hint = selected_agent.model
                agent_system_prompt = selected_agent.system_prompt or None
            elif subagent_type.lower() == "plan" and get_model_entry("claude-sonnet-4-6", models_config):
                model_hint = "claude-sonnet-4-6"
            task_prompt = (
                f"[Delegated task: {description}]\n"
                f"Working directory: {sub_cwd}\n"
                f"Subagent type: {subagent_type}\n\n"
                f"{prompt}"
            )
            if agent_system_prompt:
                task_prompt = f"{agent_system_prompt}\n\n{task_prompt}"
            final_model, result_text, _history, _events, _report = await _run_with_model_upgrades(
                task_prompt,
                context=sub_context,
                llm_client=client,
                models_config=models_config,
                initial_model=model_hint,
                tools=sub_tools,
                middlewares=sub_middlewares,
                base_history=[],
                on_text=None,
            )
            return json.dumps(
                {
                    "description": description,
                    "subagent_type": subagent_type,
                    "model": final_model,
                    "result": result_text,
                },
                ensure_ascii=False,
                indent=2,
            )
        finally:
            await client.close()

    return _executor


def _bootstrap_session(
    *,
    cwd: str,
    task: str,
    model_override: str | None,
    session_id: str | None,
    models_config: dict,
) -> tuple[str, str | None, str | None, "SessionData", "FileSessionStore"]:
    """Resolve model name, agent selection, and session state."""
    session_store = FileSessionStore(str(Path(cwd) / ".prax" / "sessions"))
    session = session_store.load(session_id) if session_id else None

    rules_path = Path(cwd) / ".prax" / "rules.yaml"
    if not rules_path.exists():
        rules_path = CONFIG_DIR / "rules.yaml"
    classifier = Classifier(str(rules_path) if rules_path.exists() else None)

    task_tier = classifier.classify(task)

    if model_override:
        model_name = model_override
    elif session is not None and (session.metadata or {}).get("preferred_model"):
        model_name = str((session.metadata or {})["preferred_model"])
    else:
        model_name = classifier.select_model(task, models_config.get("default_model", "gpt-4.1"))

    registry = get_agent_registry(cwd)
    selected_agent = registry.select_for_task(task)
    agent_system_prompt: str | None = None
    agent_name: str | None = None
    if selected_agent:
        agent_name = selected_agent.name
        agent_system_prompt = selected_agent.system_prompt
        if not model_override and get_model_entry(selected_agent.model, models_config):
            model_name = selected_agent.model
        print(
            f"\033[90m[prax] agent={agent_name} model={model_name}\033[0m",
            flush=True,
        )

    active_session_id = session_id or session_store.create_session_id()
    if session is None:
        session = SessionData(
            session_id=active_session_id,
            cwd=cwd,
            model=model_name,
            messages=[],
            metadata={"task_tier": task_tier},
        )
    else:
        session.cwd = cwd
        session.model = model_name
        session.metadata = session.metadata or {}
        session.metadata["task_tier"] = task_tier

    return model_name, agent_name, agent_system_prompt, session, session_store


async def _build_pipeline(
    *,
    cwd: str,
    model_name: str,
    models_config: dict,
    permission_mode: Any,
    agent_name: str | None,
    agent_system_prompt: str | None,
    session: "SessionData",
) -> tuple["Context", "LLMClient", list, list]:
    """Build tools, context, LLM client, and middleware chain."""
    preferred_thinking_enabled = bool(
        (session.metadata or {}).get("preferred_thinking_enabled", False)
    )
    preferred_reasoning_effort = (
        str((session.metadata or {})["preferred_reasoning_effort"])
        if (session.metadata or {}).get("preferred_reasoning_effort") is not None
        else None
    )
    task_type = str((session.metadata or {}).get("task_tier", "general"))

    task_executor = _make_task_executor(
        cwd=cwd,
        models_config=models_config,
        permission_mode=permission_mode or PermissionMode.WORKSPACE_WRITE,
        parent_model=model_name,
    )
    tools, _ = _build_tools(cwd=cwd, task_executor=task_executor, include_task_tool=True)

    mcp_configs = load_mcp_config(cwd)
    if mcp_configs:
        mcp_tools = await load_mcp_tools(mcp_configs)
        tools.extend(mcp_tools)
        if mcp_tools:
            print(f"\033[90m[prax] mcp_tools={len(mcp_tools)}\033[0m", flush=True)

    context = Context(
        cwd=cwd,
        model=model_name,
        thinking_enabled=preferred_thinking_enabled,
        reasoning_effort=preferred_reasoning_effort,
        agent_system_prompt=agent_system_prompt,
        agent_name=agent_name,
        task_type=task_type,
    )

    client = LLMClient()
    if get_model_entry(model_name, models_config) is None:
        print(f"\033[31mError: Model '{model_name}' not found in configuration\033[0m", file=sys.stderr)
        sys.exit(1)

    model_config = client.resolve_model(model_name, models_config)
    middlewares = [
        PermissionGuardMiddleware(permission_mode=permission_mode or PermissionMode.WORKSPACE_WRITE),
        LoopDetectionMiddleware(),
        TodoReminderMiddleware(cwd=cwd),
        RunBoundaryReminderMiddleware(),
        VerificationGuidanceMiddleware(),
        QualityGateMiddleware(cwd=cwd),
        EvaluatorMiddleware(cwd=cwd),
        MemoryExtractionMiddleware(cwd=cwd, llm_client=client, model_config=model_config, enabled=True),
        SummarizationMiddleware(llm_client=client, model_config=model_config, max_messages=60, keep_recent=10),
        HookMiddleware(hooks_dir=str(Path(cwd) / ".prax" / "hooks"), cwd=cwd),
        PromptCacheMiddleware(),
    ]

    return context, client, tools, middlewares


async def _execute(
    task: str,
    *,
    context: "Context",
    client: "LLMClient",
    tools: list,
    middlewares: list,
    models_config: dict,
    model_name: str,
    session: "SessionData",
    session_store: "FileSessionStore",
) -> None:
    """Run the agent loop and persist session state."""
    hook_mw = next((m for m in middlewares if isinstance(m, HookMiddleware)), None)

    # Build adaptive governance based on task tier
    tier_iteration_map = {"high-perf": 50, "standard": 30, "fast": 15}
    task_tier = (session.metadata or {}).get("task_tier", "standard")
    max_iter = tier_iteration_map.get(task_tier, 25)
    governance = GovernanceConfig(max_iterations=max_iter)

    try:
        base_history = copy.deepcopy(session.messages or [])
        if hook_mw is not None:
            await hook_mw._registry.execute_lifecycle_hooks(
                "SessionStart",
                {"session_id": session.session_id, "cwd": context.cwd, "model": model_name},
            )
        final_model, _, final_history, upgrade_events, report = await _run_with_model_upgrades(
            task,
            context=context,
            llm_client=client,
            models_config=models_config,
            initial_model=model_name,
            tools=tools,
            middlewares=middlewares,
            base_history=base_history,
            on_tool_call=_print_tool_call,
            on_tool_result=_print_tool_result,
            on_text=_print_text,
            governance=governance,
        )
        session.model = final_model
        session.messages = final_history
        session.metadata = dict(session.metadata or {})
        session.metadata["usage"] = _merge_usage(session.metadata.get("usage"), report.usage)
        session.metadata["last_run"] = build_last_run_metadata(
            model=final_model,
            runtime=OPENPRAX_NATIVE,
            extra={
                "stop_reason": report.stop_reason,
                "iterations": report.iterations,
            },
        )
        if upgrade_events:
            history = list(session.metadata.get("upgrade_history", []))
            history.extend(upgrade_events)
            session.metadata["upgrade_history"] = history
    finally:
        if hook_mw is not None:
            await hook_mw._registry.execute_lifecycle_hooks(
                "Stop",
                {"session_id": session.session_id, "cwd": context.cwd},
            )
        session_store.save(session)
        await client.close()


async def _run_via_claude_cli(
    task: str,
    *,
    cwd: str,
    model_name: str,
    session: "SessionData",
    session_store: "FileSessionStore",
    agent_system_prompt: str | None,
    agent_name: str | None,
    permission_mode: Any | None,
    hooks_dir: str,
) -> None:
    """Execute task via `claude -p` subprocess (primary path when claude CLI is available)."""
    from .core.hooks import get_hook_registry, load_hooks_from_directory
    from pathlib import Path as _Path

    hook_registry = get_hook_registry()
    hooks_path = _Path(hooks_dir)
    if hooks_path.exists():
        load_hooks_from_directory(hooks_path)
    hook_registry.load_from_claude_settings(cwd)

    await hook_registry.execute_lifecycle_hooks(
        "SessionStart",
        {"session_id": session.session_id, "cwd": cwd, "model": model_name},
    )

    # Build augmented prompt: inject agent system prompt + memory context
    context = Context(
        cwd=cwd,
        model=model_name,
        agent_system_prompt=agent_system_prompt,
        agent_name=agent_name,
        task_type=str((session.metadata or {}).get("task_tier", "general")),
    )
    from .core.skills_loader import load_skills, format_skills_for_subagent
    system_prompt = context.build_system_prompt()
    all_skills = load_skills(cwd)
    if all_skills:
        inline_skills = format_skills_for_subagent(all_skills)
        system_prompt = f"{system_prompt}\n\n{inline_skills}"
    augmented_prompt = f"{system_prompt}\n\n---\n\n{task}"

    # Map permission_mode to claude CLI flag
    perm_flag = "bypassPermissions"
    if permission_mode is not None:
        from .core.permissions import PermissionMode as PM
        if permission_mode == PM.WORKSPACE_WRITE:
            perm_flag = "default"
        elif permission_mode == PM.DANGER_FULL_ACCESS:
            perm_flag = "bypassPermissions"

    executor = ClaudeCliExecutor()
    try:
        result = await executor.run(
            augmented_prompt,
            session_id=session.session_id,
            model=model_name,
            permission_mode=perm_flag,
            cwd=cwd,
            on_text=_print_text,
        )
        _print_text(None)  # close color escape
    finally:
        await hook_registry.execute_lifecycle_hooks(
            "Stop",
            {"session_id": session.session_id, "cwd": cwd},
        )

    # Persist session metadata
    session.metadata = dict(session.metadata or {})
    if result.usage:
        session.metadata["usage"] = _merge_usage(session.metadata.get("usage"), result.usage)
    session.metadata["last_run"] = build_last_run_metadata(
        model=model_name,
        runtime=OPENPRAX_CLAUDE_DEBUG_BRIDGE,
        extra={"tool_calls": result.tool_calls},
    )
    if result.session_id:
        session.session_id = result.session_id
    session_store.save(session)


async def _run(
    task: str,
    model_override: str | None = None,
    permission_mode: Any | None = None,
    session_id: str | None = None,
    runtime_path: str = "auto",
) -> None:
    models_config = load_models_config()
    cwd = str(Path.cwd())

    model_name, agent_name, agent_system_prompt, session, session_store = _bootstrap_session(
        cwd=cwd,
        task=task,
        model_override=model_override,
        session_id=session_id,
        models_config=models_config,
    )

    print(f"\033[90m[prax] model={model_name} cwd={cwd}\033[0m", flush=True)
    print(f"\033[90m[prax] session={session.session_id}\033[0m", flush=True)

    use_bridge = (
        runtime_path == "bridge"
        or (runtime_path == "auto" and claude_cli_available())
    )

    if runtime_path == "bridge" and not claude_cli_available():
        print(
            "\033[31mError:\033[0m --runtime-path=bridge requires `claude` CLI to be installed.",
            file=sys.stderr,
        )
        sys.exit(1)

    if use_bridge:
        print(f"\033[90m[prax] executor=claude-cli (debug bridge)\033[0m", flush=True)
        await _run_via_claude_cli(
            task,
            cwd=cwd,
            model_name=model_name,
            session=session,
            session_store=session_store,
            agent_system_prompt=agent_system_prompt,
            agent_name=agent_name,
            permission_mode=permission_mode,
            hooks_dir=str(Path(cwd) / ".prax" / "hooks"),
        )
        return

    # Native: direct LLM API path
    executor_reason = "explicit --runtime-path=native" if runtime_path == "native" else "claude CLI not found"
    print(f"\033[90m[prax] executor=direct-api ({executor_reason})\033[0m", flush=True)
    context, client, tools, middlewares = await _build_pipeline(
        cwd=cwd,
        model_name=model_name,
        models_config=models_config,
        permission_mode=permission_mode,
        agent_name=agent_name,
        agent_system_prompt=agent_system_prompt,
        session=session,
    )

    await _execute(
        task,
        context=context,
        client=client,
        tools=tools,
        middlewares=middlewares,
        models_config=models_config,
        model_name=model_name,
        session=session,
        session_store=session_store,
    )


def main() -> None:
    # Early exit for version/help commands to avoid triggering Claude CLI bridge
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "version", "-v"):
        from prax import __version__
        print(f"prax {__version__}")
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] in ("--help", "help", "-h"):
        print("Usage: prax [command] [options]")
        print("       prax prompt \"read login.py and refactor auth\"")
        print("       prax repl --session-id session_xxxxx")
        print("       prax status --session-id session_xxxxx")
        print("       prax install / doctor / repair / uninstall")
        print(format_help())
        sys.exit(0)

    if len(sys.argv) < 2:
        print("Usage: prax [command] [options]")
        print("       prax prompt \"read login.py and refactor auth\"")
        print("       prax repl --session-id session_xxxxx")
        print("       prax status --session-id session_xxxxx")
        print("       prax install / doctor / repair / uninstall")
        print(format_help())
        sys.exit(1)

    try:
        options, positional = _parse_global_args(sys.argv[1:])
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    cwd = str(Path.cwd())

    # Launch TUI if requested
    if options["tui"]:
        from .tui import launch_tui
        launch_tui(cwd=cwd)
        return

    # Delegate to cli.py for Claude Code integration subcommands
    CLI_SUBCOMMANDS = {
        "install", "doctor", "list-installed", "show-state", "history",
        "list-archives", "list-backups", "repair", "export-plugin",
        "export-marketplace", "restore", "uninstall", "debug-claude",
    }
    if positional and positional[0] in CLI_SUBCOMMANDS:
        from . import cli as cli_module
        # Reconstruct argv for cli.py's argparse
        sys.argv = ["prax"] + positional
        cli_module.main()
        return

    models_config = load_models_config(cwd)
    command_ctx = _build_command_context(
        cwd=cwd,
        models_config=models_config,
        session_id=options["session_id"],
        permission_mode=options["permission_mode"] or PermissionMode.WORKSPACE_WRITE,
        output_format=options["output_format"],
    )

    if positional and positional[0] == "repl":
        session_store = FileSessionStore(str(Path(cwd) / ".prax" / "sessions"))
        repl_session_id = options["session_id"] or session_store.create_session_id()

        def command_context_factory(active_session_id: str) -> CommandContext:
            return _build_command_context(
                cwd=cwd,
                models_config=models_config,
                session_id=active_session_id,
                permission_mode=options["permission_mode"] or PermissionMode.WORKSPACE_WRITE,
                output_format=options["output_format"],
            )

        def task_runner(next_task: str, active_session_id: str) -> None:
            _run_task_sync(
                next_task,
                model_override=options["model_override"],
                permission_mode=options["permission_mode"],
                session_id=active_session_id,
                runtime_path=options["runtime_path"],
            )

        run_repl(
            session_id=repl_session_id,
            command_context_factory=command_context_factory,
            task_runner=task_runner,
        )
        return

    if positional and positional[0] == "prompt":
        positional = positional[1:]

    command = parse_command_tokens(positional)
    if command is not None:
        if command.name == "resume" and len(command.args) >= 2:
            session_id = command.args[0]
            task = " ".join(command.args[1:])
            _run_task_sync(
                task,
                model_override=options["model_override"],
                permission_mode=options["permission_mode"],
                session_id=session_id,
                runtime_path=options["runtime_path"],
            )
        else:
            try:
                result = run_command(command, command_ctx)
            except Exception as exc:
                print(f"\033[31mError:\033[0m {exc}", file=sys.stderr)
                sys.exit(1)
            _print_command_result(result, output_format=options["output_format"])
        return

    task = " ".join(positional)
    if not task:
        print("Error: no task specified", file=sys.stderr)
        sys.exit(1)

    if _handle_slash_command(task):
        slash_command = parse_slash_command(task)
        if slash_command is None:
            sys.exit(2)
        if slash_command.name == "resume" and len(slash_command.args) >= 2:
            session_id = slash_command.args[0]
            task = " ".join(slash_command.args[1:])
            _run_task_sync(
                task,
                model_override=options["model_override"],
                permission_mode=options["permission_mode"],
                session_id=session_id,
                runtime_path=options["runtime_path"],
            )
            return
        try:
            result = run_command(slash_command, command_ctx)
        except Exception as exc:
            print(f"\033[31mError:\033[0m {exc}", file=sys.stderr)
            sys.exit(2)
        _print_command_result(result, output_format=options["output_format"])
        return

    _run_task_sync(
        task,
        model_override=options["model_override"],
        permission_mode=options["permission_mode"],
        session_id=options["session_id"],
        runtime_path=options["runtime_path"],
    )


if __name__ == "__main__":
    main()
