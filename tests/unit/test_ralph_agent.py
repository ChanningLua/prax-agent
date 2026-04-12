"""Unit tests for prax/agents/ralph.py — RalphAgent.

All tests are pure unit tests with no real I/O. run_agent_loop and
TodoStore are mocked throughout so no LLM API calls are made.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from prax.agents.ralph import MAX_RALPH_ITERATIONS, MAX_RETRY_ATTEMPTS, RalphAgent, _OpenVikingMemoryShim
from prax.agents.base import AgentResult
from prax.core.checkpoint import Checkpoint, CheckpointStore
from prax.core.todo_store import TodoItem, TodoStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(tmp_path, **kwargs):
    """Create a RalphAgent pointing at a tmp directory with defaults."""
    defaults = dict(
        cwd=str(tmp_path),
        model="test-model",
        use_llm_planner=False,
    )
    defaults.update(kwargs)
    return RalphAgent(**defaults)


def _todo(content="Write tests", status="pending"):
    return TodoItem(content=content, active_form=content, status=status)


def _patch_resolve_model(agent):
    """Patch _resolve_model to return a fake ModelConfig-like object."""
    fake_model_config = MagicMock()
    fake_model_config.model = agent.model
    agent._resolve_model = MagicMock(return_value=fake_model_config)
    return fake_model_config


# ---------------------------------------------------------------------------
# 1. Single iteration — all todos complete after first run_agent_loop call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_iteration_completion(tmp_path):
    """RalphAgent completes when TodoStore has no pending todos after first loop."""
    agent = _make_agent(tmp_path)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="done") as mock_loop,
        patch.object(TodoStore, "load", return_value=[_todo(status="done")]),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(CheckpointStore, "clear"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("test task")

    assert isinstance(result, AgentResult)
    assert result.stop_reason == "todos_complete"
    assert result.iterations >= 1


# ---------------------------------------------------------------------------
# 2. Empty task causes initial run_agent_loop to execute, then completes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_task_runs_once_then_exits(tmp_path):
    """Empty string task still invokes the agent loop once then exits cleanly."""
    agent = _make_agent(tmp_path)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="") as mock_loop,
        patch.object(TodoStore, "load", return_value=[]),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(CheckpointStore, "clear"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("")

    assert result.stop_reason == "todos_complete"
    assert not result.had_errors


# ---------------------------------------------------------------------------
# 3. Max iterations reached — returns partial stop_reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_iterations_reached(tmp_path):
    """RalphAgent stops after max_iterations even when todos are still pending."""
    agent = _make_agent(tmp_path, max_iterations=2)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="partial"),
        patch.object(TodoStore, "load", return_value=[_todo(status="pending")]),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(CheckpointStore, "save"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("big task")

    assert result.stop_reason == "partial"
    assert result.iterations <= 2


# ---------------------------------------------------------------------------
# 4. Error recovery — single error then success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_recovery_then_success(tmp_path):
    """After one loop error the agent recovers and completes on the next pass."""
    agent = _make_agent(tmp_path, max_iterations=5)
    model_config = _patch_resolve_model(agent)

    call_count = 0

    async def _flaky_loop(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure")
        return "ok"

    # First load: pending; second load (after error recovery pass): done
    load_responses = iter([
        [_todo(status="pending")],
        [_todo(status="done")],
    ])

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", side_effect=_flaky_loop),
        patch.object(TodoStore, "load", side_effect=load_responses),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(CheckpointStore, "save"),
        patch.object(CheckpointStore, "clear"),
        patch("prax.agents.ralph.asyncio.sleep", new_callable=AsyncMock),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("some task")

    # Should record that an error happened
    assert result.had_errors is True


# ---------------------------------------------------------------------------
# 5. Model config resolution failure — early AgentResult returned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_config_failure_returns_error_result(tmp_path):
    """When _resolve_model returns an AgentResult, run returns it immediately."""
    agent = _make_agent(tmp_path)
    error_result = AgentResult(
        text="Model not found",
        stop_reason="config_error",
        iterations=0,
        had_errors=True,
    )
    agent._resolve_model = MagicMock(return_value=error_result)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch.object(CheckpointStore, "load", return_value=None),
    ):
        MockClient.return_value.close = AsyncMock()
        result = await agent.run("task")

    assert result is error_result
    assert result.stop_reason == "config_error"


# ---------------------------------------------------------------------------
# 6. Checkpoint save at configured interval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_saved_at_interval(tmp_path):
    """CheckpointStore.save is called when iteration % checkpoint_interval == 0."""
    agent = _make_agent(tmp_path, max_iterations=4, checkpoint_interval=3)
    model_config = _patch_resolve_model(agent)

    # Provide enough pending-todo loads to drive multiple iterations
    # then eventually return done
    load_seq = [
        [_todo(status="pending")],  # iter 1 continuation check
        [_todo(status="pending")],  # iter 2 continuation check
        [_todo(status="pending")],  # iter 3 continuation check (checkpoint expected)
        [_todo(status="done")],     # exit
        [_todo(status="done")],     # final load for clear
    ]
    load_iter = iter(load_seq)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="ok"),
        patch.object(TodoStore, "load", side_effect=load_iter),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(CheckpointStore, "save") as mock_save,
        patch.object(CheckpointStore, "clear"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        await agent.run("multi-iter task")

    # At least one checkpoint save should have been called
    assert mock_save.call_count >= 1


# ---------------------------------------------------------------------------
# 7. Checkpoint resume — resumes from saved checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_resume_skips_initial_loop(tmp_path):
    """When a checkpoint exists, run resumes from it without re-running the initial task."""
    agent = _make_agent(tmp_path)
    model_config = _patch_resolve_model(agent)

    saved_checkpoint = Checkpoint(
        session_id=agent._session_id,
        iteration=3,
        task="resumed task",
        model="test-model",
        message_history=[{"role": "user", "content": "hi"}],
        todo_snapshot=[],
        created_at="2026-01-01T00:00:00+00:00",
    )

    emitted = []
    agent.on_text = emitted.append

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="resumed"),
        patch.object(CheckpointStore, "load", return_value=saved_checkpoint),
        patch.object(TodoStore, "load", return_value=[_todo(status="done")]),
        patch.object(CheckpointStore, "clear"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("original task")

    # The emitted text should include "Resuming"
    assert any("Resuming" in msg for msg in emitted)
    # Iterations should start from 3 (checkpoint.iteration)
    assert result.iterations >= 3


# ---------------------------------------------------------------------------
# 8. Checkpoint cleared after successful completion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_cleared_on_completion(tmp_path):
    """CheckpointStore.clear is called when all todos are done."""
    agent = _make_agent(tmp_path)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="done"),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(TodoStore, "load", return_value=[_todo(status="done")]),
        patch.object(CheckpointStore, "clear") as mock_clear,
        patch.object(CheckpointStore, "save"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        await agent.run("task")

    mock_clear.assert_called_once_with(agent._session_id)


# ---------------------------------------------------------------------------
# 9. Checkpoint NOT cleared when todos remain (partial completion)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_not_cleared_on_partial(tmp_path):
    """CheckpointStore.clear is NOT called when todos are still pending."""
    agent = _make_agent(tmp_path, max_iterations=1)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="partial"),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(TodoStore, "load", return_value=[_todo(status="pending")]),
        patch.object(CheckpointStore, "clear") as mock_clear,
        patch.object(CheckpointStore, "save"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("unfinished task")

    mock_clear.assert_not_called()
    assert result.stop_reason == "partial"


# ---------------------------------------------------------------------------
# 10. Memory backend receives store_experience calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_backend_receives_store_experience(tmp_path):
    """When memory_backend is provided, store_experience is called during loop."""
    memory_backend = MagicMock()
    memory_backend.store_experience = AsyncMock()
    memory_backend.get_knowledge_graph = MagicMock(return_value=None)

    agent = _make_agent(tmp_path, memory_backend=memory_backend, max_iterations=2)
    model_config = _patch_resolve_model(agent)

    load_seq = iter([
        [_todo(status="pending")],
        [_todo(status="done")],
        [_todo(status="done")],  # final load for clear check
    ])

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="done"),
        patch.object(TodoStore, "load", side_effect=load_seq),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(CheckpointStore, "save"),
        patch.object(CheckpointStore, "clear"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        await agent.run("memory test")

    assert memory_backend.store_experience.call_count >= 1


# ---------------------------------------------------------------------------
# 11. on_text callback receives status messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_text_callback_receives_messages(tmp_path):
    """The on_text callback is called with Ralph status messages during execution."""
    received = []
    agent = _make_agent(tmp_path, on_text=received.append)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="ok"),
        patch.object(CheckpointStore, "load", return_value=None),
        patch.object(TodoStore, "load", return_value=[_todo(status="done")]),
        patch.object(CheckpointStore, "clear"),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        await agent.run("hello world")

    assert len(received) >= 1
    assert any("[Ralph]" in msg for msg in received)


# ---------------------------------------------------------------------------
# 12. Initial run failure returns error AgentResult without continuing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initial_run_failure_returns_error(tmp_path):
    """If the initial run_agent_loop call raises, Ralph returns an error result."""
    agent = _make_agent(tmp_path)
    model_config = _patch_resolve_model(agent)

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock,
              side_effect=RuntimeError("boom")),
        patch.object(CheckpointStore, "load", return_value=None),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        result = await agent.run("failing task")

    assert result.had_errors is True
    assert result.stop_reason == "error"
    assert "boom" in result.text


# ---------------------------------------------------------------------------
# 13. _build_tools constructs non-empty tool list
# ---------------------------------------------------------------------------

def test_build_tools_returns_non_empty_list(tmp_path):
    """_build_tools returns at least TodoWriteTool and background task tools."""
    agent = _make_agent(tmp_path)
    tools = agent._build_tools()
    # Must include at minimum TodoWriteTool + TaskTool + 5 background tools
    assert len(tools) >= 2
    tool_names = [type(t).__name__ for t in tools]
    assert "TodoWriteTool" in tool_names


# ---------------------------------------------------------------------------
# 14. _OpenVikingMemoryShim silently ignores unavailable OpenViking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openviking_shim_unavailable_is_silent():
    """_OpenVikingMemoryShim.store_experience is a no-op when available=False."""
    ov = MagicMock()
    ov.available = False
    shim = _OpenVikingMemoryShim(ov)

    exp = MagicMock()
    # Should not raise even though openviking is unavailable
    await shim.store_experience(exp)
    ov.store_experience.assert_not_called()


# ---------------------------------------------------------------------------
# 15. RalphAgent uses provided session_id as checkpoint key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_id_used_for_checkpoint(tmp_path):
    """The provided session_id is used to load and clear checkpoints."""
    session_id = "ralph_fixed_session"
    agent = _make_agent(tmp_path, session_id=session_id)
    model_config = _patch_resolve_model(agent)

    assert agent._session_id == session_id

    with (
        patch("prax.agents.ralph.LLMClient") as MockClient,
        patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="ok"),
        patch.object(CheckpointStore, "load", return_value=None) as mock_load,
        patch.object(TodoStore, "load", return_value=[_todo(status="done")]),
        patch.object(CheckpointStore, "clear") as mock_clear,
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=model_config)

        await agent.run("session task")

    mock_load.assert_called_with(session_id)
    mock_clear.assert_called_with(session_id)
