"""Unit tests for prax/main.py.

All tests are pure unit tests — no real LLM calls, no real filesystem I/O
beyond tmp_path, no subprocess execution.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from prax.main import (
    _parse_global_args,
    _build_tools,
    _make_task_executor,
    _bootstrap_session,
    _print_tool_call,
    _print_tool_result,
    _print_text,
    _handle_slash_command,
    _merge_usage,
    _run_with_model_upgrades,
)
from prax.core.permissions import PermissionMode
from prax.core.stream_events import ToolMatchEvent, ToolResultEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_models_config(model_name: str = "test-model") -> dict:
    return {
        "default_model": model_name,
        "models": {
            model_name: {"provider": "test", "api_key": "k"},
        },
    }


# ---------------------------------------------------------------------------
# TestPrintHelpers
# ---------------------------------------------------------------------------


class TestPrintHelpers:
    def test_print_tool_call_tool_match_event(self, capsys):
        event = MagicMock(spec=ToolMatchEvent)
        event.tool_name = "Bash"
        event.tool_input = {"command": "ls"}
        _print_tool_call(event)
        out = capsys.readouterr().out
        assert "Bash" in out

    def test_print_tool_call_legacy_object(self, capsys):
        legacy = MagicMock()
        del legacy.__class__  # not a ToolMatchEvent
        legacy.name = "Read"
        legacy.input = "some input"
        # Bypass isinstance check: just ensure no crash and output contains name
        _print_tool_call(legacy)
        out = capsys.readouterr().out
        # output should contain the tool name
        assert "Read" in out

    def test_print_tool_call_truncates_long_params(self, capsys):
        event = MagicMock(spec=ToolMatchEvent)
        event.tool_name = "Bash"
        event.tool_input = {"command": "x" * 500}
        _print_tool_call(event)
        out = capsys.readouterr().out
        # truncated at 120 chars + "..."
        assert "..." in out

    def test_print_tool_result_error_result(self, capsys):
        result_event = MagicMock(spec=ToolResultEvent)
        result_event.content_preview = "Permission denied"
        result_event.is_error = True
        _print_tool_result(result_event)
        out = capsys.readouterr().out
        assert "Permission denied" in out

    def test_print_tool_result_success_result(self, capsys):
        result_event = MagicMock(spec=ToolResultEvent)
        result_event.content_preview = "file content here"
        result_event.is_error = False
        _print_tool_result(result_event)
        out = capsys.readouterr().out
        assert "file content here" in out

    def test_print_tool_result_with_legacy_result(self, capsys):
        call_event = MagicMock()
        legacy_result = MagicMock()
        legacy_result.content = "result content"
        legacy_result.is_error = False
        _print_tool_result(call_event, legacy_result)
        out = capsys.readouterr().out
        assert "result content" in out

    def test_print_tool_result_truncates_long_content(self, capsys):
        result_event = MagicMock(spec=ToolResultEvent)
        result_event.content_preview = "y" * 500
        result_event.is_error = False
        _print_tool_result(result_event)
        out = capsys.readouterr().out
        assert "..." in out

    def test_print_text_streams_text(self, capsys):
        import prax.main as main_mod
        main_mod._stream_started = False
        _print_text("hello world")
        out = capsys.readouterr().out
        assert "hello world" in out
        main_mod._stream_started = False

    def test_print_text_none_closes_stream(self, capsys):
        import prax.main as main_mod
        main_mod._stream_started = True
        _print_text(None)
        out = capsys.readouterr().out
        assert "\033[0m" in out
        assert main_mod._stream_started is False

    def test_print_text_none_when_not_started_is_noop(self, capsys):
        import prax.main as main_mod
        main_mod._stream_started = False
        _print_text(None)
        out = capsys.readouterr().out
        assert out == ""


# ---------------------------------------------------------------------------
# TestParseGlobalArgs
# ---------------------------------------------------------------------------


class TestParseGlobalArgs:
    def test_no_args_returns_defaults(self):
        opts, pos = _parse_global_args([])
        assert opts["model_override"] is None
        assert opts["permission_mode"] is None
        assert opts["session_id"] is None
        assert opts["output_format"] == "text"
        assert opts["tui"] is False
        assert pos == []

    def test_model_flag(self):
        opts, pos = _parse_global_args(["--model", "gpt-4"])
        assert opts["model_override"] == "gpt-4"
        assert pos == []

    def test_permission_mode_workspace_write(self):
        opts, _ = _parse_global_args(["--permission-mode", "workspace-write"])
        assert opts["permission_mode"] == PermissionMode.WORKSPACE_WRITE

    def test_permission_mode_danger(self):
        opts, _ = _parse_global_args(["--permission-mode", "danger-full-access"])
        assert opts["permission_mode"] == PermissionMode.DANGER_FULL_ACCESS

    def test_permission_mode_alias_dangerous(self):
        opts, _ = _parse_global_args(["--permission-mode", "dangerous"])
        assert opts["permission_mode"] == PermissionMode.DANGER_FULL_ACCESS

    def test_permission_mode_invalid_becomes_none(self):
        opts, _ = _parse_global_args(["--permission-mode", "not-valid"])
        assert opts["permission_mode"] is None

    def test_session_id_flag(self):
        opts, _ = _parse_global_args(["--session-id", "sess_abc123"])
        assert opts["session_id"] == "sess_abc123"

    def test_output_format_flag(self):
        opts, _ = _parse_global_args(["--output-format", "json"])
        assert opts["output_format"] == "json"

    def test_tui_flag(self):
        opts, _ = _parse_global_args(["--tui"])
        assert opts["tui"] is True

    def test_positional_args_collected(self):
        opts, pos = _parse_global_args(["do", "this", "task"])
        assert pos == ["do", "this", "task"]

    def test_flags_and_positional_mixed(self):
        opts, pos = _parse_global_args(["--model", "m1", "write", "tests"])
        assert opts["model_override"] == "m1"
        assert pos == ["write", "tests"]

    def test_multiple_flags(self):
        opts, pos = _parse_global_args([
            "--model", "claude-3",
            "--session-id", "s1",
            "--permission-mode", "read-only",
            "do", "something",
        ])
        assert opts["model_override"] == "claude-3"
        assert opts["session_id"] == "s1"
        assert opts["permission_mode"] == PermissionMode.READ_ONLY
        assert pos == ["do", "something"]


# ---------------------------------------------------------------------------
# TestBuildTools
# ---------------------------------------------------------------------------


class TestBuildTools:
    def test_always_includes_todo_write_tool(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "TodoWriteTool" in tool_names

    def test_task_tool_included_when_executor_provided(self, tmp_path):
        executor = MagicMock()
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, _ = _build_tools(
                cwd=str(tmp_path), task_executor=executor, include_task_tool=True
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "TaskTool" in tool_names

    def test_task_tool_excluded_when_include_false(self, tmp_path):
        executor = MagicMock()
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, _ = _build_tools(
                cwd=str(tmp_path), task_executor=executor, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "TaskTool" not in tool_names

    def test_background_tools_included_with_task_tool(self, tmp_path):
        executor = MagicMock()
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, _ = _build_tools(
                cwd=str(tmp_path), task_executor=executor, include_task_tool=True
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "StartTaskTool" in tool_names
        assert "CheckTaskTool" in tool_names

    def test_ast_grep_included_when_available(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=True),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "AstGrepSearchTool" in tool_names
        assert flags.get("has_ast_grep") is True

    def test_ast_grep_excluded_when_unavailable(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        assert "has_ast_grep" not in flags

    def test_tmux_bash_included_when_available(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=True),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "TmuxBashTool" in tool_names
        assert flags.get("has_tmux_bash") is True

    def test_hashline_always_included(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "HashlineReadTool" in tool_names
        assert "HashlineEditTool" in tool_names
        assert flags.get("has_hashline") is True

    def test_sandbox_bash_always_included(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "SandboxBashTool" in tool_names
        assert flags.get("has_sandbox_bash") is True

    def test_verify_command_always_included(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=False),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "VerifyCommandTool" in tool_names
        assert flags.get("has_verify_command") is True

    def test_web_search_included_when_available(self, tmp_path):
        with (
            patch("prax.main.AstGrepSearchTool.is_available", return_value=False),
            patch("prax.main.TmuxBashTool.is_available", return_value=False),
            patch("prax.main.WebSearchTool.is_available", return_value=True),
            patch("prax.main.WebCrawlerTool.is_available", return_value=False),
        ):
            tools, flags = _build_tools(
                cwd=str(tmp_path), task_executor=None, include_task_tool=False
            )
        tool_names = [type(t).__name__ for t in tools]
        assert "WebSearchTool" in tool_names
        assert flags.get("has_web_search") is True


class TestTaskExecutor:
    @pytest.mark.asyncio
    async def test_task_executor_inherits_parent_permission_mode(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        nested = workspace / "nested"
        nested.mkdir()

        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        async def _fake_run(*args, **kwargs):
            return ("test-model", "done", [], [], MagicMock())

        with (
            patch("prax.main.LLMClient", return_value=mock_client),
            patch("prax.main._build_tools", return_value=([], {})),
            patch("prax.main.get_agent_registry") as mock_registry,
            patch("prax.main._run_with_model_upgrades", side_effect=_fake_run) as mock_run,
            patch("prax.main.PermissionGuardMiddleware") as mock_guard,
        ):
            mock_registry.return_value.get_by_name.return_value = None
            mock_registry.return_value.select_for_task.return_value = None
            executor = _make_task_executor(
                cwd=str(workspace),
                models_config={},
                permission_mode=PermissionMode.READ_ONLY,
                parent_model="test-model",
            )

            await executor(
                "Inspect repo",
                f"Working directory: {nested}\nRead files only",
                "explore",
                2,
            )

        mock_guard.assert_called_once_with(permission_mode=PermissionMode.READ_ONLY)
        assert mock_run.call_args.kwargs["context"].cwd == str(nested)

    @pytest.mark.asyncio
    async def test_task_executor_ignores_outside_workspace_cwd_without_full_access(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        async def _fake_run(*args, **kwargs):
            return ("test-model", "done", [], [], MagicMock())

        with (
            patch("prax.main.LLMClient", return_value=mock_client),
            patch("prax.main._build_tools", return_value=([], {})),
            patch("prax.main.get_agent_registry") as mock_registry,
            patch("prax.main._run_with_model_upgrades", side_effect=_fake_run) as mock_run,
        ):
            mock_registry.return_value.get_by_name.return_value = None
            mock_registry.return_value.select_for_task.return_value = None
            executor = _make_task_executor(
                cwd=str(workspace),
                models_config={},
                permission_mode=PermissionMode.WORKSPACE_WRITE,
                parent_model="test-model",
            )

            await executor(
                "Inspect repo",
                f"Working directory: {outside}\nTry to leave workspace",
                "explore",
                2,
            )

        assert mock_run.call_args.kwargs["context"].cwd == str(workspace)


# ---------------------------------------------------------------------------
# TestBootstrapSession
# ---------------------------------------------------------------------------


class TestBootstrapSession:
    def test_creates_new_session_when_none_exists(self, tmp_path):
        models_config = _fake_models_config("test-model")
        with (
            patch("prax.main.FileSessionStore") as MockStore,
            patch("prax.main.Classifier") as MockClassifier,
            patch("prax.main.get_agent_registry") as MockRegistry,
        ):
            mock_store = MagicMock()
            mock_store.load.return_value = None
            mock_store.create_session_id.return_value = "session_new_123"
            MockStore.return_value = mock_store

            mock_classifier = MagicMock()
            mock_classifier.classify.return_value = "standard"
            mock_classifier.select_model.return_value = "test-model"
            MockClassifier.return_value = mock_classifier

            mock_registry = MagicMock()
            mock_registry.select_for_task.return_value = None
            MockRegistry.return_value = mock_registry

            model_name, agent_name, agent_sys_prompt, session, store = _bootstrap_session(
                cwd=str(tmp_path),
                task="write unit tests",
                model_override=None,
                session_id=None,
                models_config=models_config,
            )

        assert model_name == "test-model"
        assert session.session_id == "session_new_123"
        assert agent_name is None

    def test_model_override_takes_priority(self, tmp_path):
        models_config = _fake_models_config("test-model")
        with (
            patch("prax.main.FileSessionStore") as MockStore,
            patch("prax.main.Classifier") as MockClassifier,
            patch("prax.main.get_agent_registry") as MockRegistry,
        ):
            mock_store = MagicMock()
            mock_store.load.return_value = None
            mock_store.create_session_id.return_value = "s1"
            MockStore.return_value = mock_store

            mock_classifier = MagicMock()
            mock_classifier.classify.return_value = "standard"
            mock_classifier.select_model.return_value = "should-not-be-used"
            MockClassifier.return_value = mock_classifier

            mock_registry = MagicMock()
            mock_registry.select_for_task.return_value = None
            MockRegistry.return_value = mock_registry

            model_name, _, _, session, _ = _bootstrap_session(
                cwd=str(tmp_path),
                task="any task",
                model_override="my-custom-model",
                session_id=None,
                models_config=models_config,
            )

        assert model_name == "my-custom-model"

    def test_resumes_existing_session(self, tmp_path):
        from prax.core.session_store import SessionData

        existing = SessionData(
            session_id="sess_existing",
            cwd=str(tmp_path),
            model="old-model",
            messages=[{"role": "user", "content": "hello"}],
            metadata={"preferred_model": "resumed-model"},
        )
        models_config = _fake_models_config("test-model")
        with (
            patch("prax.main.FileSessionStore") as MockStore,
            patch("prax.main.Classifier") as MockClassifier,
            patch("prax.main.get_agent_registry") as MockRegistry,
        ):
            mock_store = MagicMock()
            mock_store.load.return_value = existing
            mock_store.create_session_id.return_value = "sess_existing"
            MockStore.return_value = mock_store

            mock_classifier = MagicMock()
            mock_classifier.classify.return_value = "standard"
            mock_classifier.select_model.return_value = "fallback-model"
            MockClassifier.return_value = mock_classifier

            mock_registry = MagicMock()
            mock_registry.select_for_task.return_value = None
            MockRegistry.return_value = mock_registry

            model_name, _, _, session, _ = _bootstrap_session(
                cwd=str(tmp_path),
                task="continue task",
                model_override=None,
                session_id="sess_existing",
                models_config=models_config,
            )

        # preferred_model from existing session metadata is used
        assert model_name == "resumed-model"

    def test_agent_selected_for_task(self, tmp_path):
        models_config = _fake_models_config("test-model")
        with (
            patch("prax.main.FileSessionStore") as MockStore,
            patch("prax.main.Classifier") as MockClassifier,
            patch("prax.main.get_agent_registry") as MockRegistry,
            patch("prax.main.get_model_entry", return_value=None),
        ):
            mock_store = MagicMock()
            mock_store.load.return_value = None
            mock_store.create_session_id.return_value = "s1"
            MockStore.return_value = mock_store

            mock_classifier = MagicMock()
            mock_classifier.classify.return_value = "standard"
            mock_classifier.select_model.return_value = "test-model"
            MockClassifier.return_value = mock_classifier

            fake_agent = MagicMock()
            fake_agent.name = "code-reviewer"
            fake_agent.system_prompt = "You are a code reviewer."
            fake_agent.model = "agent-model"

            mock_registry = MagicMock()
            mock_registry.select_for_task.return_value = fake_agent
            MockRegistry.return_value = mock_registry

            _, agent_name, agent_sys_prompt, _, _ = _bootstrap_session(
                cwd=str(tmp_path),
                task="review my code",
                model_override=None,
                session_id=None,
                models_config=models_config,
            )

        assert agent_name == "code-reviewer"
        assert "code reviewer" in agent_sys_prompt.lower()


# ---------------------------------------------------------------------------
# TestMergeUsage
# ---------------------------------------------------------------------------


class TestMergeUsage:
    def test_merge_two_dicts(self):
        result = _merge_usage({"input_tokens": 10}, {"input_tokens": 5, "output_tokens": 3})
        assert result["input_tokens"] == 15
        assert result["output_tokens"] == 3

    def test_merge_none_existing(self):
        result = _merge_usage(None, {"tokens": 7})
        assert result == {"tokens": 7}

    def test_merge_none_latest(self):
        result = _merge_usage({"tokens": 3}, None)
        assert result == {"tokens": 3}

    def test_merge_both_none(self):
        result = _merge_usage(None, None)
        assert result == {}

    def test_non_int_values_skipped(self):
        result = _merge_usage({"tokens": 5}, {"tokens": 3, "model": "gpt-4"})
        assert result["tokens"] == 8
        assert "model" not in result


# ---------------------------------------------------------------------------
# TestHandleSlashCommand
# ---------------------------------------------------------------------------


class TestHandleSlashCommand:
    def test_non_slash_returns_false(self):
        assert _handle_slash_command("run tests") is False

    def test_ralph_loop_command_returns_true(self, capsys):
        result = _handle_slash_command("/ralph-loop")
        assert result is True
        out = capsys.readouterr().out
        assert "ralph-loop" in out

    def test_ralph_command_returns_true(self, capsys):
        result = _handle_slash_command("/ralph")
        assert result is True

    def test_unknown_slash_command_returns_true(self, capsys):
        with patch("prax.main.command_map", return_value={}):
            result = _handle_slash_command("/unknown-command")
        assert result is True
        out = capsys.readouterr().out
        assert "unknown-command" in out


# ---------------------------------------------------------------------------
# TestRunWithModelUpgrades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_model_upgrades_succeeds_on_first_attempt():
    """When the first model succeeds, returns its result without upgrades."""
    from prax.core.agent_loop import AgentRunReport

    mock_context = MagicMock()
    mock_client = MagicMock()
    fake_report = AgentRunReport(
        stop_reason="end_turn",
        iterations=1,
        had_tool_errors=False,
        only_permission_errors=False,
        usage={"input_tokens": 100},
    )

    async def _mock_loop(task, *, on_complete, **kwargs):
        on_complete(fake_report)
        return "result text"

    with (
        patch("prax.main.get_upgrade_path", return_value=["test-model"]),
        patch("prax.main.should_upgrade_model") as mock_should_upgrade,
    ):
        mock_should_upgrade.return_value = MagicMock(should_retry=False, reason="")
        mock_client.resolve_model.return_value = MagicMock(model="test-model")

        final_model, text, history, events, report = await _run_with_model_upgrades(
            "do task",
            context=mock_context,
            llm_client=mock_client,
            models_config={},
            initial_model="test-model",
            tools=[],
            middlewares=[],
            base_history=[],
            run_loop=_mock_loop,
        )

    assert final_model == "test-model"
    assert text == "result text"
    assert events == []
    assert report.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_run_with_model_upgrades_no_models_raises():
    """When no upgrade path is configured, raises RuntimeError."""
    mock_context = MagicMock()
    mock_client = MagicMock()

    with patch("prax.main.get_upgrade_path", return_value=[]):
        with pytest.raises(RuntimeError, match="No configured models"):
            await _run_with_model_upgrades(
                "task",
                context=mock_context,
                llm_client=mock_client,
                models_config={},
                initial_model="bad-model",
                tools=[],
                middlewares=[],
                base_history=[],
            )


@pytest.mark.asyncio
async def test_run_with_model_upgrades_upgrades_on_exception():
    """When first model raises an upgradeable exception, tries next model."""
    from prax.core.agent_loop import AgentRunReport

    mock_context = MagicMock()
    mock_context.model = "model-a"
    mock_client = MagicMock()
    fake_report = AgentRunReport(
        stop_reason="end_turn",
        iterations=1,
        had_tool_errors=False,
        only_permission_errors=False,
        usage={},
    )

    call_count = 0

    async def _mock_loop(task, *, on_complete, model_config, **kwargs):
        nonlocal call_count
        call_count += 1
        if model_config.model == "model-a":
            raise RuntimeError("context_length_exceeded")
        on_complete(fake_report)
        return "upgraded result"

    with (
        patch("prax.main.get_upgrade_path", return_value=["model-a", "model-b"]),
        patch("prax.main.get_exception_upgrade_reason", return_value="context_length"),
        patch("prax.main.should_upgrade_model") as mock_should_upgrade,
    ):
        mock_should_upgrade.return_value = MagicMock(should_retry=False)
        model_a_config = MagicMock(model="model-a")
        model_b_config = MagicMock(model="model-b")
        mock_client.resolve_model.side_effect = [model_a_config, model_b_config]

        final_model, text, _, events, _ = await _run_with_model_upgrades(
            "task",
            context=mock_context,
            llm_client=mock_client,
            models_config={},
            initial_model="model-a",
            tools=[],
            middlewares=[],
            base_history=[],
            run_loop=_mock_loop,
        )

    assert final_model == "model-b"
    assert text == "upgraded result"
    assert len(events) == 1
    assert events[0]["from"] == "model-a"
    assert events[0]["to"] == "model-b"


# ---------------------------------------------------------------------------
# TestMain
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_no_args_exits_1(self, monkeypatch):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_runs_task(self, monkeypatch, tmp_path):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax", "write", "hello world"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main.load_models_config", return_value=_fake_models_config()),
            patch("prax.main.claude_cli_available", return_value=False),
            patch("prax.main._run_task_sync") as mock_run,
        ):
            main()

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        task = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("task", "")
        assert "hello world" in task or "write hello world" in task

    def test_main_with_prompt_subcommand(self, monkeypatch, tmp_path):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax", "prompt", "my task here"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main.load_models_config", return_value=_fake_models_config()),
            patch("prax.main.claude_cli_available", return_value=False),
            patch("prax.main._run_task_sync") as mock_run,
        ):
            main()

        mock_run.assert_called_once()

    def test_main_with_model_override(self, monkeypatch, tmp_path):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax", "--model", "my-model", "do task"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main.load_models_config", return_value=_fake_models_config()),
            patch("prax.main.claude_cli_available", return_value=False),
            patch("prax.main._run_task_sync") as mock_run,
        ):
            main()

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1].get("model_override") == "my-model" or (
            call_kwargs[0] and call_kwargs[0][1] == "my-model"
        )

    def test_main_empty_task_exits_1(self, monkeypatch, tmp_path):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax", "--model", "m"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main.load_models_config", return_value=_fake_models_config()),
            patch("prax.main.parse_command_tokens", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_main_repl_subcommand_calls_run_repl(self, monkeypatch, tmp_path):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax", "repl"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main.load_models_config", return_value=_fake_models_config()),
            patch("prax.main.run_repl") as mock_repl,
            patch("prax.main.FileSessionStore") as MockStore,
        ):
            mock_store = MagicMock()
            mock_store.create_session_id.return_value = "sess_repl"
            MockStore.return_value = mock_store

            main()

        mock_repl.assert_called_once()

    def test_main_tui_calls_launch_tui(self, monkeypatch, tmp_path):
        from prax.main import main

        monkeypatch.setattr(sys, "argv", ["prax", "--tui"])
        monkeypatch.chdir(tmp_path)

        with (
            patch("prax.main.load_models_config", return_value=_fake_models_config()),
            patch("prax.main.launch_tui", create=True) as mock_tui,
            patch("prax.tui.launch_tui", create=True) as mock_tui2,
        ):
            # Patch the import inside main()
            import prax.tui as tui_mod
            original = getattr(tui_mod, "launch_tui", None)
            tui_mod.launch_tui = MagicMock()
            try:
                main()
                tui_mod.launch_tui.assert_called_once()
            finally:
                if original is not None:
                    tui_mod.launch_tui = original
                elif hasattr(tui_mod, "launch_tui"):
                    del tui_mod.launch_tui
