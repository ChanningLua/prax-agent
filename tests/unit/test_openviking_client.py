"""Unit tests for OpenVikingClient (prax/core/openviking.py).

All grpc interactions are mocked — no real network calls.
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fake_grpc():
    """Return a fake grpc module."""
    grpc_mod = types.ModuleType("grpc")

    class FakeChannel:
        def close(self):
            pass

    grpc_mod.insecure_channel = MagicMock(return_value=FakeChannel())
    return grpc_mod


def _make_client_with_grpc():
    """Return an OpenVikingClient with grpc mocked as available."""
    fake_grpc = _make_fake_grpc()
    with patch.dict(sys.modules, {"grpc": fake_grpc}):
        # Force reimport so _try_connect picks up mocked grpc
        if "prax.core.openviking" in sys.modules:
            del sys.modules["prax.core.openviking"]
        import importlib
        mod = importlib.import_module("prax.core.openviking")
        client = mod.OpenVikingClient(host="localhost", port=50051)
    # Restore the module for subsequent imports
    return client, mod


def _make_client_without_grpc():
    """Return an OpenVikingClient where grpc is not installed."""
    with patch.dict(sys.modules, {"grpc": None}):
        if "prax.core.openviking" in sys.modules:
            del sys.modules["prax.core.openviking"]
        import importlib
        mod = importlib.import_module("prax.core.openviking")
        client = mod.OpenVikingClient(host="localhost", port=50051)
    return client, mod


# ── available property ────────────────────────────────────────────────────────


def test_available_true_when_grpc_installed() -> None:
    client, _ = _make_client_with_grpc()
    assert client.available is True


def test_available_false_when_grpc_not_installed() -> None:
    client, _ = _make_client_without_grpc()
    assert client.available is False


# ── get_project_context ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_project_context_returns_empty_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False
    client._channel = None

    result = await client.get_project_context("/some/path")
    assert result == ""


@pytest.mark.asyncio
async def test_get_project_context_delegates_to_rpc_when_available() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = True
    client._channel = MagicMock()

    with patch.object(client, "_rpc_get_project_context", AsyncMock(return_value="ctx text")):
        result = await client.get_project_context("/path")
    assert result == "ctx text"


@pytest.mark.asyncio
async def test_get_project_context_returns_empty_on_rpc_exception() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = True
    client._channel = MagicMock()

    with patch.object(client, "_rpc_get_project_context", AsyncMock(side_effect=RuntimeError("rpc fail"))):
        result = await client.get_project_context("/path")
    assert result == ""


# ── search_code ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_code_returns_empty_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    result = await client.search_code("query", "/path")
    assert result == []


@pytest.mark.asyncio
async def test_search_code_delegates_to_rpc_when_available() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = True
    client._channel = MagicMock()

    expected = [{"file": "foo.py", "score": 0.9}]
    with patch.object(client, "_rpc_search_code", AsyncMock(return_value=expected)):
        result = await client.search_code("query")
    assert result == expected


# ── get_session_history ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session_history_returns_empty_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    result = await client.get_session_history("sess-1")
    assert result == []


@pytest.mark.asyncio
async def test_save_session_is_noop_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    # Should not raise
    await client.save_session("sess-1", [{"role": "user", "content": "hi"}])


# ── vector_search / vector_store ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vector_search_returns_empty_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    result = await client.vector_search("query", top_k=3)
    assert result == []


@pytest.mark.asyncio
async def test_vector_store_is_noop_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    await client.vector_store("some content", {"key": "val"})


# ── get_experiences / store_experience ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_experiences_returns_empty_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    result = await client.get_experiences("debug")
    assert result == []


@pytest.mark.asyncio
async def test_store_experience_is_noop_when_unavailable() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = False

    await client.store_experience({"task_type": "debug", "insight": "use logs"})


# ── format_experiences_for_prompt ─────────────────────────────────────────────


def test_format_experiences_for_prompt_empty_list() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = True

    result = client.format_experiences_for_prompt([])
    assert result == ""


def test_format_experiences_for_prompt_with_experiences() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = True

    experiences = [
        {"task_type": "debug", "insight": "use print statements"},
        {"task_type": "refactor", "insight": "extract small functions"},
    ]
    result = client.format_experiences_for_prompt(experiences)
    assert "## Global Experiences" in result
    assert "[debug] use print statements" in result
    assert "[refactor] extract small functions" in result


def test_format_experiences_for_prompt_caps_at_ten() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._available = True

    experiences = [
        {"task_type": "debug", "insight": f"insight {i}"}
        for i in range(15)
    ]
    result = client.format_experiences_for_prompt(experiences)
    # 10 items + the header line
    lines = [l for l in result.splitlines() if l.startswith("- ")]
    assert len(lines) == 10


# ── close ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_calls_channel_close() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    mock_channel = MagicMock()
    client._channel = mock_channel
    client._available = True

    await client.close()
    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
async def test_close_is_safe_when_no_channel() -> None:
    from prax.core.openviking import OpenVikingClient
    client = OpenVikingClient.__new__(OpenVikingClient)
    client._channel = None
    client._available = False

    await client.close()  # should not raise
