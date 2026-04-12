"""Unit tests for prax/core/openviking.py.

All gRPC calls are mocked; no real network connections are made.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from prax.core.openviking import OpenVikingClient


# ── construction ──────────────────────────────────────────────────────────────

class TestOpenVikingClientInit:
    def test_unavailable_when_grpc_not_installed(self) -> None:
        with patch.dict("sys.modules", {"grpc": None}):
            # Force ImportError path by patching the import inside _try_connect
            with patch("builtins.__import__", side_effect=ImportError("no grpc")):
                client = OpenVikingClient.__new__(OpenVikingClient)
                client._host = "localhost"
                client._port = 50051
                client._channel = None
                client._stub = None
                client._available = False
                client._try_connect()
            assert client.available is False

    def test_available_when_grpc_installed(self) -> None:
        mock_grpc = MagicMock()
        mock_grpc.insecure_channel.return_value = MagicMock()
        with patch.dict("sys.modules", {"grpc": mock_grpc}):
            client = OpenVikingClient(host="localhost", port=50051)
        assert client.available is True

    def test_custom_host_and_port(self) -> None:
        mock_grpc = MagicMock()
        mock_grpc.insecure_channel.return_value = MagicMock()
        with patch.dict("sys.modules", {"grpc": mock_grpc}):
            client = OpenVikingClient(host="remote", port=9999)
        assert client._host == "remote"
        assert client._port == 9999


# ── fallbacks when unavailable ────────────────────────────────────────────────

class TestOpenVikingFallbacks:
    def _unavailable_client(self) -> OpenVikingClient:
        client = OpenVikingClient.__new__(OpenVikingClient)
        client._host = "localhost"
        client._port = 50051
        client._channel = None
        client._stub = None
        client._available = False
        return client

    @pytest.mark.asyncio
    async def test_get_project_context_returns_empty_when_unavailable(self) -> None:
        client = self._unavailable_client()
        result = await client.get_project_context("/some/path")
        assert result == ""

    @pytest.mark.asyncio
    async def test_search_code_returns_empty_when_unavailable(self) -> None:
        client = self._unavailable_client()
        result = await client.search_code("def foo")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_session_history_returns_empty_when_unavailable(self) -> None:
        client = self._unavailable_client()
        result = await client.get_session_history("session-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_save_session_is_noop_when_unavailable(self) -> None:
        client = self._unavailable_client()
        await client.save_session("session-1", [{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_vector_search_returns_empty_when_unavailable(self) -> None:
        client = self._unavailable_client()
        result = await client.vector_search("query", top_k=3)
        assert result == []

    @pytest.mark.asyncio
    async def test_vector_store_is_noop_when_unavailable(self) -> None:
        client = self._unavailable_client()
        await client.vector_store("content", {"meta": "data"})

    @pytest.mark.asyncio
    async def test_get_experiences_returns_empty_when_unavailable(self) -> None:
        client = self._unavailable_client()
        result = await client.get_experiences("refactor")
        assert result == []

    @pytest.mark.asyncio
    async def test_store_experience_is_noop_when_unavailable(self) -> None:
        client = self._unavailable_client()
        await client.store_experience({
            "task_type": "debug",
            "context": "ci failing",
            "insight": "check env vars",
            "outcome": "fixed",
            "tags": ["ci"],
            "timestamp": "2026-01-01T00:00:00Z",
        })


# ── format_experiences_for_prompt ────────────────────────────────────────────

class TestFormatExperiencesForPrompt:
    def _client(self) -> OpenVikingClient:
        client = OpenVikingClient.__new__(OpenVikingClient)
        client._available = False
        return client

    def test_empty_returns_empty_string(self) -> None:
        client = self._client()
        assert client.format_experiences_for_prompt([]) == ""

    def test_single_experience(self) -> None:
        client = self._client()
        experiences = [{"task_type": "debug", "insight": "check logs first"}]
        result = client.format_experiences_for_prompt(experiences)
        assert "## Global Experiences" in result
        assert "check logs first" in result
        assert "[debug]" in result

    def test_experience_without_insight_skipped(self) -> None:
        client = self._client()
        experiences = [{"task_type": "refactor", "insight": ""}]
        result = client.format_experiences_for_prompt(experiences)
        # Only the header is present → returns ""
        assert result == ""

    def test_capped_at_ten_experiences(self) -> None:
        client = self._client()
        experiences = [
            {"task_type": "t", "insight": f"insight-{i}"}
            for i in range(15)
        ]
        result = client.format_experiences_for_prompt(experiences)
        # At most 10 entries
        assert result.count("insight-") == 10


# ── close ─────────────────────────────────────────────────────────────────────

class TestOpenVikingClose:
    @pytest.mark.asyncio
    async def test_close_calls_channel_close(self) -> None:
        mock_grpc = MagicMock()
        mock_channel = MagicMock()
        mock_grpc.insecure_channel.return_value = mock_channel
        with patch.dict("sys.modules", {"grpc": mock_grpc}):
            client = OpenVikingClient(host="localhost", port=50051)
        await client.close()
        mock_channel.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_with_no_channel_is_safe(self) -> None:
        client = OpenVikingClient.__new__(OpenVikingClient)
        client._channel = None
        client._available = False
        await client.close()  # must not raise


# ── RPC exception degradation ─────────────────────────────────────────────────

class TestOpenVikingRpcDegradation:
    def _available_client(self) -> OpenVikingClient:
        mock_grpc = MagicMock()
        mock_grpc.insecure_channel.return_value = MagicMock()
        with patch.dict("sys.modules", {"grpc": mock_grpc}):
            return OpenVikingClient(host="localhost", port=50051)

    @pytest.mark.asyncio
    async def test_get_project_context_degrades_on_exception(self) -> None:
        client = self._available_client()
        with patch.object(client, "_rpc_get_project_context", side_effect=RuntimeError("rpc boom")):
            result = await client.get_project_context("/p")
        assert result == ""

    @pytest.mark.asyncio
    async def test_search_code_degrades_on_exception(self) -> None:
        client = self._available_client()
        with patch.object(client, "_rpc_search_code", side_effect=RuntimeError("rpc boom")):
            result = await client.search_code("query")
        assert result == []

    @pytest.mark.asyncio
    async def test_vector_search_degrades_on_exception(self) -> None:
        client = self._available_client()
        with patch.object(client, "_rpc_vector_search", side_effect=RuntimeError("rpc boom")):
            result = await client.vector_search("q")
        assert result == []
