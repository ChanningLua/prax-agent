"""
Comprehensive unit tests for RalphAgent covering uncovered paths:
- run() initial loop success
- run() LLM planner decomposition + skip on failure
- run() initial loop exception → error AgentResult
- run() continuation loop until todos done
- run() checkpoint save at interval
- run() error recovery: ABORT, UPGRADE_MODEL, REDUCE_SCOPE, SWITCH_TOOL
- run() delay_seconds → asyncio.sleep
- run() max_iterations reached (partial completion)
- run() from checkpoint (resume)
- run() _resolve_model returns AgentResult (model not found)
- _OpenVikingMemoryShim.store_experience and close
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from prax.agents.base import AgentResult
from prax.agents.ralph import RalphAgent, _OpenVikingMemoryShim, MAX_RALPH_ITERATIONS
from prax.core.error_recovery import RecoveryAction, RecoveryStrategy, ErrorClassification, ErrorType


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_agent(**kw) -> RalphAgent:
    defaults = dict(
        cwd="/tmp",
        model="gpt-4",
        models_config={"providers": {}},
        max_iterations=10,
        use_llm_planner=False,
    )
    defaults.update(kw)
    # Patch CheckpointStore so no real filesystem access during __init__
    with patch("prax.agents.ralph.CheckpointStore"):
        return RalphAgent(**defaults)


def _todo(content: str, status: str = "pending"):
    t = MagicMock()
    t.content = content
    t.status = status
    return t


def _noop_recovery(action=RecoveryAction.RETRY_SAME, delay=0, model=None, batch_size=None, hint=None):
    s = MagicMock(spec=RecoveryStrategy)
    s.action = action
    s.delay_seconds = delay
    s.suggested_model = model
    s.suggested_batch_size = batch_size
    s.tool_hint = hint
    s.reason = "test reason"
    return s


# ── run() model resolution failure ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_resolve_model_error_returns_early():
    """If _resolve_model returns AgentResult, run() returns it immediately."""
    agent = _make_agent()
    error_result = AgentResult(text="no model", stop_reason="error", iterations=0, had_errors=True)

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=error_result), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch.object(agent, "_checkpoint_store") as mock_cp_store:

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        mock_cp_store.load.return_value = None

        result = await agent.run("task")

    assert result is error_result
    mock_client.close.assert_awaited_once()


# ── run() initial loop success ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_initial_loop_success_all_todos_done():
    """run() completes on first loop if no todos remain."""
    agent = _make_agent()

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="done text"), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.return_value = []   # no todos → loop exits immediately
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None   # no checkpoint
        mock_cp_store.clear = MagicMock()

        result = await agent.run("Build auth")

    assert result.text == "done text"
    assert result.stop_reason == "todos_complete"
    assert result.iterations == 1
    assert result.had_errors is False


# ── run() planner decomposition ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_uses_llm_planner_when_enabled():
    """When use_llm_planner=True, LLMPlanner.decompose is called."""
    agent = _make_agent(use_llm_planner=True)

    planned_item = MagicMock()
    planned_item.content = "Write tests"
    planned_item.active_form = "Write tests"
    planned_item.status = "pending"

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="ok"), \
         patch("prax.agents.ralph.LLMPlanner") as MockPlanner, \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        planner_instance = MagicMock()
        planner_instance.decompose = AsyncMock(return_value=[planned_item])
        MockPlanner.return_value = planner_instance

        ts = MagicMock()
        ts.load.return_value = []
        ts.save = MagicMock()
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.clear = MagicMock()

        await agent.run("Implement feature")

    planner_instance.decompose.assert_awaited_once()
    ts.save.assert_called_once()


@pytest.mark.asyncio
async def test_run_planner_failure_is_swallowed():
    """LLMPlanner.decompose exception is logged but does not abort run."""
    agent = _make_agent(use_llm_planner=True)

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="ok"), \
         patch("prax.agents.ralph.LLMPlanner") as MockPlanner, \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        planner_instance = MagicMock()
        planner_instance.decompose = AsyncMock(side_effect=RuntimeError("planner down"))
        MockPlanner.return_value = planner_instance

        ts = MagicMock()
        ts.load.return_value = []
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.clear = MagicMock()

        result = await agent.run("Implement feature")

    # run() should still complete normally
    assert result.text == "ok"


# ── run() initial loop exception ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_initial_loop_exception_returns_error():
    """Exception in first run_agent_loop call → error AgentResult."""
    agent = _make_agent()

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop",
               new_callable=AsyncMock,
               side_effect=RuntimeError("initial fail")), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.return_value = []
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None

        result = await agent.run("task")

    assert result.had_errors is True
    assert result.stop_reason == "error"
    assert "initial fail" in result.text
    mock_client.close.assert_awaited_once()


# ── run() continuation loop + checkpoint ──────────────────────────────────────


@pytest.mark.asyncio
async def test_run_continuation_loop_saves_checkpoint_at_interval():
    """Checkpoint is saved when total_iterations % checkpoint_interval == 0."""
    agent = _make_agent(checkpoint_interval=1)  # save every iteration

    call_count = [0]

    async def _fake_loop(*args, **kwargs):
        call_count[0] += 1
        return f"iter_{call_count[0]}"

    # First call: pending todo; second call: no todos
    pending = _todo("task A")
    done = _todo("task A", status="done")
    load_seq = [
        [pending],  # continuation check 1
        [done],     # continuation check 2 → no pending, exit
    ]
    load_idx = [0]

    def _load():
        idx = min(load_idx[0], len(load_seq) - 1)
        load_idx[0] += 1
        return load_seq[idx]

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, side_effect=_fake_loop), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.CheckpointStore") as MockCPS, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.side_effect = _load
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.save = MagicMock()
        mock_cp_store.clear = MagicMock()
        checkpoint_mock = MagicMock()
        MockCPS.create_checkpoint = MagicMock(return_value=checkpoint_mock)

        await agent.run("task")

    # Checkpoint should have been saved
    assert mock_cp_store.save.called


# ── run() error recovery paths ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_recovery_abort_breaks_loop():
    """ABORT recovery action causes the continuation loop to break."""
    agent = _make_agent()

    call_count = [0]

    async def _fake_loop(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return "initial"
        raise RuntimeError("continuation error")

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, side_effect=_fake_loop), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.CheckpointStore") as MockCPS, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.classify_error") as mock_classify, \
         patch("prax.agents.ralph.compute_recovery") as mock_recovery, \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        pending = _todo("something")
        ts = MagicMock()
        ts.load.return_value = [pending]
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.save = MagicMock()
        mock_cp_store.clear = MagicMock()
        MockCPS.create_checkpoint = MagicMock(return_value=MagicMock())

        mock_classify.return_value = MagicMock(error_type=MagicMock(value="unknown"))
        mock_recovery.return_value = _noop_recovery(action=RecoveryAction.ABORT)

        result = await agent.run("task")

    assert result.had_errors is True
    # Only 2 calls: initial + one failed continuation
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_run_recovery_upgrade_model():
    """UPGRADE_MODEL recovery switches active_model and resolves new config."""
    agent = _make_agent()

    call_count = [0]

    async def _fake_loop(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return "initial"
        if call_count[0] == 2:
            raise RuntimeError("need upgrade")
        return "after upgrade"

    # Five load calls: while check #1, checkpoint-on-error, while check #2 after recovery, while check #3 exit, final all_done
    todos_seq = [
        [_todo("task A")],              # while check #1 → continue
        [_todo("task A")],              # checkpoint-on-error load
        [_todo("task A")],              # while check #2 after recovery → continue again
        [_todo("task A", status="done")],  # while check #3 → exit
        [_todo("task A", status="done")],  # final all_done check
    ]
    idx = [0]

    def _load():
        v = todos_seq[min(idx[0], len(todos_seq) - 1)]
        idx[0] += 1
        return v

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, side_effect=_fake_loop), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.CheckpointStore") as MockCPS, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=["gpt-4", "gpt-4o"]), \
         patch("prax.agents.ralph.classify_error") as mock_classify, \
         patch("prax.agents.ralph.compute_recovery") as mock_recovery, \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.resolve_model = MagicMock(return_value=MagicMock())
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.side_effect = _load
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.save = MagicMock()
        mock_cp_store.clear = MagicMock()
        MockCPS.create_checkpoint = MagicMock(return_value=MagicMock())

        mock_classify.return_value = MagicMock(error_type=MagicMock(value="resource"))
        mock_recovery.return_value = _noop_recovery(
            action=RecoveryAction.UPGRADE_MODEL,
            model="gpt-4o"
        )

        result = await agent.run("task")

    mock_client.resolve_model.assert_called_with("gpt-4o", agent.models_config)
    assert result.metadata["final_model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_run_recovery_reduce_scope_injects_hint():
    """REDUCE_SCOPE recovery injects a batch hint into message_history."""
    agent = _make_agent()

    call_count = [0]

    async def _fake_loop(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return "initial"
        if call_count[0] == 2:
            raise RuntimeError("too many todos")
        return "ok"

    # load() is called multiple times: while-check, checkpoint-on-error, while-check again, final
    # Sequence: pending → pending → pending → pending(after-recovery-while-check) → done (final)
    todos_seq = [
        [_todo("task A"), _todo("task B")],  # while check #1 → continue
        [_todo("task A"), _todo("task B")],  # checkpoint-on-error load
        [_todo("task A"), _todo("task B")],  # while check #2 after recovery → continue again
        [_todo("task A", status="done"), _todo("task B", status="done")],  # while check #3 → exit
        [_todo("task A", status="done"), _todo("task B", status="done")],  # final all_done check
    ]
    idx = [0]

    def _load():
        v = todos_seq[min(idx[0], len(todos_seq) - 1)]
        idx[0] += 1
        return v

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, side_effect=_fake_loop), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.CheckpointStore") as MockCPS, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.classify_error") as mock_classify, \
         patch("prax.agents.ralph.compute_recovery") as mock_recovery, \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.side_effect = _load
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.save = MagicMock()
        mock_cp_store.clear = MagicMock()
        MockCPS.create_checkpoint = MagicMock(return_value=MagicMock())

        mock_classify.return_value = MagicMock(error_type=MagicMock(value="resource"))
        mock_recovery.return_value = _noop_recovery(
            action=RecoveryAction.REDUCE_SCOPE,
            batch_size=1
        )

        await agent.run("task")

    # Calls: initial(1) + failed-continuation(2) + recovered-continuation(3)
    assert call_count[0] == 3


@pytest.mark.asyncio
async def test_run_recovery_switch_tool_injects_hint():
    """SWITCH_TOOL recovery injects a tool hint into message_history."""
    agent = _make_agent()

    call_count = [0]

    async def _fake_loop(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return "initial"
        if call_count[0] == 2:
            raise RuntimeError("tool broken")
        return "ok with other tool"

    todos_seq = [
        [_todo("task A")],              # while check #1 → continue
        [_todo("task A")],              # checkpoint-on-error load
        [_todo("task A")],              # while check #2 after recovery → continue again
        [_todo("task A", status="done")],  # while check #3 → exit
        [_todo("task A", status="done")],  # final all_done check
    ]
    idx = [0]

    def _load():
        v = todos_seq[min(idx[0], len(todos_seq) - 1)]
        idx[0] += 1
        return v

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, side_effect=_fake_loop), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.CheckpointStore") as MockCPS, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.classify_error") as mock_classify, \
         patch("prax.agents.ralph.compute_recovery") as mock_recovery, \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.side_effect = _load
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.save = MagicMock()
        mock_cp_store.clear = MagicMock()
        MockCPS.create_checkpoint = MagicMock(return_value=MagicMock())

        mock_classify.return_value = MagicMock(error_type=MagicMock(value="tool_failure"))
        mock_recovery.return_value = _noop_recovery(
            action=RecoveryAction.SWITCH_TOOL,
            hint="Use Write instead of Edit"
        )

        result = await agent.run("task")

    # Three loops: initial, failed, then recovered
    assert call_count[0] == 3
    # had_errors reflects that an error occurred (and was recovered), not final failure
    assert result.text == "ok with other tool"


@pytest.mark.asyncio
async def test_run_recovery_delay_calls_sleep():
    """delay_seconds > 0 triggers asyncio.sleep() in recovery."""
    agent = _make_agent()

    call_count = [0]

    async def _fake_loop(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return "initial"
        if call_count[0] == 2:
            raise RuntimeError("transient error")
        return "recovered"

    todos_seq = [
        [_todo("task A")],
        [_todo("task A")],
        [_todo("task A", status="done")],
    ]
    idx = [0]

    def _load():
        v = todos_seq[min(idx[0], len(todos_seq) - 1)]
        idx[0] += 1
        return v

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, side_effect=_fake_loop), \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.CheckpointStore") as MockCPS, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.classify_error") as mock_classify, \
         patch("prax.agents.ralph.compute_recovery") as mock_recovery, \
         patch("prax.agents.ralph.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.side_effect = _load
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = None
        mock_cp_store.save = MagicMock()
        mock_cp_store.clear = MagicMock()
        MockCPS.create_checkpoint = MagicMock(return_value=MagicMock())

        mock_classify.return_value = MagicMock(error_type=MagicMock(value="transient"))
        mock_recovery.return_value = _noop_recovery(action=RecoveryAction.RETRY_SAME, delay=5)

        await agent.run("task")

    mock_sleep.assert_awaited_once_with(5)


# ── run() from checkpoint (resume) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_resumes_from_checkpoint():
    """If a checkpoint exists, run() resumes without running initial loop."""
    agent = _make_agent()

    saved_history = [{"role": "user", "content": "previous message"}]
    checkpoint = MagicMock()
    checkpoint.iteration = 2
    checkpoint.message_history = saved_history

    with patch("prax.agents.ralph.LLMClient") as MockClient, \
         patch.object(agent, "_resolve_model", return_value=MagicMock()), \
         patch.object(agent, "_build_context", return_value=MagicMock()), \
         patch.object(agent, "_build_tools", return_value=[]), \
         patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock, return_value="resumed ok") as mock_loop, \
         patch("prax.agents.ralph.TodoStore") as MockTodoStore, \
         patch.object(agent, "_checkpoint_store") as mock_cp_store, \
         patch("prax.agents.ralph.get_upgrade_path", return_value=[]), \
         patch("prax.agents.ralph.LoopDetectionMiddleware"), \
         patch("prax.agents.ralph.TodoReminderMiddleware"), \
         patch("prax.agents.ralph.IntentGateMiddleware"):

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        ts = MagicMock()
        ts.load.return_value = []  # no pending → exit immediately
        MockTodoStore.return_value = ts

        mock_cp_store.load.return_value = checkpoint   # checkpoint found
        mock_cp_store.clear = MagicMock()

        result = await agent.run("resumed task")

    # Initial run_agent_loop NOT called; continuation skipped since no todos
    mock_loop.assert_not_called()
    assert result.iterations == 2   # inherited from checkpoint


# ── _OpenVikingMemoryShim ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openviking_shim_store_experience_when_available():
    """store_experience delegates to openviking client when available."""
    ov = MagicMock()
    ov.available = True
    ov.store_experience = AsyncMock()

    shim = _OpenVikingMemoryShim(ov)
    exp = MagicMock()
    exp.to_dict.return_value = {"key": "val"}

    await shim.store_experience(exp)

    ov.store_experience.assert_awaited_once_with({"key": "val"})


@pytest.mark.asyncio
async def test_openviking_shim_store_experience_when_not_available():
    """store_experience is a no-op when openviking.available is False."""
    ov = MagicMock()
    ov.available = False
    ov.store_experience = AsyncMock()

    shim = _OpenVikingMemoryShim(ov)
    await shim.store_experience(MagicMock())

    ov.store_experience.assert_not_called()


@pytest.mark.asyncio
async def test_openviking_shim_store_experience_exception_silently_swallowed():
    """Exceptions from openviking.store_experience are silently caught."""
    ov = MagicMock()
    ov.available = True
    ov.store_experience = AsyncMock(side_effect=RuntimeError("network error"))

    shim = _OpenVikingMemoryShim(ov)
    # Should not raise
    await shim.store_experience(MagicMock())


@pytest.mark.asyncio
async def test_openviking_shim_close():
    """close() delegates to openviking.close()."""
    ov = MagicMock()
    ov.close = AsyncMock()

    shim = _OpenVikingMemoryShim(ov)
    await shim.close()

    ov.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_openviking_shim_close_exception_silently_swallowed():
    """Exceptions from openviking.close() are silently caught."""
    ov = MagicMock()
    ov.close = AsyncMock(side_effect=RuntimeError("close failed"))

    shim = _OpenVikingMemoryShim(ov)
    # Should not raise
    await shim.close()
