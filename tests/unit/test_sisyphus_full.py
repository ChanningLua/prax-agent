"""
Comprehensive unit tests for SisyphusAgent covering uncovered paths:
- run() routing to ralph/team/direct strategies
- run() when _resolve_model() returns AgentResult (error path)
- _classify_strategy() JSON parsing, prefix fallback, exception → "direct"
- _run_direct() success and exception paths
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.agents.base import AgentResult
from prax.agents.sisyphus import SisyphusAgent


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_agent(model: str = "gpt-4") -> SisyphusAgent:
    return SisyphusAgent(
        cwd="/tmp",
        model=model,
        models_config={"providers": {}},
    )


# ── run() routing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_routes_to_ralph():
    """When strategy='ralph', run() creates and calls RalphAgent."""
    agent = _make_agent()

    ralph_result = AgentResult(text="ralph done", stop_reason="todos_complete", iterations=2)

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve, \
         patch.object(agent, "_classify_strategy", new_callable=AsyncMock) as mock_cls, \
         patch("prax.agents.sisyphus.RalphAgent") as MockRalph:

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = MagicMock()  # model config object
        mock_cls.return_value = "ralph"

        ralph_instance = MagicMock()
        ralph_instance.run = AsyncMock(return_value=ralph_result)
        MockRalph.return_value = ralph_instance

        result = await agent.run("Implement feature X")

    assert result is ralph_result
    MockRalph.assert_called_once()
    ralph_instance.run.assert_awaited_once_with("Implement feature X")


@pytest.mark.asyncio
async def test_run_routes_to_team():
    """When strategy='team', run() creates and calls TeamAgent."""
    agent = _make_agent()

    team_result = AgentResult(text="team done", stop_reason="end_turn", iterations=1)

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve, \
         patch.object(agent, "_classify_strategy", new_callable=AsyncMock) as mock_cls, \
         patch("prax.agents.sisyphus.TeamAgent") as MockTeam:

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = MagicMock()
        mock_cls.return_value = "team"

        team_instance = MagicMock()
        team_instance.run = AsyncMock(return_value=team_result)
        MockTeam.return_value = team_instance

        result = await agent.run("Analyze 3 APIs in parallel")

    assert result is team_result
    MockTeam.assert_called_once()


@pytest.mark.asyncio
async def test_run_routes_to_direct():
    """When strategy='direct', run() calls _run_direct()."""
    agent = _make_agent()

    direct_result = AgentResult(text="direct done", stop_reason="end_turn", iterations=1)

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve, \
         patch.object(agent, "_classify_strategy", new_callable=AsyncMock) as mock_cls, \
         patch.object(agent, "_run_direct", new_callable=AsyncMock) as mock_direct:

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = MagicMock()
        mock_cls.return_value = "direct"
        mock_direct.return_value = direct_result

        result = await agent.run("Explain how X works")

    assert result is direct_result
    mock_direct.assert_awaited_once_with("Explain how X works")


@pytest.mark.asyncio
async def test_run_returns_early_when_resolve_model_fails():
    """If _resolve_model() returns an AgentResult, run() returns it immediately."""
    agent = _make_agent()

    error_result = AgentResult(
        text="Model not found", stop_reason="error", iterations=0, had_errors=True
    )

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve:

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = error_result

        result = await agent.run("Any task")

    assert result is error_result
    mock_client.close.assert_awaited_once()


# ── _classify_strategy paths ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_strategy_parses_json_ralph():
    """JSON response with strategy=ralph is parsed correctly."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []
        mock_loop.return_value = '{"strategy": "ralph", "reason": "multiple todos"}'

        strategy = await agent._classify_strategy(
            "Implement user auth",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "ralph"


@pytest.mark.asyncio
async def test_classify_strategy_parses_json_team():
    """JSON response with strategy=team is parsed correctly."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []
        mock_loop.return_value = '{"strategy": "team", "reason": "parallel work"}'

        strategy = await agent._classify_strategy(
            "Analyze 3 APIs",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "team"


@pytest.mark.asyncio
async def test_classify_strategy_parses_json_direct():
    """JSON response with strategy=direct is parsed correctly."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []
        mock_loop.return_value = '{"strategy": "direct", "reason": "simple answer"}'

        strategy = await agent._classify_strategy(
            "Explain how X works",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "direct"


@pytest.mark.asyncio
async def test_classify_strategy_prefix_fallback_ralph():
    """Non-JSON response starting with 'ralph' triggers prefix fallback."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []
        mock_loop.return_value = "ralph: this needs continuous execution"

        strategy = await agent._classify_strategy(
            "Implement feature",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "ralph"


@pytest.mark.asyncio
async def test_classify_strategy_prefix_fallback_team():
    """Non-JSON response starting with 'team' triggers prefix fallback."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []
        mock_loop.return_value = "team: can split into parallel tasks"

        strategy = await agent._classify_strategy(
            "Parallel work",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "team"


@pytest.mark.asyncio
async def test_classify_strategy_exception_returns_direct():
    """Exception during LLM classification → returns 'direct' as safe default."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop",
               new_callable=AsyncMock,
               side_effect=RuntimeError("LLM unavailable")), \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []

        strategy = await agent._classify_strategy(
            "Some task",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "direct"


@pytest.mark.asyncio
async def test_classify_strategy_json_with_unknown_strategy_falls_to_prefix():
    """JSON strategy value not in (ralph/team/direct) falls through to prefix check."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.core.todo_store.TodoStore") as MockTodoStore, \
         patch("prax.agents.sisyphus.Context"):

        MockTodoStore.return_value.load.return_value = []
        # JSON with invalid strategy value — falls to prefix matching on raw text
        mock_loop.return_value = '{"strategy": "unknown_mode"}'

        strategy = await agent._classify_strategy(
            "Some task",
            client=MagicMock(),
            model_config=MagicMock(),
        )

    assert strategy == "direct"


# ── _run_direct paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_direct_success():
    """_run_direct() success path wraps text in AgentResult."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve, \
         patch.object(agent, "_build_context") as mock_ctx, \
         patch("prax.agents.sisyphus.run_agent_loop", new_callable=AsyncMock) as mock_loop, \
         patch("prax.agents.sisyphus.TodoWriteTool"), \
         patch("prax.agents.sisyphus.TaskTool"), \
         patch("prax.agents.sisyphus.LoopDetectionMiddleware"), \
         patch("prax.agents.sisyphus.TodoReminderMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = MagicMock()
        mock_ctx.return_value = MagicMock()
        mock_loop.return_value = "direct result text"

        result = await agent._run_direct("Simple question")

    assert isinstance(result, AgentResult)
    assert result.text == "direct result text"
    assert result.stop_reason == "end_turn"
    assert result.had_errors is False
    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_direct_exception_returns_error_result():
    """_run_direct() exception → AgentResult with had_errors=True."""
    agent = _make_agent()

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve, \
         patch.object(agent, "_build_context") as mock_ctx, \
         patch("prax.agents.sisyphus.run_agent_loop",
               new_callable=AsyncMock,
               side_effect=RuntimeError("loop exploded")), \
         patch("prax.agents.sisyphus.TodoWriteTool"), \
         patch("prax.agents.sisyphus.TaskTool"), \
         patch("prax.agents.sisyphus.LoopDetectionMiddleware"), \
         patch("prax.agents.sisyphus.TodoReminderMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = MagicMock()
        mock_ctx.return_value = MagicMock()

        result = await agent._run_direct("Complex task")

    assert isinstance(result, AgentResult)
    assert result.had_errors is True
    assert result.stop_reason == "error"
    assert "loop exploded" in result.text
    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_direct_resolve_model_error():
    """_run_direct() when _resolve_model returns AgentResult → returned directly."""
    agent = _make_agent()

    error_result = AgentResult(text="no model", stop_reason="error", iterations=0, had_errors=True)

    with patch("prax.agents.sisyphus.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model") as mock_resolve:

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_resolve.return_value = error_result

        result = await agent._run_direct("Some task")

    assert result is error_result
    mock_client.close.assert_awaited_once()
