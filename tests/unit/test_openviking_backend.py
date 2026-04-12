"""Unit tests for OpenVikingBackend (prax/core/memory/openviking_backend.py).

All gRPC/network interactions are mocked — no real I/O.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.core.memory.backend import Experience, Fact, MemoryContext


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_backend(available: bool = True, verified: bool = True):
    """Build an OpenVikingBackend with a mocked OpenVikingClient."""
    mock_client = MagicMock()
    mock_client.available = available

    with patch("prax.core.memory.openviking_backend.OpenVikingClient", return_value=mock_client):
        if available:
            # Simulate ping succeeding so verified=True without a real event loop
            with patch("asyncio.get_event_loop") as mock_loop:
                mock_lp = MagicMock()
                mock_lp.is_running.return_value = True  # optimistic path: loop running
                mock_loop.return_value = mock_lp
                from prax.core.memory.openviking_backend import OpenVikingBackend
                backend = OpenVikingBackend(host="localhost", port=50051)
        else:
            from prax.core.memory.openviking_backend import OpenVikingBackend
            backend = OpenVikingBackend(host="localhost", port=50051)

    backend._client = mock_client
    return backend, mock_client


# ── verified property ────────────────────────────────────────────────────────


def test_verified_true_when_client_available() -> None:
    backend, _ = _make_backend(available=True)
    assert backend.verified is True


def test_verified_false_when_client_unavailable() -> None:
    backend, _ = _make_backend(available=False)
    assert backend.verified is False


# ── get_facts — always empty ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_facts_returns_empty() -> None:
    backend, _ = _make_backend()
    facts = await backend.get_facts("/some/cwd")
    assert facts == []


# ── store_fact / delete_fact — no-op ────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_fact_is_noop() -> None:
    backend, mock_client = _make_backend()
    fact = Fact(id="f1", content="test", category="context", confidence=0.8)
    await backend.store_fact("/cwd", fact)
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_delete_fact_is_noop() -> None:
    backend, mock_client = _make_backend()
    await backend.delete_fact("/cwd", "f1")
    mock_client.assert_not_called()


# ── get_context ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_context_delegates_to_client() -> None:
    backend, mock_client = _make_backend()
    mock_client.get_project_context = AsyncMock(return_value="project context text")

    ctx = await backend.get_context("/cwd")

    assert isinstance(ctx, MemoryContext)
    assert ctx.work_context == "project context text"
    mock_client.get_project_context.assert_called_once_with("/cwd")


# ── get_experiences ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_experiences_delegates_to_client() -> None:
    backend, mock_client = _make_backend()
    exp_dict = {
        "id": "e1",
        "task_type": "debug",
        "context": "ctx",
        "insight": "use logs",
        "outcome": "completed",
        "tags": ["python"],
        "timestamp": "2026-01-01T00:00:00+00:00",
        "project": "prax",
    }
    mock_client.get_experiences = AsyncMock(return_value=[exp_dict])

    experiences = await backend.get_experiences("debug", limit=5)

    assert len(experiences) == 1
    assert experiences[0].insight == "use logs"


@pytest.mark.asyncio
async def test_get_experiences_respects_limit() -> None:
    backend, mock_client = _make_backend()
    exps = [
        {"id": f"e{i}", "task_type": "debug", "context": "", "insight": f"insight {i}",
         "outcome": "completed", "tags": [], "timestamp": "", "project": ""}
        for i in range(10)
    ]
    mock_client.get_experiences = AsyncMock(return_value=exps)

    experiences = await backend.get_experiences("debug", limit=3)
    assert len(experiences) <= 3


# ── store_experience ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_experience_calls_client() -> None:
    backend, mock_client = _make_backend()
    mock_client.store_experience = AsyncMock()

    exp = Experience(
        id="e1", task_type="refactor", context="ctx",
        insight="extract method", outcome="completed",
        tags=[], timestamp="2026-01-01T00:00:00+00:00", project="prax",
    )
    await backend.store_experience(exp)

    mock_client.store_experience.assert_called_once()
    call_arg = mock_client.store_experience.call_args[0][0]
    assert call_arg["task_type"] == "refactor"


# ── format_for_prompt ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_format_for_prompt_includes_context_and_experiences() -> None:
    backend, mock_client = _make_backend()
    mock_client.get_project_context = AsyncMock(return_value="OpenViking context")
    mock_client.get_experiences = AsyncMock(return_value=[
        {"id": "e1", "task_type": "debug", "context": "", "insight": "trace logs",
         "outcome": "completed", "tags": [], "timestamp": "", "project": ""}
    ])
    mock_client.format_experiences_for_prompt = MagicMock(
        return_value="## Global Experiences\n- [debug] trace logs"
    )

    result = await backend.format_for_prompt("/cwd", task_type="debug")

    assert "OpenViking context" in result
    assert "trace logs" in result


# ── close ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_delegates_to_client() -> None:
    backend, mock_client = _make_backend()
    mock_client.close = AsyncMock()

    await backend.close()

    mock_client.close.assert_called_once()
