"""Command registry and argument parsing."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    argument_hint: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: list[str]


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("help", "Show available commands"),
    CommandSpec("status", "Show session/runtime status"),
    CommandSpec("model", "Show or set the preferred model", "[model]"),
    CommandSpec("thinking", "Show or set extended thinking mode", "[on|off]"),
    CommandSpec("reasoning", "Show or set reasoning effort", "[none|low|medium|high]"),
    CommandSpec("providers", "List configured providers and model availability"),
    CommandSpec("doctor", "Check glm/codex/claude flow readiness", "[glm|codex|claude|all] [--fix] [--write-env-file]"),
    CommandSpec("template", "Show setup templates for glm/codex/claude", "[glm|codex|claude|all]"),
    CommandSpec("init-models", "Create .prax/models.yaml (default: empty skeleton; --full seeds full schema)", "[glm|codex|claude|all] [--full] [--force] [--set-default]"),
    CommandSpec("config", "Show loaded config", "[models|rules|all]"),
    CommandSpec("permissions", "Show or set the preferred permission mode", "[mode]"),
    CommandSpec("session", "Manage sessions", "[list|show <id>|delete <id>]"),
    CommandSpec("todo", "Inspect or clear the current todo list", "[show|clear]"),
    CommandSpec("compact", "Compact a saved session", "[session-id]"),
    CommandSpec("clear", "Clear a saved session", "[session-id]"),
    CommandSpec("cost", "Show estimated token usage and cost", "[session-id]"),
    CommandSpec("resume", "Resume a saved session", "<session-id> <task...>"),
    CommandSpec("plan", "Seed todos from a task description", "<task...>"),
    CommandSpec("budget", "Show or set the token budget for this session", "[tokens]"),
    CommandSpec("skills", "List or show available skills", "[show <name>]"),
    CommandSpec("governance", "Show unified status of agents/skills/hooks/memory/solutions", ""),
    CommandSpec("runtime", "Show or set runtime path", "[native|bridge|auto]"),
)


def command_map() -> dict[str, CommandSpec]:
    mapping: dict[str, CommandSpec] = {}
    for spec in COMMAND_SPECS:
        mapping[spec.name] = spec
        for alias in spec.aliases:
            mapping[alias] = spec
    return mapping


def parse_command_tokens(tokens: list[str]) -> ParsedCommand | None:
    if not tokens:
        return None
    spec = command_map().get(tokens[0])
    if spec is None:
        return None
    return ParsedCommand(name=spec.name, args=tokens[1:])


def parse_slash_command(task: str) -> ParsedCommand | None:
    if not task.startswith("/"):
        return None
    try:
        parts = shlex.split(task)
    except ValueError:
        parts = task.split()
    if not parts:
        return None
    parts[0] = parts[0][1:]
    return parse_command_tokens(parts)


def format_help() -> str:
    lines = ["Available commands:"]
    for spec in COMMAND_SPECS:
        suffix = f" {spec.argument_hint}" if spec.argument_hint else ""
        lines.append(f"  /{spec.name}{suffix} - {spec.summary}")
    return "\n".join(lines)
