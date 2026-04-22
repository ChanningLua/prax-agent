from __future__ import annotations

from prax.commands.handlers import CommandContext
from prax.core.permissions import PermissionMode
from prax.core.session_store import FileSessionStore
from prax.core.session_store import SessionData
from prax.repl import run_repl


def test_repl_runs_tasks_and_slash_commands(tmp_path):
    events: list[tuple[str, str]] = []
    outputs: list[str] = []
    inputs = iter([
        "/help",
        "implement feature",
        "/exit",
    ])

    def input_func(_prompt: str) -> str:
        return next(inputs)

    def output_func(text: str) -> None:
        outputs.append(text)

    def command_context_factory(session_id: str) -> CommandContext:
        return CommandContext(
            cwd=str(tmp_path),
            models_config={"default_model": "gpt-5.4", "providers": {}},
            session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
            session_id=session_id,
            permission_mode=PermissionMode.WORKSPACE_WRITE,
        )

    def task_runner(task: str, session_id: str) -> None:
        events.append((session_id, task))

    run_repl(
        session_id="session_repl",
        command_context_factory=command_context_factory,
        task_runner=task_runner,
        input_func=input_func,
        output_func=output_func,
    )

    assert any("interactive session=session_repl" in item for item in outputs)
    assert any("status model:gpt-5.4 perm:workspace-write T:off R:- glm:off codex:off claude:off" in item for item in outputs)
    assert any("Available commands:" in item for item in outputs)
    assert events == [("session_repl", "implement feature")]


def test_repl_resume_switches_session(tmp_path):
    events: list[tuple[str, str]] = []
    inputs = iter([
        "/resume session_next continue this",
        "/quit",
    ])

    def input_func(_prompt: str) -> str:
        return next(inputs)

    def command_context_factory(session_id: str) -> CommandContext:
        return CommandContext(
            cwd=str(tmp_path),
            models_config={"default_model": "gpt-5.4", "providers": {}},
            session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
            session_id=session_id,
            permission_mode=PermissionMode.WORKSPACE_WRITE,
        )

    def task_runner(task: str, session_id: str) -> None:
        events.append((session_id, task))

    run_repl(
        session_id="session_start",
        command_context_factory=command_context_factory,
        task_runner=task_runner,
        input_func=input_func,
        output_func=lambda _text: None,
    )

    assert events == [("session_next", "continue this")]


def test_repl_prompt_reflects_session_preferences(tmp_path):
    prompts: list[str] = []
    store = FileSessionStore(str(tmp_path / ".prax" / "sessions"))
    store.save(
        SessionData(
            session_id="session_pref",
            cwd=str(tmp_path),
            model="claude-sonnet-4-7",
            messages=[],
            metadata={
                "preferred_model": "sonnet",
                "preferred_permission_mode": "read-only",
                "preferred_thinking_enabled": True,
                "preferred_reasoning_effort": "high",
            },
        )
    )
    inputs = iter(["/quit"])

    def input_func(prompt: str) -> str:
        prompts.append(prompt)
        return next(inputs)

    def command_context_factory(session_id: str) -> CommandContext:
        return CommandContext(
            cwd=str(tmp_path),
            models_config={"default_model": "gpt-5.4", "providers": {}},
            session_store=store,
            session_id=session_id,
            permission_mode=PermissionMode.WORKSPACE_WRITE,
        )

    run_repl(
        session_id="session_pref",
        command_context_factory=command_context_factory,
        task_runner=lambda *_args: None,
        input_func=input_func,
        output_func=lambda _text: None,
    )

    assert prompts
    assert "model:sonnet" in prompts[0]
    assert "perm:read-only" in prompts[0]
    assert "T:on" in prompts[0]
    assert "R:high" in prompts[0]
