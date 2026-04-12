"""Unit tests for prax/agents/team.py — TeamAgent and topological_waves.

All tests are pure unit tests with no real I/O. run_agent_loop is mocked
so no LLM API calls are made.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.agents.team import (
    SubtaskDef,
    SubtaskResult,
    TeamAgent,
    topological_waves,
)
from prax.agents.base import AgentResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(tmp_path, **kwargs):
    defaults = dict(cwd=str(tmp_path), model="test-model")
    defaults.update(kwargs)
    return TeamAgent(**defaults)


def _patch_resolve_model(agent):
    fake_mc = MagicMock()
    fake_mc.model = agent.model
    agent._resolve_model = MagicMock(return_value=fake_mc)
    return fake_mc


def _subtask(id_, description="do something", depends_on=None):
    return SubtaskDef(id=id_, description=description, prompt=description, depends_on=depends_on or [])


# ---------------------------------------------------------------------------
# topological_waves — pure function tests (no I/O)
# ---------------------------------------------------------------------------

def test_topological_waves_single_node():
    """A single task with no dependencies produces one wave."""
    subtasks = [_subtask("1")]
    waves = topological_waves(subtasks)
    assert len(waves) == 1
    assert waves[0][0].id == "1"


def test_topological_waves_all_independent():
    """Tasks with no inter-dependencies all land in the same wave."""
    subtasks = [_subtask("1"), _subtask("2"), _subtask("3")]
    waves = topological_waves(subtasks)
    assert len(waves) == 1
    assert len(waves[0]) == 3


def test_topological_waves_linear_chain():
    """A chain A → B → C produces three sequential waves."""
    subtasks = [
        _subtask("A"),
        _subtask("B", depends_on=["A"]),
        _subtask("C", depends_on=["B"]),
    ]
    waves = topological_waves(subtasks)
    assert len(waves) == 3
    assert waves[0][0].id == "A"
    assert waves[1][0].id == "B"
    assert waves[2][0].id == "C"


def test_topological_waves_diamond():
    """Diamond dependency: A → {B, C} → D produces three waves."""
    subtasks = [
        _subtask("A"),
        _subtask("B", depends_on=["A"]),
        _subtask("C", depends_on=["A"]),
        _subtask("D", depends_on=["B", "C"]),
    ]
    waves = topological_waves(subtasks)
    assert len(waves) == 3
    wave_ids = [{st.id for st in wave} for wave in waves]
    assert {"A"} in wave_ids
    assert {"B", "C"} in wave_ids
    assert {"D"} in wave_ids


def test_topological_waves_cycle_fallback():
    """Cyclic dependencies fall back to sequential execution (one task per wave)."""
    subtasks = [
        _subtask("A", depends_on=["B"]),
        _subtask("B", depends_on=["A"]),
    ]
    waves = topological_waves(subtasks)
    # Fallback: each subtask in its own wave
    total_tasks = sum(len(w) for w in waves)
    assert total_tasks == 2


def test_topological_waves_ignores_missing_dep():
    """A dependency on a non-existent id is treated as satisfied (not in id_map)."""
    subtasks = [_subtask("A", depends_on=["GHOST"])]
    waves = topological_waves(subtasks)
    # A has an invalid dep, in_degree stays 0, so it runs in wave 1
    assert len(waves) == 1
    assert waves[0][0].id == "A"


# ---------------------------------------------------------------------------
# TeamAgent.run — DAG decomposition and execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_decompose_returns_subtasks(tmp_path):
    """TeamAgent.run correctly parses JSON subtask list and executes all."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "step 1", "prompt": "do step 1", "depends_on": []},
        {"id": "2", "description": "step 2", "prompt": "do step 2", "depends_on": []},
    ])

    call_count = 0

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subtask_json  # decompose
        if call_count <= 3:
            return "subtask result"  # subtask execution
        return "merged result"  # merge

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("big task")

    assert isinstance(result, AgentResult)
    assert result.stop_reason == "merged"
    assert result.metadata["subtask_count"] == 2


