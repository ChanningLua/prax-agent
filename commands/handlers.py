"""Builtin command handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.compaction import CompactionConfig, SUMMARY_PREFIX, compact_messages
from ..core.config_files import load_rules_config
from ..core.planning import generate_initial_plan
from ..core.todo_store import TodoItem
from ..core.model_catalog import get_model_entry, iter_model_catalog
from ..core.permissions import PermissionMode
from ..core.provider_setup import (
    build_flow_template,
    flow_names,
    load_local_models_config,
    merge_into_local_config,
    render_yaml,
    write_env_example,
    write_local_models_config,
)
from ..core.session_store import FileSessionStore, SessionData
from ..core.skills_loader import load_skills
from ..core.todo_store import TodoStore
from .registry import ParsedCommand, format_help


@dataclass
class CommandContext:
    cwd: str
    models_config: dict
    session_store: FileSessionStore
    session_id: str | None = None
    permission_mode: PermissionMode = PermissionMode.WORKSPACE_WRITE
    output_format: str = "text"


@dataclass
class CommandResult:
    text: str
    data: dict[str, Any] | None = None

    def render(self, output_format: str) -> str:
        if output_format == "json":
            return json.dumps(self.data or {"text": self.text}, indent=2, ensure_ascii=False)
        return self.text


def run_command(command: ParsedCommand, ctx: CommandContext) -> CommandResult:
    handler_name = f"_handle_{command.name.replace('-', '_')}"
    handler = globals().get(handler_name)
    if handler is None:
        raise ValueError(f"Unsupported command: {command.name}")
    return handler(command.args, ctx)


def _load_session(ctx: CommandContext, session_id: str | None) -> SessionData | None:
    effective_id = session_id or ctx.session_id
    if not effective_id:
        return None
    return ctx.session_store.load(effective_id)


def _save_session(ctx: CommandContext, session: SessionData) -> None:
    ctx.session_store.save(session)


def _session_summary(session: SessionData) -> dict[str, Any]:
    messages = session.messages or []
    metadata = session.metadata or {}
    last_run = metadata.get("last_run", {})
    first_content = messages[0]["content"] if messages else ""
    compacted = isinstance(first_content, str) and first_content.startswith(SUMMARY_PREFIX)
    usage = metadata.get("usage", {})
    return {
        "session_id": session.session_id,
        "model": session.model,
        "message_count": len(messages),
        "compacted": compacted,
        "fallback_count": len(metadata.get("upgrade_history", [])),
        "usage": usage,
        "executor": last_run.get("executor"),
        "runtime_path": last_run.get("runtime_path"),
        "integration_mode": last_run.get("integration_mode"),
    }


def _todo_store(ctx: CommandContext) -> TodoStore:
    return TodoStore(ctx.cwd)


def _handle_help(_args: list[str], _ctx: CommandContext) -> CommandResult:
    return CommandResult(text=format_help(), data={"commands": format_help().splitlines()[1:]})


def _handle_providers(_args: list[str], ctx: CommandContext) -> CommandResult:
    entries = iter_model_catalog(ctx.models_config)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault(entry.provider, []).append({
            "name": entry.name,
            "api_model": entry.api_model,
            "aliases": list(entry.aliases),
            "tier": entry.tier,
            "available": entry.available,
            "api_format": entry.api_format,
            "request_mode": entry.request_mode,
            "env_names": list(entry.env_names),
            "api_model_configured": entry.api_model_configured,
            "supports_tools": entry.supports_tools,
            "supports_streaming": entry.supports_streaming,
            "supports_reasoning_effort": entry.supports_reasoning_effort,
            "supports_thinking": entry.supports_thinking,
            "default_reasoning_effort": entry.default_reasoning_effort,
            "default_thinking_budget_tokens": entry.default_thinking_budget_tokens,
        })

    lines = []
    for provider, models in grouped.items():
        lines.append(f"{provider}:")
        for model in models:
            status = "available" if model["available"] else "missing-credentials"
            if not model["api_model_configured"]:
                status = "template"
            capability_bits = []
            if model["supports_tools"]:
                capability_bits.append("tools")
            if model["supports_streaming"]:
                capability_bits.append("stream")
            if model["supports_reasoning_effort"]:
                capability_bits.append("reasoning")
            if model["supports_thinking"]:
                capability_bits.append("thinking")
            alias_text = f", aliases={','.join(model['aliases'])}" if model["aliases"] else ""
            lines.append(
                f"  - {model['name']} ({model['tier'] or 'n/a'}, {status}, "
                f"mode={model['request_mode']}, capabilities={','.join(capability_bits) or 'none'}{alias_text})"
            )
    return CommandResult(text="\n".join(lines) or "No providers configured.", data={"providers": grouped})


def _handle_doctor(args: list[str], ctx: CommandContext) -> CommandResult:
    target = "all"
    fix = False
    force = False
    export_env_hint = False
    write_env_file = False
    set_default = False
    for arg in args:
        if arg == "--fix":
            fix = True
        elif arg == "--force":
            force = True
        elif arg == "--export-env-hint":
            export_env_hint = True
        elif arg == "--write-env-file":
            write_env_file = True
        elif arg == "--set-default":
            set_default = True
        else:
            target = arg.lower()
    flows = ["glm", "codex", "claude"] if target == "all" else [target]
    valid = {"glm", "codex", "claude"}
    if any(flow not in valid for flow in flows):
        raise ValueError("doctor target must be glm|codex|claude|all")

    catalog = iter_model_catalog(ctx.models_config)
    report: dict[str, Any] = {}
    lines: list[str] = []

    for flow in flows:
        flow_entries = _flow_entries(flow, catalog)
        env_names = sorted({env for entry in flow_entries for env in entry.env_names})
        configured = len(flow_entries) > 0
        available_entries = [entry for entry in flow_entries if entry.available]
        template_entries = [entry for entry in flow_entries if not entry.api_model_configured]
        ready = bool(available_entries)
        if not configured:
            flow_status = "not-configured"
        elif ready:
            flow_status = "ready"
        elif template_entries:
            flow_status = "template"
        else:
            flow_status = "missing-key"
        next_step = _flow_next_step(flow, configured=configured, ready=ready, has_template=bool(template_entries))

        report[flow] = {
            "status": flow_status,
            "configured": configured,
            "ready": ready,
            "env_names": env_names,
            "missing_env_names": [] if ready else env_names,
            "template_models": [entry.name for entry in template_entries],
            "models": [entry.name for entry in flow_entries],
            "available_models": [entry.name for entry in available_entries],
            "next_step": next_step,
        }
        if export_env_hint and env_names:
            report[flow]["export_hints"] = [f"export {env_name}=<your-{env_name.lower()}>" for env_name in env_names]

        lines.append(f"{flow}: {flow_status}")
        lines.append(f"  configured_models={report[flow]['models'] or []}")
        lines.append(f"  env_names={env_names or []}")
        lines.append(f"  next_step={next_step}")
        if export_env_hint and env_names:
            lines.append(f"  export_hints={report[flow]['export_hints']}")

    if fix:
        fixed_flow = target
        merged = merge_into_local_config(
            load_local_models_config(ctx.cwd),
            fixed_flow,
            overwrite=force,
            set_default=set_default,
        )
        path = write_local_models_config(ctx.cwd, merged)
        fix_data: dict[str, Any] = {"path": str(path), "flow": fixed_flow, "force": force, "set_default": set_default}
        if write_env_file:
            env_path = write_env_example(ctx.cwd, fixed_flow)
            fix_data["env_path"] = str(env_path)
        report["fix"] = fix_data
        lines.append(f"fix: wrote {path}")
        if write_env_file:
            lines.append(f"fix: wrote {fix_data['env_path']}")

    return CommandResult(text="\n".join(lines), data={"flows": report})


def _handle_template(args: list[str], _ctx: CommandContext) -> CommandResult:
    target = (args[0].lower() if args else "all")
    if target == "all":
        ordered = ["glm", "codex", "claude"]
        data = {name: render_yaml(build_flow_template(name)) for name in ordered}
        text = "\n\n".join(f"# {name}\n{data[name]}" for name in ordered)
        return CommandResult(text=text, data={"templates": data})
    if target not in {"glm", "codex", "claude"}:
        raise ValueError("template target must be glm|codex|claude|all")
    template = render_yaml(build_flow_template(target))
    return CommandResult(text=template, data={"template": template})


def _handle_init_models(args: list[str], ctx: CommandContext) -> CommandResult:
    flow = (args[0].lower() if args else "all")
    force = "--force" in args
    set_default = "--set-default" in args
    if flow == "--force":
        flow = "all"
    if flow == "--set-default":
        flow = "all"
    if flow not in {"glm", "codex", "claude", "all"}:
        raise ValueError("init-models target must be glm|codex|claude|all")
    merged = merge_into_local_config(
        load_local_models_config(ctx.cwd),
        flow,
        overwrite=force,
        set_default=set_default,
    )
    path = write_local_models_config(ctx.cwd, merged)
    data = {"path": str(path), "flow": flow, "force": force, "set_default": set_default}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_config(args: list[str], ctx: CommandContext) -> CommandResult:
    section = args[0] if args else "all"
    rules_config = load_rules_config(ctx.cwd)
    data = {
        "models": ctx.models_config,
        "rules": rules_config,
    }
    if section == "all":
        payload = data
    else:
        payload = {section: data.get(section)}
        if payload[section] is None:
            raise ValueError(f"Unknown config section: {section}")
    return CommandResult(text=json.dumps(payload, indent=2, ensure_ascii=False), data=payload)


def _handle_status(_args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, None)
    todos = [todo.to_dict() for todo in _todo_store(ctx).load()]
    if session is None:
        entry = get_model_entry(str(ctx.models_config.get("default_model")), ctx.models_config)
        data = {
            "cwd": ctx.cwd,
            "session_id": ctx.session_id,
            "default_model": ctx.models_config.get("default_model"),
            "provider": entry.provider if entry else None,
            "api_model": entry.api_model if entry else None,
            "request_mode": entry.request_mode if entry else None,
            "supports_thinking": entry.supports_thinking if entry else False,
            "supports_reasoning_effort": entry.supports_reasoning_effort if entry else False,
            "permission_mode": ctx.permission_mode.value,
            "thinking_enabled": False,
            "reasoning_effort": None,
            "flow_status": get_flow_status_summary(ctx),
            "todos": todos,
        }
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)

    summary = _session_summary(session)
    effective_model = (
        (session.metadata or {}).get("preferred_model")
        or session.model
        or ctx.models_config.get("default_model")
    )
    entry = get_model_entry(str(effective_model), ctx.models_config)
    summary["cwd"] = session.cwd
    summary["provider"] = entry.provider if entry else None
    summary["api_model"] = entry.api_model if entry else None
    summary["request_mode"] = entry.request_mode if entry else None
    summary["api_model_configured"] = entry.api_model_configured if entry else False
    summary["aliases"] = list(entry.aliases) if entry else []
    summary["supports_tools"] = entry.supports_tools if entry else False
    summary["supports_streaming"] = entry.supports_streaming if entry else False
    summary["supports_thinking"] = entry.supports_thinking if entry else False
    summary["supports_reasoning_effort"] = entry.supports_reasoning_effort if entry else False
    summary["permission_mode"] = (session.metadata or {}).get("preferred_permission_mode", ctx.permission_mode.value)
    summary["thinking_enabled"] = bool((session.metadata or {}).get("preferred_thinking_enabled", False))
    summary["reasoning_effort"] = (session.metadata or {}).get("preferred_reasoning_effort")
    summary["flow_status"] = get_flow_status_summary(ctx)
    summary["todos"] = todos
    return CommandResult(text=json.dumps(summary, indent=2, ensure_ascii=False), data=summary)


def _handle_model(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, None)
    if not args:
        preferred = (session.metadata or {}).get("preferred_model") if session else None
        entry = get_model_entry(str(preferred or ctx.models_config.get("default_model")), ctx.models_config)
        data = {
            "default_model": ctx.models_config.get("default_model"),
            "preferred_model": preferred,
            "session_model": session.model if session else None,
            "api_model": entry.api_model if entry else None,
            "request_mode": entry.request_mode if entry else None,
            "api_model_configured": entry.api_model_configured if entry else False,
            "aliases": list(entry.aliases) if entry else [],
            "supports_thinking": entry.supports_thinking if entry else False,
            "supports_reasoning_effort": entry.supports_reasoning_effort if entry else False,
        }
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)

    model_name = args[0]
    entry = get_model_entry(model_name, ctx.models_config)
    if entry is None:
        raise ValueError(f"Unknown model: {model_name}")
    if session is None:
        effective_id = ctx.session_id or ctx.session_store.create_session_id()
        session = SessionData(
            session_id=effective_id,
            cwd=ctx.cwd,
            model=model_name,
            messages=[],
            metadata={},
        )
    session.metadata = dict(session.metadata or {})
    session.metadata["preferred_model"] = model_name
    session.model = model_name
    _save_session(ctx, session)
    data = {"session_id": session.session_id, "preferred_model": model_name}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_thinking(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, None)
    if not args:
        enabled = bool((session.metadata or {}).get("preferred_thinking_enabled", False)) if session else False
        data = {"preferred_thinking_enabled": enabled}
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)

    value = args[0].lower()
    if value not in {"on", "off"}:
        raise ValueError("thinking must be 'on' or 'off'")
    enabled = value == "on"
    if session is None:
        effective_id = ctx.session_id or ctx.session_store.create_session_id()
        session = SessionData(
            session_id=effective_id,
            cwd=ctx.cwd,
            model=ctx.models_config.get("default_model"),
            messages=[],
            metadata={},
        )
    session.metadata = dict(session.metadata or {})
    session.metadata["preferred_thinking_enabled"] = enabled
    _save_session(ctx, session)
    data = {"session_id": session.session_id, "preferred_thinking_enabled": enabled}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_reasoning(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, None)
    if not args:
        effort = (session.metadata or {}).get("preferred_reasoning_effort") if session else None
        data = {"preferred_reasoning_effort": effort}
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)

    effort = args[0].lower()
    if effort not in {"none", "low", "medium", "high"}:
        raise ValueError("reasoning must be one of none|low|medium|high")
    if session is None:
        effective_id = ctx.session_id or ctx.session_store.create_session_id()
        session = SessionData(
            session_id=effective_id,
            cwd=ctx.cwd,
            model=ctx.models_config.get("default_model"),
            messages=[],
            metadata={},
        )
    session.metadata = dict(session.metadata or {})
    session.metadata["preferred_reasoning_effort"] = effort
    _save_session(ctx, session)
    data = {"session_id": session.session_id, "preferred_reasoning_effort": effort}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_permissions(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, None)
    if not args:
        preferred = (session.metadata or {}).get("preferred_permission_mode") if session else None
        data = {
            "default_permission_mode": ctx.permission_mode.value,
            "preferred_permission_mode": preferred,
        }
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)

    mode = PermissionMode(args[0])
    if session is None:
        effective_id = ctx.session_id or ctx.session_store.create_session_id()
        session = SessionData(
            session_id=effective_id,
            cwd=ctx.cwd,
            model=ctx.models_config.get("default_model"),
            messages=[],
            metadata={},
        )
    session.metadata = dict(session.metadata or {})
    session.metadata["preferred_permission_mode"] = mode.value
    _save_session(ctx, session)
    data = {"session_id": session.session_id, "preferred_permission_mode": mode.value}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_runtime(args: list[str], ctx: CommandContext) -> CommandResult:
    """Show or set the preferred runtime path."""
    session = _load_session(ctx, None)

    if not args:
        # Show current preferred_runtime_path
        preferred = (session.metadata or {}).get("preferred_runtime_path") if session else None
        current = preferred or "native (default)"
        return CommandResult(
            text=f"Current runtime path: {current}\n"
                 f"Available: native, bridge, auto\n"
                 f"Use '/runtime <path>' to change"
        )

    # Set preferred_runtime_path
    runtime_path = args[0].lower()
    if runtime_path not in ("native", "bridge", "auto"):
        return CommandResult(
            text=f"Invalid runtime path: {runtime_path}\n"
                 f"Must be one of: native, bridge, auto"
        )

    if not session:
        effective_id = ctx.session_id or ctx.session_store.create_session_id()
        session = SessionData(
            session_id=effective_id,
            cwd=ctx.cwd,
            model=ctx.models_config.get("default_model"),
            messages=[],
            metadata={},
        )

    session.metadata = dict(session.metadata or {})
    session.metadata["preferred_runtime_path"] = runtime_path
    _save_session(ctx, session)

    return CommandResult(
        text=f"Runtime path set to: {runtime_path}\n"
             f"This will be used for future commands in this session"
    )


def _handle_session(args: list[str], ctx: CommandContext) -> CommandResult:
    action = args[0] if args else "list"
    if action == "list":
        items = []
        root = ctx.session_store.root_dir
        if root.exists():
            for path in sorted(root.glob("session_*.json")):
                session = ctx.session_store.load(path.stem)
                if session is None:
                    continue
                items.append(_session_summary(session))
        return CommandResult(text=json.dumps({"sessions": items}, indent=2, ensure_ascii=False), data={"sessions": items})

    if len(args) < 2:
        raise ValueError("session command requires an id for show/delete")
    session_id = args[1]
    if action == "show":
        session = _load_session(ctx, session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        data = _session_summary(session)
        data["cwd"] = session.cwd
        data["metadata"] = session.metadata or {}
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)
    if action == "delete":
        path = ctx.session_store.session_path(session_id)
        if not path.exists():
            raise ValueError(f"Session not found: {session_id}")
        path.unlink()
        data = {"deleted": session_id}
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)
    raise ValueError(f"Unsupported session action: {action}")


def _handle_todo(args: list[str], ctx: CommandContext) -> CommandResult:
    action = args[0] if args else "show"
    store = _todo_store(ctx)
    if action == "show":
        todos = [todo.to_dict() for todo in store.load()]
        data = {"todos": todos}
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)
    if action == "clear":
        store.clear()
        data = {"cleared": True}
        return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)
    raise ValueError("todo action must be show|clear")


def _handle_compact(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, args[0] if args else None)
    if session is None:
        raise ValueError("No session found to compact")
    before = len(session.messages or [])
    session.messages = compact_messages(session.messages or [], CompactionConfig())
    _save_session(ctx, session)
    data = {"session_id": session.session_id, "before": before, "after": len(session.messages)}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_clear(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, args[0] if args else None)
    if session is None:
        raise ValueError("No session found to clear")
    session.messages = []
    session.metadata = {
        key: value
        for key, value in (session.metadata or {}).items()
        if key in {"preferred_model", "preferred_permission_mode"}
    }
    _save_session(ctx, session)
    data = {"session_id": session.session_id, "cleared": True}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_cost(args: list[str], ctx: CommandContext) -> CommandResult:
    session = _load_session(ctx, args[0] if args else None)
    if session is None:
        raise ValueError("No session found for cost inspection")
    metadata = session.metadata or {}
    usage = dict(metadata.get("usage", {}))
    total_tokens = int(usage.get("total_tokens", 0))
    model_name = session.model or metadata.get("preferred_model") or ctx.models_config.get("default_model")
    entry = get_model_entry(str(model_name), ctx.models_config) if model_name else None
    estimated_cost = None
    if entry and entry.cost_per_1m_tokens is not None:
        estimated_cost = round(total_tokens * entry.cost_per_1m_tokens / 1_000_000, 6)
    data = {
        "session_id": session.session_id,
        "model": model_name,
        "usage": usage,
        "estimated_cost": estimated_cost,
    }
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_resume(args: list[str], ctx: CommandContext) -> CommandResult:
    if not args:
        raise ValueError("resume requires a session id")
    session = _load_session(ctx, args[0])
    if session is None:
        raise ValueError(f"Session not found: {args[0]}")
    data = {
        "session_id": session.session_id,
        "model": session.model,
        "message_count": len(session.messages or []),
        "resume_hint": f"prax --session-id {session.session_id} <task>",
    }
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _handle_plan(args: list[str], ctx: CommandContext) -> CommandResult:
    task = " ".join(args).strip()
    if not task:
        raise ValueError("plan requires a task description")
    planned = generate_initial_plan(task)
    todos = [TodoItem(content=p.content, active_form=p.active_form, status=p.status) for p in planned]
    _todo_store(ctx).save(todos)
    data = {"task": task, "todos": [t.to_dict() for t in todos]}
    return CommandResult(text=json.dumps(data, indent=2, ensure_ascii=False), data=data)


def _flow_entries(flow: str, catalog: list[Any]) -> list[Any]:
    if flow == "glm":
        return [entry for entry in catalog if entry.provider == "zhipu" or entry.name.startswith("glm")]
    if flow == "claude":
        return [entry for entry in catalog if entry.provider == "anthropic" or entry.name.startswith("claude")]
    if flow == "codex":
        return [entry for entry in catalog if entry.request_mode == "responses"]
    return []


def get_flow_status_summary(ctx: CommandContext) -> str:
    catalog = iter_model_catalog(ctx.models_config)
    parts: list[str] = []
    for flow in ("glm", "codex", "claude"):
        entries = _flow_entries(flow, catalog)
        if not entries:
            status = "off"
        elif any(entry.available for entry in entries):
            status = "ready"
        elif any(not entry.api_model_configured for entry in entries):
            status = "template"
        else:
            status = "missing-key"
        parts.append(f"{flow}:{status}")
    return " ".join(parts)


def get_repl_runtime_summary(ctx: CommandContext) -> str:
    session = _load_session(ctx, None)
    preferred_model = None if session is None else (session.metadata or {}).get("preferred_model")
    preferred_permission = None if session is None else (session.metadata or {}).get("preferred_permission_mode")
    thinking_enabled = bool((session.metadata or {}).get("preferred_thinking_enabled", False)) if session else False
    reasoning_effort = (session.metadata or {}).get("preferred_reasoning_effort") if session else None
    model_text = str(preferred_model or (session.model if session else ctx.models_config.get("default_model") or "-"))
    permission_text = str(preferred_permission or ctx.permission_mode.value)
    thinking_text = "on" if thinking_enabled else "off"
    reasoning_text = str(reasoning_effort or "-")
    return f"model:{model_text} perm:{permission_text} T:{thinking_text} R:{reasoning_text} {get_flow_status_summary(ctx)}"


def _flow_next_step(flow: str, *, configured: bool, ready: bool, has_template: bool) -> str:
    if ready:
        return f"Run `prax providers` or start a session with the {flow} model."
    if not configured:
        return f"Run `prax template {flow}` and copy the snippet into .prax/models.yaml."
    if has_template:
        return f"Replace the template api_model for {flow} in .prax/models.yaml, then export credentials."
    return f"Export the required credential env vars and retry `prax doctor {flow}`."


def _handle_budget(args: list[str], ctx: CommandContext) -> CommandResult:
    """Show or set the token budget for this session."""
    session = _load_session(ctx, None)

    if not args:
        # Show current budget
        current_budget = (session.metadata or {}).get("max_budget_tokens") if session else None
        data = {"max_budget_tokens": current_budget}
        if current_budget is None:
            text = "No token budget set (unlimited)"
        else:
            text = f"Token budget: {current_budget:,} tokens"
        return CommandResult(text=text, data=data)

    # Set budget
    try:
        budget = int(args[0])
        if budget <= 0:
            raise ValueError("Budget must be positive")
    except ValueError:
        return CommandResult(
            text=f"Error: budget must be a positive integer, got: {args[0]}",
            data={"error": "invalid_budget"}
        )

    if session is None:
        effective_id = ctx.session_id or ctx.session_store.create_session_id()
        session = SessionData(
            session_id=effective_id,
            cwd=ctx.cwd,
            model=ctx.models_config.get("default_model"),
            messages=[],
            metadata={},
        )

    session.metadata = dict(session.metadata or {})
    session.metadata["max_budget_tokens"] = budget
    _save_session(ctx, session)

    data = {"session_id": session.session_id, "max_budget_tokens": budget}
    return CommandResult(
        text=f"Token budget set to {budget:,} tokens",
        data=data
    )


def _handle_skills(args: list[str], ctx: CommandContext) -> CommandResult:
    """List or show available skills from .prax/skills/."""
    skills = load_skills(ctx.cwd)

    if not skills:
        return CommandResult(
            text="No skills found. Create .prax/skills/ with SKILL.md files.",
            data={"skills": []}
        )

    if args and args[0] == "show" and len(args) > 1:
        # Show full content of a specific skill
        skill_name = args[1]
        skill = next((s for s in skills if s.name == skill_name), None)
        if skill is None:
            available = ", ".join(s.name for s in skills)
            return CommandResult(
                text=f"Skill '{skill_name}' not found. Available: {available}",
                data={"error": "not_found"}
            )
        return CommandResult(
            text=f"# {skill.name}\n\n{skill.content}",
            data={"name": skill.name, "content": skill.content, "path": skill.path}
        )

    # List all skills
    lines = ["Available skills:"]
    for skill in skills:
        desc = f" - {skill.description}" if skill.description else ""
        lines.append(f"  {skill.name}{desc}")

    data = {"skills": [{"name": s.name, "description": s.description} for s in skills]}
    return CommandResult(text="\n".join(lines), data=data)


def _handle_governance(_args: list[str], ctx: CommandContext) -> CommandResult:
    """Show unified status of all governance objects: agents, skills, hooks, memory, solutions, quality-gates."""
    prax_dir = Path(ctx.cwd) / ".prax"

    # Agents
    agents_dir = prax_dir / "agents"
    agent_count = len(list(agents_dir.glob("*.md"))) if agents_dir.exists() else 0

    # Skills
    skills = load_skills(ctx.cwd)
    skill_count = len(skills)

    # Hooks (from .claude/settings.json)
    hook_count = 0
    settings_path = Path(ctx.cwd) / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hooks = settings.get("hooks", {})
            hook_count = sum(len(v) if isinstance(v, list) else 1 for v in hooks.values())
        except Exception:
            pass

    # Memory facts
    fact_count = 0
    memory_path = prax_dir / "memory.json"
    if memory_path.exists():
        try:
            memory_data = json.loads(memory_path.read_text(encoding="utf-8"))
            fact_count = len(memory_data.get("facts", []))
        except Exception:
            pass

    # Solutions
    solutions_dir = prax_dir / "solutions"
    solution_count = len(list(solutions_dir.glob("*.md"))) if solutions_dir.exists() else 0

    # Quality gates (from rules.yaml)
    qg_commands = 0
    qg_completion = 0
    rules_path = prax_dir / "rules.yaml"
    if rules_path.exists():
        try:
            import yaml as _yaml
            rules = _yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
            qg = rules.get("quality_gates", {})
            qg_commands = len(qg.get("commands", []))
            qg_completion = len(qg.get("completion_checks", []))
        except Exception:
            pass

    lines = [
        "[prax governance]",
        f"  agents:        {agent_count:3d} loaded  ({agents_dir})",
        f"  skills:        {skill_count:3d} loaded  ({prax_dir / 'skills'})",
        f"  hooks:         {hook_count:3d} loaded  ({settings_path})",
        f"  memory:        {fact_count:3d} facts   ({memory_path})",
        f"  solutions:     {solution_count:3d} files   ({solutions_dir})",
        f"  quality-gates: commands={qg_commands}, completion_checks={qg_completion}",
    ]

    data = {
        "agents": agent_count,
        "skills": skill_count,
        "hooks": hook_count,
        "memory_facts": fact_count,
        "solutions": solution_count,
        "quality_gates": {"commands": qg_commands, "completion_checks": qg_completion},
    }
    return CommandResult(text="\n".join(lines), data=data)
