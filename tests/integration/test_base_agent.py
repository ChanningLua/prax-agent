"""Integration tests for BaseAgent helpers — _resolve_model, _build_context, _run_with_retry."""
from __future__ import annotations

import asyncio
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from prax.agents.base import AgentResult, BaseAgent
from prax.core.context import Context


# ── Minimal concrete agent for testing ───────────────────────────────────────

class _DummyAgent(BaseAgent):
    name = "dummy"

    async def run(self, task: str, **kwargs: Any) -> AgentResult:
        return AgentResult(text="ok", stop_reason="end_turn", iterations=1)


def _agent(cwd: str, model: str = "test-model", memory_backend=None, openviking=None) -> _DummyAgent:
    return _DummyAgent(
        cwd=cwd,
        model=model,
        memory_backend=memory_backend,
        openviking=openviking,
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── _resolve_model ────────────────────────────────────────────────────────────

class TestBaseAgentResolveModel:
    def _mock_client(self, raise_value_error: bool = False):
        client = MagicMock()
        if raise_value_error:
            client.resolve_model.side_effect = ValueError("model not found")
        else:
            client.resolve_model.return_value = MagicMock(model="test-model")
        return client

    def test_resolve_model_returns_model_config_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)
            client = self._mock_client(raise_value_error=False)
            result = agent._resolve_model(client, {})
            assert not isinstance(result, AgentResult)
            client.resolve_model.assert_called_once()

    def test_resolve_model_returns_agent_result_on_value_error(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d, model="bad-model")
            client = self._mock_client(raise_value_error=True)
            result = agent._resolve_model(client, {})
            assert isinstance(result, AgentResult)
            assert result.had_errors is True
            assert result.stop_reason == "config_error"
            assert "bad-model" in result.text

    def test_resolve_model_uses_agent_name_in_message(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d, model="x")
            client = self._mock_client(raise_value_error=True)
            result = agent._resolve_model(client, {}, agent_name="MyAgent")
            assert "MyAgent" in result.text

    def test_resolve_model_passes_models_config(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)
            client = self._mock_client()
            config = {"key": "val"}
            agent._resolve_model(client, config)
            client.resolve_model.assert_called_once_with(agent.model, config)


# ── _build_context ────────────────────────────────────────────────────────────

class TestBaseAgentBuildContext:
    def test_returns_context_instance(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = _agent(d)._build_context()
            assert isinstance(ctx, Context)

    def test_context_has_correct_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = _agent(d)._build_context()
            assert str(ctx.cwd) == d

    def test_prefers_explicit_memory_backend_arg(self):
        with tempfile.TemporaryDirectory() as d:
            explicit_backend = MagicMock()
            self_backend = MagicMock()
            agent = _agent(d, memory_backend=self_backend)
            ctx = agent._build_context(memory_backend=explicit_backend)
            assert ctx._memory_backend is explicit_backend

    def test_falls_back_to_self_memory_backend(self):
        with tempfile.TemporaryDirectory() as d:
            backend = MagicMock()
            agent = _agent(d, memory_backend=backend)
            ctx = agent._build_context()
            assert ctx._memory_backend is backend

    def test_falls_back_to_legacy_openviking_when_no_memory_backend(self):
        with tempfile.TemporaryDirectory() as d:
            ov = MagicMock()
            agent = _agent(d, openviking=ov)
            ctx = agent._build_context()
            # No memory_backend set → openviking path; _memory_backend stays None
            assert ctx._memory_backend is None

    def test_no_backend_builds_plain_context(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = _agent(d)._build_context()
            assert ctx._memory_backend is None


# ── _run_with_retry ───────────────────────────────────────────────────────────

class TestBaseAgentRunWithRetry:
    def test_returns_result_on_first_success(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)

            async def coro():
                return "success"

            result = run(agent._run_with_retry(coro))
            assert result == "success"

    def test_retries_on_exception(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)
            call_count = 0

            async def coro():
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise RuntimeError("transient error")
                return "recovered"

            result = run(agent._run_with_retry(coro, max_retries=3))
            assert result == "recovered"
            assert call_count == 3

    def test_raises_last_exception_when_all_retries_fail(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)
            call_count = 0

            async def coro():
                nonlocal call_count
                call_count += 1
                raise ValueError(f"fail #{call_count}")

            with pytest.raises(ValueError, match="fail #3"):
                run(agent._run_with_retry(coro, max_retries=3))
            assert call_count == 3

    def test_max_retries_zero_raises_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)

            async def coro():
                raise RuntimeError("instant fail")

            # max_retries=0 → single attempt, no retry → raises immediately
            with pytest.raises(RuntimeError, match="instant fail"):
                run(agent._run_with_retry(coro, max_retries=0))

    def test_factory_called_multiple_times(self):
        """coro_factory must be called fresh each retry — not the same coroutine."""
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)
            factory_calls = []

            async def factory():
                factory_calls.append(1)
                if len(factory_calls) < 2:
                    raise RuntimeError("first fail")
                return "ok"

            result = run(agent._run_with_retry(factory, max_retries=3))
            assert result == "ok"
            assert len(factory_calls) == 2


# ── _emit ─────────────────────────────────────────────────────────────────────

class TestBaseAgentEmit:
    def test_emit_calls_on_text(self):
        with tempfile.TemporaryDirectory() as d:
            received = []
            agent = _DummyAgent(cwd=d, on_text=received.append)
            agent._emit("hello")
            assert received == ["hello"]

    def test_emit_noop_when_no_on_text(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(d)
            agent._emit("should not crash")  # no on_text set


# ── Inheritance ───────────────────────────────────────────────────────────────

class TestBaseAgentInheritance:
    def test_run_is_abstract(self):
        """Cannot instantiate BaseAgent directly."""
        with pytest.raises(TypeError):
            BaseAgent(cwd="/tmp")   # type: ignore

    def test_concrete_subclass_instantiates(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _DummyAgent(cwd=d)
            assert agent.cwd == d

    def test_run_returns_agent_result(self):
        with tempfile.TemporaryDirectory() as d:
            agent = _DummyAgent(cwd=d)
            result = run(agent.run("test task"))
            assert isinstance(result, AgentResult)