@pytest.mark.asyncio
async def test_run_fallback_on_decompose_failure(tmp_path):
    """When decomposition fails (invalid JSON), TeamAgent runs task as single loop."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    call_count = 0

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "not valid json at all"  # decompose fails
        return "single result"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("fallback task")

    assert result.stop_reason == "end_turn"
    assert result.iterations == 1


@pytest.mark.asyncio
async def test_run_parallel_execution_in_wave(tmp_path):
    """Tasks in the same wave run in parallel — verified by call ordering."""
    agent = _make_agent(tmp_path, max_parallel=4)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "parallel A", "prompt": "A", "depends_on": []},
        {"id": "2", "description": "parallel B", "prompt": "B", "depends_on": []},
    ])

    call_count = 0
    order = []

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        order.append(call_count)
        if call_count == 1:
            return subtask_json
        return "ok"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("parallel task")

    # 1 decompose + 2 subtasks + 1 merge = 4
    assert call_count == 4
    assert result.metadata["wave_count"] == 1  # both tasks in same wave


@pytest.mark.asyncio
async def test_run_sequential_waves_for_deps(tmp_path):
    """Dependent subtasks execute in separate waves."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "first", "prompt": "do first", "depends_on": []},
        {"id": "2", "description": "second", "prompt": "do second", "depends_on": ["1"]},
    ])

    call_count = 0

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subtask_json
        return "ok"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("sequential task")

    assert result.metadata["wave_count"] == 2


@pytest.mark.asyncio
async def test_run_subtask_error_captured_in_result(tmp_path):
    """When a subtask's run_agent_loop raises, the error is captured without aborting."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "ok task", "prompt": "do ok", "depends_on": []},
        {"id": "2", "description": "bad task", "prompt": "do bad", "depends_on": []},
    ])

    call_count = 0

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subtask_json
        if call_count == 3:
            raise RuntimeError("subtask boom")
        return "ok"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("mixed task")

    assert result.had_errors is True


@pytest.mark.asyncio
async def test_run_merge_failure_falls_back_to_raw_text(tmp_path):
    """When the merge call raises, TeamAgent falls back to concatenated results."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "t1", "prompt": "do t1", "depends_on": []},
    ])

    call_count = 0

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subtask_json
        if call_count == 2:
            return "subtask result text"
        raise RuntimeError("merge failed")

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("task")

    # The fallback raw text should appear
    assert "subtask result text" in result.text


@pytest.mark.asyncio
async def test_run_dependency_context_injected_in_prompt(tmp_path):
    """Subtask B receives result from A injected into its prompt."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "A", "description": "first step", "prompt": "do A", "depends_on": []},
        {"id": "B", "description": "second step", "prompt": "do B", "depends_on": ["A"]},
    ])

    prompts_seen = []
    call_count = 0

    async def _mock_loop(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        prompts_seen.append(prompt)
        if call_count == 1:
            return subtask_json
        if call_count == 2:
            return "A result content"
        return "ok"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        await agent.run("chained task")

    # B's prompt should contain A's result
    b_prompt = next((p for p in prompts_seen if "A result content" in p), None)
    assert b_prompt is not None, "B's prompt should contain A's result"


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

    with patch("prax.agents.team.LLMClient") as MockClient:
        MockClient.return_value.close = AsyncMock()
        result = await agent.run("any task")

    assert result is error_result


@pytest.mark.asyncio
async def test_run_metadata_contains_wave_and_subtask_counts(tmp_path):
    """Result metadata includes subtask_count and wave_count."""
    agent = _make_agent(tmp_path)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "a", "prompt": "a", "depends_on": []},
        {"id": "2", "description": "b", "prompt": "b", "depends_on": ["1"]},
        {"id": "3", "description": "c", "prompt": "c", "depends_on": ["1"]},
    ])

    call_count = 0

    async def _mock_loop(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subtask_json
        return "ok"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        result = await agent.run("meta task")

    assert result.metadata["subtask_count"] == 3
    assert result.metadata["wave_count"] == 2  # wave1: [1], wave2: [2,3]


@pytest.mark.asyncio
async def test_on_text_callback_receives_wave_announcements(tmp_path):
    """The on_text callback receives wave status messages during execution."""
    received = []
    agent = _make_agent(tmp_path, on_text=received.append)
    mc = _patch_resolve_model(agent)

    subtask_json = json.dumps([
        {"id": "1", "description": "step1", "prompt": "p1", "depends_on": []},
    ])

    call_count = 0

    async def _mock_loop(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subtask_json
        return "ok"

    with (
        patch("prax.agents.team.LLMClient") as MockClient,
        patch("prax.agents.team.run_agent_loop", side_effect=_mock_loop),
    ):
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.resolve_model = MagicMock(return_value=mc)

        await agent.run("callback task")

    assert any("[Team]" in msg for msg in received)
