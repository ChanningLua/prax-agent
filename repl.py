"""Interactive REPL for Prax sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from .commands.handlers import CommandContext, get_repl_runtime_summary, run_command
from .commands.registry import parse_slash_command


@dataclass
class ReplState:
    session_id: str
    running: bool = True


def _build_prompt(session_id: str, runtime_summary: str) -> str:
    return f"prax[{session_id} {runtime_summary}]> "


def run_repl(
    *,
    session_id: str,
    command_context_factory: Callable[[str], CommandContext],
    task_runner: Callable[[str, str], None],
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """Run a simple interactive REPL with slash-command support."""
    state = ReplState(session_id=session_id)

    output_func(f"[prax] interactive session={state.session_id}")
    output_func(f"[prax] status {get_repl_runtime_summary(command_context_factory(state.session_id))}")
    output_func("Type a task, `/help`, or `/exit` to quit.")

    while state.running:
        try:
            runtime_summary = get_repl_runtime_summary(command_context_factory(state.session_id))
            raw = input_func(_build_prompt(state.session_id, runtime_summary))
        except EOFError:
            output_func("")
            break
        except KeyboardInterrupt:
            output_func("")
            break

        task = raw.strip()
        if not task:
            continue

        if task in {"/exit", "/quit"}:
            state.running = False
            continue

        if task.startswith("/"):
            if task.startswith("/resume "):
                slash_command = parse_slash_command(task)
                if slash_command is None or len(slash_command.args) < 2:
                    output_func("Error: /resume requires <session-id> <task>")
                    continue
                next_session_id = slash_command.args[0]
                next_task = " ".join(slash_command.args[1:])
                task_runner(next_task, next_session_id)
                state.session_id = next_session_id
                continue

            slash_command = parse_slash_command(task)
            if slash_command is None:
                output_func(f"Error: unknown slash command: {task}")
                continue
            try:
                result = run_command(slash_command, command_context_factory(state.session_id))
            except Exception as exc:
                output_func(f"Error: {exc}")
                continue
            output_func(result.render(command_context_factory(state.session_id).output_format))
            continue

        try:
            task_runner(task, state.session_id)
        except Exception as exc:
            output_func(f"Error: {exc}")
