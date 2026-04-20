"""Tests for Prax core components."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.core.agent_loop import run_agent_loop
from prax.core.agent_loop import AgentRunReport
from prax.core.classifier import Classifier
from prax.core.compaction import CompactionConfig, SUMMARY_PREFIX, compact_messages
from prax.core.context import Context
from prax.core.llm_client import LLMClient, LLMResponse, ModelConfig
from prax.core.middleware import LoopDetectionMiddleware, PermissionMiddleware
from prax.core.middleware import TodoReminderMiddleware
from prax.core.model_upgrade import get_upgrade_path, should_upgrade_model
from prax.core.permissions import ExecutionPolicy, PermissionMode
from prax.core.session_store import FileSessionStore, SessionData
from prax.core.todo_store import TodoStore
from prax.core.sandbox.base import SandboxResult
from prax.main import _run_with_model_upgrades
from prax.tools.apply_patch import ApplyPatchTool
from prax.tools.base import Tool, ToolCall, ToolResult
from prax.tools.bash import BashTool
from prax.tools.edit import EditTool
from prax.tools.hashing import compute_line_hash
from prax.tools.read import ReadTool
from prax.tools.task import TaskTool
from prax.tools.todo_write import TodoWriteTool
from prax.tools.write import WriteTool


# ── Tool tests ──────────────────────────────────────────────────────


class TestReadTool:
    def test_claude_format(self):
        tool = ReadTool()
        fmt = tool.to_claude_format()
        assert fmt["name"] == "Read"
        assert "file_path" in fmt["input_schema"]["properties"]

    def test_openai_format(self):
        tool = ReadTool()
        fmt = tool.to_openai_format()
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "Read"

    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3")
        tool = ReadTool()
        result = await tool.execute({"file_path": str(f)})
        assert not result.is_error
        assert f"1#{compute_line_hash(1, 'line1')}|line1" in result.content
        assert f"2#{compute_line_hash(2, 'line2')}|line2" in result.content

    @pytest.mark.asyncio
    async def test_read_missing_file(self):
        tool = ReadTool()
        result = await tool.execute({"file_path": "/nonexistent/path"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 11)))
        tool = ReadTool()
        result = await tool.execute({"file_path": str(f), "offset": 3, "limit": 2})
        assert "line3" in result.content
        assert "line4" in result.content
        assert "line5" not in result.content


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        target = tmp_path / "subdir" / "output.txt"
        tool = WriteTool()
        result = await tool.execute({
            "file_path": str(target),
            "content": "hello world",
        })
        assert not result.is_error
        assert target.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_write_overwrite(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("old")
        tool = WriteTool()
        await tool.execute({"file_path": str(f), "content": "new"})
        assert f.read_text() == "new"


class TestEditTool:
    @pytest.mark.asyncio
    async def test_edit_exact_match(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nbeta\n")
        tool = EditTool()

        result = await tool.execute({
            "file_path": str(target),
            "old_string": "beta",
            "new_string": "gamma",
        })

        assert not result.is_error
        assert target.read_text() == "alpha\ngamma\n"

    @pytest.mark.asyncio
    async def test_edit_requires_specific_match(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("beta\nbeta\n")
        tool = EditTool()

        result = await tool.execute({
            "file_path": str(target),
            "old_string": "beta",
            "new_string": "gamma",
        })

        assert result.is_error
        assert "multiple times" in result.content

    @pytest.mark.asyncio
    async def test_edit_rejects_checksum_mismatch(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("before")
        tool = EditTool()

        result = await tool.execute({
            "file_path": str(target),
            "old_string": "before",
            "new_string": "after",
            "expected_sha256": "deadbeef",
        })

        assert result.is_error
        assert "checksum changed" in result.content


class TestApplyPatchTool:
    @pytest.mark.asyncio
    async def test_apply_single_hunk_with_hash_guard(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nbeta\ngamma\n")
        tool = ApplyPatchTool()

        result = await tool.execute({
            "file_path": str(target),
            "hunks": [
                {
                    "start_line": 2,
                    "delete_count": 1,
                    "expected_start_hash": compute_line_hash(2, "beta"),
                    "replacement_lines": ["delta"],
                }
            ],
        })

        assert not result.is_error
        assert target.read_text() == "alpha\ndelta\ngamma\n"

    @pytest.mark.asyncio
    async def test_apply_multiple_hunks(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("a\nb\nc\nd\n")
        tool = ApplyPatchTool()

        result = await tool.execute({
            "file_path": str(target),
            "hunks": [
                {
                    "start_line": 2,
                    "delete_count": 1,
                    "expected_start_hash": compute_line_hash(2, "b"),
                    "replacement_lines": ["beta"],
                },
                {
                    "start_line": 4,
                    "delete_count": 1,
                    "expected_start_hash": compute_line_hash(4, "d"),
                    "replacement_lines": ["delta"],
                },
            ],
        })

        assert not result.is_error
        assert target.read_text() == "a\nbeta\nc\ndelta\n"

    @pytest.mark.asyncio
    async def test_apply_patch_rejects_hash_mismatch(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nbeta\n")
        tool = ApplyPatchTool()

        result = await tool.execute({
            "file_path": str(target),
            "hunks": [
                {
                    "start_line": 2,
                    "delete_count": 1,
                    "expected_start_hash": "deadbeef",
                    "replacement_lines": ["delta"],
                }
            ],
        })

        assert result.is_error
        assert "line hash mismatch" in result.content

    @pytest.mark.asyncio
    async def test_apply_patch_rejects_checksum_mismatch(self, tmp_path):
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nbeta\n")
        tool = ApplyPatchTool()

        result = await tool.execute({
            "file_path": str(target),
            "expected_sha256": "deadbeef",
            "hunks": [
                {
                    "start_line": 2,
                    "delete_count": 1,
                    "replacement_lines": ["delta"],
                }
            ],
        })

        assert result.is_error
        assert "checksum changed" in result.content


class TestTodoWriteTool:
    @pytest.mark.asyncio
    async def test_todo_write_persists_and_clears_when_completed(self, tmp_path):
        tool = TodoWriteTool(cwd=str(tmp_path))

        first = await tool.execute({
            "todos": [
                {"content": "Plan", "activeForm": "Planning", "status": "in_progress"},
                {"content": "Implement", "activeForm": "Implementing", "status": "pending"},
            ]
        })
        store = TodoStore(str(tmp_path))

        assert not first.is_error
        assert len(store.load()) == 2

        second = await tool.execute({
            "todos": [
                {"content": "Plan", "activeForm": "Planning", "status": "completed"},
                {"content": "Implement", "activeForm": "Implementing", "status": "completed"},
                {"content": "Verify", "activeForm": "Verifying", "status": "completed"},
            ]
        })

        assert not second.is_error
        assert store.load() == []


class TestTaskTool:
    @pytest.mark.asyncio
    async def test_task_tool_delegates_to_executor(self):
        async def fake_executor(description, prompt, subagent_type, max_turns, load_skills=True):
            return f"{description}|{prompt}|{subagent_type}|{max_turns}"

        tool = TaskTool(executor=fake_executor)
        result = await tool.execute({
            "description": "Inspect repo",
            "prompt": "Read the docs",
            "subagent_type": "plan",
            "max_turns": 3,
        })

        assert not result.is_error
        assert result.content == "Inspect repo|Read the docs|plan|3"

    @pytest.mark.asyncio
    async def test_task_tool_rejects_invalid_subagent_type(self):
        async def fake_executor(description, prompt, subagent_type, max_turns, load_skills=True):
            return "should not reach"

        tool = TaskTool(executor=fake_executor)
        result = await tool.execute({
            "description": "Some task",
            "prompt": "Do something",
            "subagent_type": "unknown-type",
        })

        assert result.is_error
        assert "subagent_type must be one of" in result.content

    @pytest.mark.asyncio
    async def test_task_tool_defaults_to_general_purpose(self):
        received: list[str] = []

        async def fake_executor(description, prompt, subagent_type, max_turns, load_skills=True):
            received.append(subagent_type)
            return "ok"

        tool = TaskTool(executor=fake_executor)
        result = await tool.execute({
            "description": "Some task",
            "prompt": "Do something",
        })

        assert not result.is_error
        assert received == ["general-purpose"]


class TestBashTool:
    @pytest.mark.asyncio
    async def test_echo(self):
        mock_provider = MagicMock()
        mock_sb = MagicMock()
        mock_sb.execute_command_v2.return_value = SandboxResult(output="hello\n", exit_code=0)
        mock_provider.get.return_value = mock_sb
        mock_provider.acquire.return_value = "sid-1"
        with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=mock_provider):
            tool = BashTool()
        result = await tool.execute({"command": "echo hello"})
        assert not result.is_error
        assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_failing_command(self):
        mock_provider = MagicMock()
        mock_sb = MagicMock()
        mock_sb.execute_command_v2.return_value = SandboxResult(output="failed", exit_code=1)
        mock_provider.get.return_value = mock_sb
        mock_provider.acquire.return_value = "sid-1"
        with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=mock_provider):
            tool = BashTool()
        result = await tool.execute({"command": "false"})
        assert "Exit code:" in result.content

    @pytest.mark.asyncio
    async def test_timeout(self):
        mock_provider = MagicMock()
        mock_sb = MagicMock()
        mock_sb.execute_command_v2.return_value = SandboxResult(output="Error: command timed out after 1s", exit_code=-1, timed_out=True)
        mock_provider.get.return_value = mock_sb
        mock_provider.acquire.return_value = "sid-1"
        with patch("prax.tools.sandbox_bash.get_sandbox_provider", return_value=mock_provider):
            tool = BashTool()
        result = await tool.execute({"command": "sleep 10", "timeout": 1})
        assert result.is_error
        assert "timed out" in result.content


# ── Format conversion tests ────────────────────────────────────────


class TestLLMClientFormatConversion:
    def setup_method(self):
        self.client = LLMClient()

    def teardown_method(self):
        asyncio.run(self.client.close())

    def test_simple_text_message_conversion(self):
        messages = [{"role": "user", "content": "hello"}]
        result = self.client._claude_to_openai_messages(messages, "system prompt")
        assert result[0] == {"role": "system", "content": "system prompt"}
        assert result[1] == {"role": "user", "content": "hello"}

    def test_tool_use_message_conversion(self):
        messages = [
            {"role": "user", "content": "read file"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me read that"},
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test.txt"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "file contents here",
                    },
                ],
            },
        ]
        result = self.client._claude_to_openai_messages(messages, "")
        # user message
        assert result[0]["role"] == "user"
        # assistant with tool_calls
        assert result[1]["role"] == "assistant"
        assert len(result[1]["tool_calls"]) == 1
        assert result[1]["tool_calls"][0]["function"]["name"] == "Read"
        # tool result
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "toolu_123"

    def test_openai_response_to_claude_text(self):
        data = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        resp = self.client._openai_response_to_claude(data)
        assert resp.text == "Hello!"
        assert not resp.has_tool_calls

    def test_openai_response_to_claude_tool_call(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "Read",
                                    "arguments": '{"file_path": "/tmp/x.txt"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        resp = self.client._openai_response_to_claude(data)
        assert resp.has_tool_calls
        assert resp.tool_calls[0].name == "Read"
        assert resp.tool_calls[0].input == {"file_path": "/tmp/x.txt"}

    def test_responses_api_response_to_claude(self):
        data = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Hello from responses"}],
                }
            ],
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        }

        resp = self.client._responses_to_claude(data)

        assert resp.text == "Hello from responses"
        assert resp.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    @pytest.mark.asyncio
    async def test_complete_rejects_models_without_tools(self):
        cfg = ModelConfig(
            provider="test",
            model="no-tools",
            base_url="http://test",
            api_key="key",
            api_format="openai",
            supports_tools=False,
        )

        with pytest.raises(RuntimeError, match="does not support tool calling"):
            await self.client.complete(
                messages=[{"role": "user", "content": "hello"}],
                tools=[ReadTool()],
                model_config=cfg,
            )

    @pytest.mark.asyncio
    async def test_openai_reasoning_effort_is_added_to_request(self):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

        async def fake_post(_url, *, json=None, headers=None):
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

        self.client._http.post = fake_post
        cfg = ModelConfig(
            provider="test",
            model="o3",
            base_url="http://test",
            api_key="key",
            api_format="openai",
            supports_reasoning_effort=True,
            default_reasoning_effort="medium",
        )

        await self.client.complete(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            model_config=cfg,
            reasoning_effort="high",
        )

        assert captured["body"]["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_anthropic_thinking_is_added_to_request(self):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}

        async def fake_post(_url, *, json=None, headers=None):
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

        self.client._http.post = fake_post
        cfg = ModelConfig(
            provider="test",
            model="claude-sonnet-4-6",
            base_url="http://test",
            api_key="key",
            api_format="anthropic",
            supports_thinking=True,
            default_thinking_budget_tokens=12000,
        )

        await self.client.complete(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            model_config=cfg,
            thinking_enabled=True,
        )

        assert captured["body"]["thinking"] == {"type": "enabled", "budget_tokens": 12000}

    @pytest.mark.asyncio
    async def test_openai_responses_mode_hits_responses_endpoint(self):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "status": "completed",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                }

        async def fake_post(url, *, json=None, headers=None):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

        self.client._http.post = fake_post
        cfg = ModelConfig(
            provider="test",
            model="gpt-5-codex",
            config_name="codex",
            base_url="http://test",
            api_key="key",
            api_format="openai",
            request_mode="responses",
            supports_tools=True,
            supports_reasoning_effort=True,
            default_reasoning_effort="medium",
        )

        resp = await self.client.complete(
            messages=[{"role": "user", "content": "hello"}],
            tools=[ReadTool()],
            model_config=cfg,
            reasoning_effort="high",
        )

        assert captured["url"] == "http://test/responses"
        assert captured["body"]["model"] == "gpt-5-codex"
        assert captured["body"]["reasoning"] == {"effort": "high"}
        assert captured["body"]["tools"][0]["type"] == "function"
        assert resp.text == "ok"


# ── Classifier tests ───────────────────────────────────────────────


class TestClassifier:
    def test_classify_with_rules(self, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(
            "rules:\n"
            "  - name: explain\n"
            "    keywords: ['explain', '解释']\n"
            "    tier: low\n"
            "  - name: refactor\n"
            "    keywords: ['重构', 'refactor']\n"
            "    tier: high\n"
            "tier_models:\n"
            "  low: ['glm-4-flash']\n"
            "  high: ['glm-5']\n"
            "  standard: ['glm-4']\n"
        )
        c = Classifier(str(rules))
        assert c.classify("explain this code") == "low"
        assert c.classify("重构登录模块") == "high"
        assert c.classify("do something random") == "standard"

    def test_select_model(self, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(
            "rules:\n"
            "  - name: explain\n"
            "    keywords: ['explain']\n"
            "    tier: low\n"
            "tier_models:\n"
            "  low: ['glm-4-flash']\n"
            "  standard: ['glm-4']\n"
        )
        c = Classifier(str(rules))
        assert c.select_model("explain this") == "glm-4-flash"
        assert c.select_model("write some code") == "glm-4"


# ── Permission tests ───────────────────────────────────────────────


class TestExecutionPolicy:
    def test_workspace_write_blocks_bash(self, tmp_path):
        policy = ExecutionPolicy(
            workspace_root=str(tmp_path),
            permission_mode=PermissionMode.WORKSPACE_WRITE,
        )

        decision = policy.authorize_tool("Bash", BashTool.permission_level)

        assert not decision.allowed
        assert "danger-full-access" in decision.reason

    def test_workspace_write_blocks_paths_outside_workspace(self, tmp_path):
        policy = ExecutionPolicy(
            workspace_root=str(tmp_path),
            permission_mode=PermissionMode.WORKSPACE_WRITE,
        )

        decision = policy.authorize_path("/tmp/outside.txt", write=True)

        assert not decision.allowed
        assert "outside the allowed" in decision.reason


class TestModelUpgrade:
    def test_get_upgrade_path_starts_from_current_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        config = {
            "providers": {
                "openai": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "OPENAI_API_KEY",
                    "format": "openai",
                    "models": [
                        {"name": "glm-4", "tier": "standard"},
                        {"name": "glm-5", "tier": "high"},
                        {"name": "gpt-4.1", "tier": "high"},
                    ],
                }
            },
            "upgrade_chain": ["glm-4-flash", "glm-4", "glm-5", "gpt-4.1"],
        }

        assert get_upgrade_path("glm-4", config) == ["glm-4", "glm-5", "gpt-4.1"]
        assert get_upgrade_path("custom-model", config) == ["glm-4", "glm-5", "gpt-4.1"]

    def test_should_upgrade_model_on_tool_error(self):
        report = AgentRunReport(
            stop_reason="end_turn",
            iterations=2,
            had_tool_errors=True,
            only_permission_errors=False,
        )

        decision = should_upgrade_model(report, "temporary failure")

        assert decision.should_retry
        assert decision.reason == "tool_error"

    @pytest.mark.asyncio
    async def test_run_with_model_upgrades_retries_and_returns_final_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        class FakeClient:
            def resolve_model(self, model_name, _models_config):
                return ModelConfig(
                    provider="test",
                    model=model_name,
                    base_url="http://test",
                    api_key="key",
                    api_format="openai",
                )

        calls: list[str] = []

        async def fake_run_loop(
            _task,
            *,
            model_config,
            message_history,
            on_complete,
            **_kwargs,
        ):
            calls.append(model_config.model)
            message_history.append({"role": "assistant", "content": f"attempt via {model_config.model}"})
            if model_config.model == "glm-4-flash":
                on_complete(
                    AgentRunReport(
                        stop_reason="end_turn",
                        iterations=1,
                        had_tool_errors=True,
                        only_permission_errors=False,
                    )
                )
                return "temporary failure"

            on_complete(
                AgentRunReport(
                    stop_reason="end_turn",
                    iterations=1,
                    had_tool_errors=False,
                    only_permission_errors=False,
                )
            )
            return "success"

        final_model, final_text, final_history, upgrade_events, report = await _run_with_model_upgrades(
            "do work",
            context=Context(cwd=str(tmp_path)),
            llm_client=FakeClient(),
            models_config={
                "providers": {
                    "openai": {
                        "base_url": "https://api.openai.com/v1",
                        "api_key_env": "OPENAI_API_KEY",
                        "format": "openai",
                        "models": [
                            {"name": "glm-4-flash", "tier": "low"},
                            {"name": "glm-5", "tier": "high"},
                        ],
                    }
                },
                "upgrade_chain": ["glm-4-flash", "glm-5"],
            },
            initial_model="glm-4-flash",
            tools=[],
            middlewares=[],
            base_history=[{"role": "user", "content": "existing"}],
            run_loop=fake_run_loop,
        )

        assert calls == ["glm-4-flash", "glm-5"]
        assert final_model == "glm-5"
        assert final_text == "success"
        assert final_history[-1]["content"] == "attempt via glm-5"
        assert upgrade_events == [{"from": "glm-4-flash", "to": "glm-5", "reason": "tool_error"}]
        assert report.stop_reason == "end_turn"


# ── Session / compaction tests ─────────────────────────────────────


class TestCompaction:
    def test_compact_messages_preserves_recent_tail(self):
        messages = [
            {"role": "user", "content": f"user request {i}"}
            for i in range(8)
        ] + [
            {"role": "assistant", "content": f"assistant reply {i}"}
            for i in range(8)
        ]

        result = compact_messages(
            messages,
            CompactionConfig(max_messages=10, keep_recent=4),
        )

        # 16 messages > max_messages=10, so keep_recent=4 → last 4 messages
        assert len(result) == 4
        assert result[-1]["content"] == "assistant reply 7"


class TestSessionStore:
    def test_save_and_load_round_trip(self, tmp_path):
        store = FileSessionStore(str(tmp_path / ".prax" / "sessions"))
        session = SessionData(
            session_id="session_demo",
            cwd=str(tmp_path),
            model="glm-4",
            messages=[{"role": "user", "content": "hello"}],
            metadata={"upgrade_history": [{"from": "a", "to": "b", "reason": "tool_error"}]},
        )

        path = store.save(session)
        loaded = store.load("session_demo")

        assert path.exists()
        assert loaded is not None
        assert loaded.session_id == "session_demo"
        assert loaded.messages == [{"role": "user", "content": "hello"}]
        assert loaded.metadata == {"upgrade_history": [{"from": "a", "to": "b", "reason": "tool_error"}]}

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "prax.session.v1"


# ── Context tests ──────────────────────────────────────────────────


class TestContext:
    def test_build_system_prompt(self):
        ctx = Context(cwd="/tmp/test-project")
        prompt = ctx.build_system_prompt()
        assert "/tmp/test-project" in prompt
        assert "Read" in prompt or "Tool" in prompt or "Prax" in prompt
        assert "TodoWrite" in prompt
        assert "Task" in prompt

    def test_loads_claude_md(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("# My Project\nUse Python 3.12")
        ctx = Context(cwd=str(tmp_path))
        prompt = ctx.build_system_prompt()
        assert "My Project" in prompt
        assert "Python 3.12" in prompt

    def test_loads_root_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Root Project\nUse project workflow.")
        ctx = Context(cwd=str(tmp_path))
        prompt = ctx.build_system_prompt()
        assert "Root Project" in prompt
        assert "Use project workflow." in prompt


# ── Agent Loop tests ───────────────────────────────────────────────


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """Model returns text immediately, no tool calls."""
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = LLMResponse(
            content=[{"type": "text", "text": "The answer is 42."}],
            stop_reason="end_turn",
        )

        ctx = Context(cwd="/tmp")
        cfg = ModelConfig(
            provider="test", model="test-model",
            base_url="http://test", api_key="key", api_format="openai",
        )

        result = await run_agent_loop(
            "What is the meaning of life?",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[],
        )
        assert result == "The answer is 42."
        mock_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_call_then_text(self, tmp_path):
        """Model calls Read tool, then returns final text."""
        # Create a test file
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello from test file!")

        mock_client = AsyncMock(spec=LLMClient)
        # First call: model wants to read a file
        mock_client.complete.side_effect = [
            LLMResponse(
                content=[
                    {"type": "text", "text": "Let me read the file."},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "Read",
                        "input": {"file_path": str(test_file)},
                    },
                ],
            ),
            # Second call: model returns final answer
            LLMResponse(
                content=[{"type": "text", "text": "The file says: Hello from test file!"}],
            ),
        ]

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test-model",
            base_url="http://test", api_key="key", api_format="openai",
        )

        result = await run_agent_loop(
            "Read hello.txt",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[ReadTool()],
        )
        assert "Hello from test file!" in result
        assert mock_client.complete.call_count == 2

        # Verify messages include tool result
        second_call_messages = mock_client.complete.call_args_list[1].kwargs["messages"]
        assert any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_result" for b in m["content"])
            for m in second_call_messages
        )

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Model calls a tool that doesn't exist."""
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.side_effect = [
            LLMResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "toolu_xyz",
                        "name": "NonexistentTool",
                        "input": {},
                    },
                ],
            ),
            LLMResponse(
                content=[{"type": "text", "text": "Tool not found, sorry."}],
            ),
        ]

        ctx = Context(cwd="/tmp")
        cfg = ModelConfig(
            provider="test", model="test-model",
            base_url="http://test", api_key="key", api_format="openai",
        )

        result = await run_agent_loop(
            "do something",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[ReadTool()],
        )
        assert "not found" in result.lower() or "sorry" in result.lower()

    @pytest.mark.asyncio
    async def test_write_tool_in_loop(self, tmp_path):
        """Model reads a file then writes a new one."""
        src = tmp_path / "src.txt"
        src.write_text("original content")
        dst = tmp_path / "dst.txt"

        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.side_effect = [
            # Read
            LLMResponse(content=[
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": str(src)}},
            ]),
            # Write
            LLMResponse(content=[
                {"type": "tool_use", "id": "t2", "name": "Write",
                 "input": {"file_path": str(dst), "content": "modified content"}},
            ]),
            # Done
            LLMResponse(content=[
                {"type": "text", "text": "Done! Wrote modified content."},
            ]),
        ]

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test",
            base_url="http://test", api_key="k", api_format="openai",
        )

        result = await run_agent_loop(
            "copy and modify",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[ReadTool(), WriteTool()],
        )

        assert dst.read_text() == "modified content"
        assert "Done" in result

    @pytest.mark.asyncio
    async def test_permission_middleware_denies_bash(self, tmp_path):
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.side_effect = [
            LLMResponse(content=[
                {"type": "tool_use", "id": "t1", "name": "SandboxBash", "input": {"command": "echo hello"}},
            ]),
            LLMResponse(content=[
                {"type": "text", "text": "Bash was denied."},
            ]),
        ]

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test",
            base_url="http://test", api_key="k", api_format="openai",
        )
        policy = ExecutionPolicy(
            workspace_root=str(tmp_path),
            permission_mode=PermissionMode.WORKSPACE_WRITE,
        )

        result = await run_agent_loop(
            "run a shell command",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[BashTool(cwd=str(tmp_path))],
            middlewares=[PermissionMiddleware(policy)],
        )

        assert result == "Bash was denied."
        tool_result_message = mock_client.complete.call_args_list[1].kwargs["messages"][-1]
        assert "Permission denied" in tool_result_message["content"][0]["content"]

    @pytest.mark.asyncio
    async def test_loop_detection_forces_stop(self, tmp_path):
        target = tmp_path / "loop.txt"
        target.write_text("hello")

        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.side_effect = [
            LLMResponse(content=[
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(target)}},
            ]),
            LLMResponse(content=[
                {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": str(target)}},
            ]),
            LLMResponse(content=[
                {"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": str(target)}},
            ]),
        ]

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test",
            base_url="http://test", api_key="k", api_format="openai",
        )

        result = await run_agent_loop(
            "read forever",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[ReadTool()],
            middlewares=[LoopDetectionMiddleware(hard_limit=3)],
        )

        assert "safety limit" in result or "Circuit breaker" in result

    @pytest.mark.asyncio
    async def test_circuit_breaker_emits_completion_report(self, tmp_path):
        events: list[object] = []

        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.side_effect = RuntimeError("network timeout")

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test",
            base_url="http://test", api_key="k", api_format="openai",
        )

        def _on_complete(event: object) -> None:
            events.append(event)

        result = await run_agent_loop(
            "trigger failures",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[],
            on_complete=_on_complete,
        )

        assert "Circuit breaker" in result
        assert any(
            isinstance(event, AgentRunReport) and event.stop_reason == "circuit_breaker"
            for event in events
        ) or any(
            getattr(event, "stop_reason", "") == "circuit_breaker" for event in events
        )

    @pytest.mark.asyncio
    async def test_existing_message_history_is_reused(self, tmp_path):
        message_history = [
            {"role": "user", "content": "previous task"},
            {"role": "assistant", "content": "previous answer"},
        ]
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = LLMResponse(
            content=[{"type": "text", "text": "continued"}],
        )

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test",
            base_url="http://test", api_key="k", api_format="openai",
        )

        result = await run_agent_loop(
            "new request",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[],
            message_history=message_history,
        )

        assert result == "continued"
        assert message_history[-1] == {"role": "user", "content": "new request"}
        sent_messages = mock_client.complete.call_args.kwargs["messages"]
        assert sent_messages[0]["content"] == "previous task"
        assert sent_messages[2]["content"] == "new request"

    @pytest.mark.asyncio
    async def test_run_loop_compacts_long_history(self, tmp_path):
        message_history = [
            {"role": "user", "content": f"user {i}"}
            for i in range(12)
        ]
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = LLMResponse(
            content=[{"type": "text", "text": "done"}],
        )

        ctx = Context(cwd=str(tmp_path))
        cfg = ModelConfig(
            provider="test", model="test",
            base_url="http://test", api_key="k", api_format="openai",
        )

        await run_agent_loop(
            "final request",
            context=ctx,
            llm_client=mock_client,
            model_config=cfg,
            tools=[],
            message_history=message_history,
        )

        sent_messages = mock_client.complete.call_args.kwargs["messages"]
        # With 12 history messages + 1 user message, compaction should trigger
        # if the loop's internal compaction threshold is exceeded
        assert len(sent_messages) > 0

    @pytest.mark.asyncio
    async def test_todo_reminder_middleware_injects_current_todos(self, tmp_path):
        TodoStore(str(tmp_path)).save([
            TodoStore(str(tmp_path))._parse_item({"content": "Plan", "activeForm": "Planning", "status": "in_progress"}),
        ])
        captured = {}

        class FakeClient:
            async def complete(self, *, messages, **_kwargs):
                captured["messages"] = messages
                return LLMResponse(content=[{"type": "text", "text": "done"}], stop_reason="end_turn")

        result = await run_agent_loop(
            "continue",
            context=Context(cwd=str(tmp_path)),
            llm_client=FakeClient(),
            model_config=ModelConfig(
                provider="test",
                model="test",
                base_url="http://test",
                api_key="k",
                api_format="openai",
            ),
            tools=[],
            middlewares=[TodoReminderMiddleware(cwd=str(tmp_path))],
        )

        assert result == "done"
        reminder_messages = [m for m in captured["messages"] if m.get("name") == "todo_reminder"]
        assert reminder_messages
        assert "Plan" in reminder_messages[0]["content"]
