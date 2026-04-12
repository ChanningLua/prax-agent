"""Coverage tests for prax/main.py.

Targets uncovered paths:
- _parse_global_args() — various flag combinations
- _build_tools() — tool availability filtering
- _bootstrap_session() — session creation/resume with config
- _build_pipeline() — middleware assembly
- _run_with_model_upgrades() — upgrade loop and exception path
- _run_via_claude_cli() — Claude CLI path
- main() — top-level with sys.argv variations
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from prax.main import (
    _parse_global_args,
    _build_tools,
    _bootstrap_session,
    _merge_usage,
    _handle_slash_command,
    _print_tool_call,
    _print_tool_result,
    _run_with_model_upgrades,
)
from prax.core.permissions import PermissionMode
from prax.core.stream_events import ToolMatchEvent, ToolResultEvent


# ── _parse_global_args ────────────────────────────────────────────────────────


def test_parse_global_args_empty():
    opts, pos = _parse_global_args([])
    assert opts["model_override"] is None
    assert opts["tui"] is False
    assert pos == []


def test_parse_global_args_model_flag():
    opts, pos = _parse_global_args(["--model", "gpt-4o", "do the thing"])
    assert opts["model_override"] == "gpt-4o"
    assert pos == ["do the thing"]


def test_parse_global_args_permission_mode_valid():
    opts, pos = _parse_global_args(["--permission-mode", "workspace-write"])
    assert opts["permission_mode"] == PermissionMode.WORKSPACE_WRITE


def test_parse_global_args_permission_mode_dangerous_alias():
    """'dangerous' is an alias for 'danger-full-access'."""
    opts, pos = _parse_global_args(["--permission-mode", "dangerous"])
    assert opts["permission_mode"] == PermissionMode.DANGER_FULL_ACCESS


def test_parse_global_args_permission_mode_invalid():
    opts, pos = _parse_global_args(["--permission-mode", "nonexistent-mode"])
    assert opts["permission_mode"] is None


def test_parse_global_args_session_id():
    opts, pos = _parse_global_args(["--session-id", "sess_abc123"])
    assert opts["session_id"] == "sess_abc123"


def test_parse_global_args_output_format_json():
    opts, pos = _parse_global_args(["--output-format", "json"])
    assert opts["output_format"] == "json"


def test_parse_global_args_tui_flag():
    opts, pos = _parse_global_args(["--tui"])
    assert opts["tui"] is True


def test_parse_global_args_combined():
    opts, pos = _parse_global_args([
        "--model", "claude-3",
        "--session-id", "s1",
        "--tui",
        "hello",
        "world",
    ])
    assert opts["model_override"] == "claude-3"
    assert opts["session_id"] == "s1"
    assert opts["tui"] is True
    assert pos == ["hello", "world"]


def test_parse_global_args_positional_passthrough():
    opts, pos = _parse_global_args(["fix", "the", "bug"])
    assert pos == ["fix", "the", "bug"]


# ── _merge_usage ──────────────────────────────────────────────────────────────


def test_merge_usage_both_none():
    result = _merge_usage(None, None)
    assert result == {}


def test_merge_usage_existing_none():
    result = _merge_usage(None, {"prompt_tokens": 5})
    assert result["prompt_tokens"] == 5


def test_merge_usage_latest_none():
    result = _merge_usage({"prompt_tokens": 3}, None)
    assert result["prompt_tokens"] == 3


def test_merge_usage_accumulates():
    result = _merge_usage({"total_tokens": 10}, {"total_tokens": 5})
    assert result["total_tokens"] == 15


def test_build_tools_always_includes_todo_write():
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=None, include_task_tool=False)
    tool_names = {type(t).__name__ for t in tools}
    assert "TodoWriteTool" in tool_names


def test_build_tools_no_task_tool_when_excluded():
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=None, include_task_tool=False)
    tool_names = [type(t).__name__ for t in tools]
    assert "TaskTool" not in tool_names


def test_build_tools_includes_task_tool_when_enabled():
    mock_executor = AsyncMock()
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=mock_executor, include_task_tool=True)
    tool_names = [type(t).__name__ for t in tools]
    assert "TaskTool" in tool_names


def test_build_tools_ast_grep_included_when_available():
    mock_executor = None
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=True),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=mock_executor, include_task_tool=False)
    assert flags.get("has_ast_grep") is True
    tool_names = [type(t).__name__ for t in tools]
    assert "AstGrepSearchTool" in tool_names


def test_build_tools_tmux_bash_included_when_available():
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=True),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=None, include_task_tool=False)
    assert flags.get("has_tmux_bash") is True


def test_build_tools_web_search_included_when_available():
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=True),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=None, include_task_tool=False)
    assert flags.get("has_web_search") is True


def test_build_tools_always_includes_hashline_and_sandbox():
    with (
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        tools, flags = _build_tools(cwd="/tmp", task_executor=None, include_task_tool=False)
    assert flags.get("has_hashline") is True
    assert flags.get("has_sandbox_bash") is True


# ── _handle_slash_command ────────────────────────────────────────────────────


def test_handle_slash_command_returns_false_for_non_slash():
    assert _handle_slash_command("do something") is False


def test_handle_slash_command_ralph_loop_prints_error(capsys):
    result = _handle_slash_command("/ralph-loop")
    assert result is True
    captured = capsys.readouterr()
    assert "ralph-loop" in captured.out


def test_handle_slash_command_ralph_prints_error(capsys):
    result = _handle_slash_command("/ralph")
    assert result is True
    captured = capsys.readouterr()
    assert "ralph" in captured.out


def test_handle_slash_command_unknown_prints_error(capsys):
    result = _handle_slash_command("/totally-unknown-command")
    assert result is True
    captured = capsys.readouterr()
    assert "does not support" in captured.out


# ── _print_tool_call / _print_tool_result ────────────────────────────────────


def test_print_tool_call_with_legacy_tool_call(capsys):
    tc = MagicMock()
    tc.name = "Read"
    tc.input = {"path": "/foo"}
    _print_tool_call(tc)
    out = capsys.readouterr().out
    assert "Read" in out


def test_print_tool_result_with_legacy_result(capsys):
    tc = MagicMock()
    result = MagicMock()
    result.content = "legacy result"
    result.is_error = False
    _print_tool_result(tc, result)
    out = capsys.readouterr().out
    assert "legacy result" in out


def test_bootstrap_session_creates_new_session(tmp_path):
    models_config = {
        "default_model": "gpt-4",
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com",
                "api_key_env": "OPENAI_KEY",
                "format": "openai",
                "models": [{"name": "gpt-4"}],
            }
        },
    }
    with (
        patch("prax.main.get_agent_registry") as mock_registry,
        patch("prax.main.Classifier") as mock_classifier_cls,
    ):
        mock_registry.return_value.select_for_task.return_value = None
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = "standard"
        mock_classifier.select_model.return_value = "gpt-4"
        mock_classifier_cls.return_value = mock_classifier

        model_name, agent_name, agent_prompt, session, store = _bootstrap_session(
            cwd=str(tmp_path),
            task="do something",
            model_override=None,
            session_id=None,
            models_config=models_config,
        )

    assert model_name == "gpt-4"
    assert agent_name is None
    assert agent_prompt is None
    assert session is not None
    assert session.messages == []


def test_bootstrap_session_uses_model_override(tmp_path):
    models_config = {"providers": {}}
    with (
        patch("prax.main.get_agent_registry") as mock_registry,
        patch("prax.main.Classifier") as mock_classifier_cls,
    ):
        mock_registry.return_value.select_for_task.return_value = None
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = "standard"
        mock_classifier.select_model.return_value = "default-model"
        mock_classifier_cls.return_value = mock_classifier

        model_name, *_ = _bootstrap_session(
            cwd=str(tmp_path),
            task="some task",
            model_override="claude-3-opus",
            session_id=None,
            models_config=models_config,
        )

    assert model_name == "claude-3-opus"


def test_bootstrap_session_resumes_existing_session(tmp_path):
    models_config = {"providers": {}}

    mock_session = MagicMock()
    mock_session.session_id = "existing-sess"
    mock_session.metadata = {"preferred_model": "gpt-4.1"}
    mock_session.messages = [{"role": "user", "content": "hi"}]

    with (
        patch("prax.main.get_agent_registry") as mock_registry,
        patch("prax.main.Classifier") as mock_classifier_cls,
        patch("prax.main.FileSessionStore") as mock_store_cls,
    ):
        mock_registry.return_value.select_for_task.return_value = None
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = "standard"
        mock_classifier.select_model.return_value = "default"
        mock_classifier_cls.return_value = mock_classifier

        mock_store = MagicMock()
        mock_store.load.return_value = mock_session
        mock_store.create_session_id.return_value = "new-sess"
        mock_store_cls.return_value = mock_store

        model_name, agent_name, agent_prompt, session, store = _bootstrap_session(
            cwd=str(tmp_path),
            task="continue",
            model_override=None,
            session_id="existing-sess",
            models_config=models_config,
        )

    # preferred_model from metadata should be used
    assert model_name == "gpt-4.1"


def test_bootstrap_session_selects_agent(tmp_path, capsys):
    models_config = {"providers": {}}

    mock_agent = MagicMock()
    mock_agent.name = "code-reviewer"
    mock_agent.system_prompt = "You review code."
    mock_agent.model = "gpt-4"

    with (
        patch("prax.main.get_agent_registry") as mock_registry,
        patch("prax.main.Classifier") as mock_classifier_cls,
        patch("prax.main.get_model_entry", return_value={"name": "gpt-4"}),
    ):
        mock_registry.return_value.select_for_task.return_value = mock_agent
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = "standard"
        mock_classifier.select_model.return_value = "default"
        mock_classifier_cls.return_value = mock_classifier

        model_name, agent_name, agent_prompt, session, store = _bootstrap_session(
            cwd=str(tmp_path),
            task="review my code",
            model_override=None,
            session_id=None,
            models_config=models_config,
        )

    assert agent_name == "code-reviewer"
    assert agent_prompt == "You review code."
    out = capsys.readouterr().out
    assert "code-reviewer" in out


# ── _run_with_model_upgrades ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_with_model_upgrades_no_upgrade_path_raises():
    mock_client = MagicMock()
    mock_context = MagicMock()
    mock_context.model = "gpt-4"

    with patch("prax.main.get_upgrade_path", return_value=[]):
        with pytest.raises(RuntimeError, match="No configured models"):
            await _run_with_model_upgrades(
                "task",
                context=mock_context,
                llm_client=mock_client,
                models_config={},
                initial_model="gpt-4",
                tools=[],
                middlewares=[],
                base_history=[],
            )


@pytest.mark.asyncio
async def test_run_with_model_upgrades_exception_last_attempt_reraises():
    mock_client = MagicMock()
    mock_config = MagicMock()
    mock_client.resolve_model.return_value = mock_config
    mock_context = MagicMock()
    mock_context.model = "gpt-4"

    async def fake_loop(*args, **kwargs):
        raise RuntimeError("fatal error")

    with (
        patch("prax.main.get_upgrade_path", return_value=["gpt-4"]),
        patch("prax.main.get_exception_upgrade_reason", return_value="some_reason"),
    ):
        with pytest.raises(RuntimeError, match="fatal error"):
            await _run_with_model_upgrades(
                "task",
                context=mock_context,
                llm_client=mock_client,
                models_config={},
                initial_model="gpt-4",
                tools=[],
                middlewares=[],
                base_history=[],
                run_loop=fake_loop,
            )


@pytest.mark.asyncio
async def test_run_with_model_upgrades_no_report_raises():
    mock_client = MagicMock()
    mock_config = MagicMock()
    mock_client.resolve_model.return_value = mock_config
    mock_context = MagicMock()
    mock_context.model = "gpt-4"

    async def fake_loop(*args, **kwargs):
        # No on_complete called
        return "text"

    with (
        patch("prax.main.get_upgrade_path", return_value=["gpt-4"]),
    ):
        with pytest.raises(RuntimeError, match="without completion report"):
            await _run_with_model_upgrades(
                "task",
                context=mock_context,
                llm_client=mock_client,
                models_config={},
                initial_model="gpt-4",
                tools=[],
                middlewares=[],
                base_history=[],
                run_loop=fake_loop,
            )


# ── _build_pipeline ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_pipeline_exits_when_model_not_found(tmp_path):
    from prax.main import _build_pipeline
    from prax.core.session_store import SessionData

    session = SessionData(
        session_id="s1",
        cwd=str(tmp_path),
        model="bad-model",
        messages=[],
        metadata={},
    )

    with (
        patch("prax.main.load_mcp_config", return_value=[]),
        patch("prax.main.get_model_entry", return_value=None),
        patch("prax.main.LLMClient") as mock_llm_cls,
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        patch("sys.exit") as mock_exit,
    ):
        mock_llm_cls.return_value = MagicMock()
        mock_exit.side_effect = SystemExit(1)

        with pytest.raises(SystemExit):
            await _build_pipeline(
                cwd=str(tmp_path),
                model_name="bad-model",
                models_config={},
                permission_mode=None,
                agent_name=None,
                agent_system_prompt=None,
                session=session,
            )


@pytest.mark.asyncio
async def test_build_pipeline_returns_context_client_tools_middlewares(tmp_path):
    from prax.main import _build_pipeline
    from prax.core.session_store import SessionData

    session = SessionData(
        session_id="s1",
        cwd=str(tmp_path),
        model="gpt-4",
        messages=[],
        metadata={},
    )

    mock_model_config = MagicMock()
    mock_llm = MagicMock()
    mock_llm.resolve_model.return_value = mock_model_config

    with (
        patch("prax.main.load_mcp_config", return_value=[]),
        patch("prax.main.get_model_entry", return_value={"name": "gpt-4"}),
        patch("prax.main.LLMClient", return_value=mock_llm),
        patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
        patch("prax.main.TmuxBashTool.is_available", return_value=False),
        patch("prax.main.WebSearchTool.is_available", return_value=False),
        patch("prax.main.WebCrawlerTool.is_available", return_value=False),
    ):
        context, client, tools, middlewares = await _build_pipeline(
            cwd=str(tmp_path),
            model_name="gpt-4",
            models_config={},
            permission_mode=PermissionMode.WORKSPACE_WRITE,
            agent_name=None,
            agent_system_prompt=None,
            session=session,
        )

    assert context is not None
    assert client is mock_llm
    assert len(tools) > 0
    assert len(middlewares) > 0


# ── _run_via_claude_cli ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_via_claude_cli_basic(tmp_path):
    from prax.main import _run_via_claude_cli
    from prax.core.session_store import SessionData

    session = SessionData(
        session_id="s1",
        cwd=str(tmp_path),
        model="gpt-4",
        messages=[],
        metadata={},
    )
    session_store = MagicMock()
    session_store.save = MagicMock()

    mock_result = MagicMock()
    mock_result.usage = {"total_tokens": 5}
    mock_result.tool_calls = 1
    mock_result.session_id = "new-s1"

    mock_executor = AsyncMock()
    mock_executor.run = AsyncMock(return_value=mock_result)

    mock_registry = MagicMock()
    mock_registry.execute_lifecycle_hooks = AsyncMock()

    with (
        patch("prax.main.ClaudeCliExecutor", return_value=mock_executor),
        patch("prax.core.hooks.get_hook_registry", return_value=mock_registry),
        patch("prax.main.Context") as mock_ctx_cls,
        patch("prax.main._print_text"),
    ):
        mock_ctx_cls.return_value.build_system_prompt.return_value = "sys prompt"
        with patch("prax.core.skills_loader.load_skills", return_value=[]):
            await _run_via_claude_cli(
                "do something",
                cwd=str(tmp_path),
                model_name="gpt-4",
                session=session,
                session_store=session_store,
                agent_system_prompt=None,
                agent_name=None,
                permission_mode=PermissionMode.WORKSPACE_WRITE,
                hooks_dir=str(tmp_path / ".prax" / "hooks"),
            )

    session_store.save.assert_called_once()


# ── main() top-level ─────────────────────────────────────────────────────────


def test_main_no_args_exits(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_main_repl_command(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "repl"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.run_repl") as mock_repl,
        patch("prax.main.FileSessionStore") as mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.create_session_id.return_value = "repl-sess"
        mock_store_cls.return_value = mock_store
        main()

    mock_repl.assert_called_once()


def test_main_prompt_prefix_stripped(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "prompt", "fix", "the", "bug"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.parse_command_tokens", return_value=None),
        patch("prax.main._handle_slash_command", return_value=False),
        patch("prax.main._run_task_sync") as mock_run,
    ):
        main()

    mock_run.assert_called_once()
    task_arg = mock_run.call_args[0][0]
    assert task_arg == "fix the bug"


def test_main_resume_command(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "resume", "sess123", "continue", "the", "work"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.parse_command_tokens") as mock_parse_cmd,
        patch("prax.main._run_task_sync") as mock_run,
    ):
        mock_cmd = MagicMock()
        mock_cmd.name = "resume"
        mock_cmd.args = ["sess123", "continue", "the", "work"]
        mock_parse_cmd.return_value = mock_cmd
        main()

    mock_run.assert_called_once()
    assert mock_run.call_args[1]["session_id"] == "sess123"


def test_main_no_task_exits_with_error(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "--model", "gpt-4"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.parse_command_tokens", return_value=None),
        patch("prax.main._handle_slash_command", return_value=False),
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1


def test_main_slash_command_unknown_exits(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "/unknown-slash"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.parse_command_tokens", return_value=None),
        patch("prax.main._handle_slash_command", return_value=True),
        patch("prax.main.parse_slash_command", return_value=None),
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 2


def test_main_slash_resume_runs_task(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "/resume", "sess-x", "do", "more"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.parse_command_tokens", return_value=None),
        patch("prax.main._handle_slash_command", return_value=True),
        patch("prax.main.parse_slash_command") as mock_parse_slash,
        patch("prax.main._run_task_sync") as mock_run,
    ):
        mock_slash = MagicMock()
        mock_slash.name = "resume"
        mock_slash.args = ["sess-x", "do", "more"]
        mock_parse_slash.return_value = mock_slash
        main()

    mock_run.assert_called_once()
    assert mock_run.call_args[1]["session_id"] == "sess-x"


def test_main_plain_task_runs(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "fix", "the", "bug"])
    with (
        patch("prax.main.load_models_config", return_value={}),
        patch("prax.main.parse_command_tokens", return_value=None),
        patch("prax.main._handle_slash_command", return_value=False),
        patch("prax.main._run_task_sync") as mock_run,
    ):
        main()

    mock_run.assert_called_once()
    task_arg = mock_run.call_args[0][0]
    assert task_arg == "fix the bug"
