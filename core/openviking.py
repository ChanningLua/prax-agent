"""OpenViking gRPC client — global context and experience service.

Provides project context, code search, session history, vector search,
and fact-based memory storage (DeerFlow pattern).

When OpenViking is unavailable, all methods degrade gracefully to local fallbacks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class OpenVikingClient:
    """OpenViking global service client.

    Connects to the OpenViking gRPC service for:
    - Project context retrieval
    - Code search and indexing
    - Session history persistence
    - Vector semantic search
    - Fact-based global experience storage (DeerFlow pattern)

    Falls back gracefully when the service is unavailable.
    """

    def __init__(self, host: str = "localhost", port: int = 50051):
        self._host = host
        self._port = port
        self._channel = None
        self._stub = None
        self._available = False
        self._try_connect()

    def _try_connect(self) -> None:
        """Attempt to connect to OpenViking gRPC service."""
        try:
            import grpc  # type: ignore
            self._channel = grpc.insecure_channel(f"{self._host}:{self._port}")
            # Lazy: mark available, actual RPC calls will fail if service is down
            self._available = True
            logger.debug("OpenViking channel created at %s:%d", self._host, self._port)
        except ImportError:
            logger.debug("grpc not installed — OpenViking unavailable, using local fallbacks")
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    # ── Project Context ──────────────────────────────────────────────

    async def get_project_context(self, path: str) -> str:
        """Retrieve project context for the given path.

        Returns structured context string or empty string if unavailable.
        """
        if not self._available:
            return ""
        try:
            return await self._rpc_get_project_context(path)
        except Exception as e:
            logger.debug("OpenViking get_project_context failed: %s", e)
            return ""

    async def _rpc_get_project_context(self, path: str) -> str:
        # Placeholder: implement with generated protobuf stubs
        # from .proto import openviking_pb2, openviking_pb2_grpc
        # stub = openviking_pb2_grpc.OpenVikingStub(self._channel)
        # response = await stub.GetProjectContext(openviking_pb2.ProjectContextRequest(path=path))
        # return response.context
        return ""

    # ── Code Search ──────────────────────────────────────────────────

    async def search_code(self, query: str, path: str = ".") -> list[dict[str, Any]]:
        """Search code in the given path using OpenViking's code index."""
        if not self._available:
            return []
        try:
            return await self._rpc_search_code(query, path)
        except Exception as e:
            logger.debug("OpenViking search_code failed: %s", e)
            return []

    async def _rpc_search_code(self, query: str, path: str) -> list[dict[str, Any]]:
        return []

    # ── Session History ──────────────────────────────────────────────

    async def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieve message history for a session."""
        if not self._available:
            return []
        try:
            return await self._rpc_get_session_history(session_id)
        except Exception as e:
            logger.debug("OpenViking get_session_history failed: %s", e)
            return []

    async def save_session(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Persist session messages to OpenViking."""
        if not self._available:
            return
        try:
            await self._rpc_save_session(session_id, messages)
        except Exception as e:
            logger.debug("OpenViking save_session failed: %s", e)

    async def _rpc_get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        return []

    async def _rpc_save_session(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        pass

    # ── Vector Search ────────────────────────────────────────────────

    async def vector_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Semantic similarity search over stored content."""
        if not self._available:
            return []
        try:
            return await self._rpc_vector_search(query, top_k)
        except Exception as e:
            logger.debug("OpenViking vector_search failed: %s", e)
            return []

    async def vector_store(self, content: str, metadata: dict[str, Any]) -> None:
        """Store content with metadata in the vector database."""
        if not self._available:
            return
        try:
            await self._rpc_vector_store(content, metadata)
        except Exception as e:
            logger.debug("OpenViking vector_store failed: %s", e)

    async def _rpc_vector_search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        return []

    async def _rpc_vector_store(self, content: str, metadata: dict[str, Any]) -> None:
        pass

    # ── Global Experience (DeerFlow Fact-based Memory) ───────────────

    async def get_experiences(self, task_type: str) -> list[dict[str, Any]]:
        """Retrieve relevant global experiences for a task type.

        Implements DeerFlow's fact-based memory pattern:
        experiences are structured facts accumulated across sessions.
        """
        if not self._available:
            return []
        try:
            return await self._rpc_get_experiences(task_type)
        except Exception as e:
            logger.debug("OpenViking get_experiences failed: %s", e)
            return []

    async def store_experience(self, fact: dict[str, Any]) -> None:
        """Store a structured fact/experience in OpenViking.

        Fact schema (DeerFlow pattern):
        {
            "task_type": str,       # e.g. "refactor", "debug", "implement"
            "context": str,         # what was the situation
            "insight": str,         # what was learned
            "outcome": str,         # what happened as a result
            "tags": list[str],      # searchable tags
            "timestamp": str,       # ISO 8601
        }
        """
        if not self._available:
            return
        try:
            await self._rpc_store_experience(fact)
        except Exception as e:
            logger.debug("OpenViking store_experience failed: %s", e)

    async def _rpc_get_experiences(self, task_type: str) -> list[dict[str, Any]]:
        return []

    async def _rpc_store_experience(self, fact: dict[str, Any]) -> None:
        pass

    def format_experiences_for_prompt(self, experiences: list[dict[str, Any]]) -> str:
        """Format experiences list for injection into system prompt."""
        if not experiences:
            return ""
        lines = ["## Global Experiences"]
        for exp in experiences[:10]:  # cap at 10 to avoid token bloat
            insight = exp.get("insight", "")
            task_type = exp.get("task_type", "")
            if insight:
                lines.append(f"- [{task_type}] {insight}")
        return "\n".join(lines) if len(lines) > 1 else ""

    async def close(self) -> None:
        """Close the gRPC channel."""
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
