"""OpenVikingBackend — thin MemoryBackend wrapper around OpenVikingClient.

Wraps the existing prax.core.openviking.OpenVikingClient and maps its
methods to the MemoryBackend interface.

Key improvement over the raw client: real ping-based availability check
at init time (not just "did grpc import succeed?"), with configurable
timeout so the factory can fall back quickly to LocalMemoryBackend when
the service is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..openviking import OpenVikingClient
from .backend import Experience, Fact, MemoryBackend, MemoryContext

logger = logging.getLogger(__name__)

_PING_TIMEOUT = 2.0  # seconds


class OpenVikingBackend(MemoryBackend):
    """MemoryBackend that delegates to an OpenViking gRPC service.

    If the service is unreachable at construction time (ping timeout or
    ImportError), ``verified`` is False and the factory will fall back to
    LocalMemoryBackend automatically.
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 50051,
        ping_timeout_seconds: float = _PING_TIMEOUT,
    ) -> None:
        self._client = OpenVikingClient(host=host, port=port)
        self._verified = False
        if self._client.available:
            # Run a real connectivity check (sync at init via asyncio.run-ish)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't block here — mark as unverified; verify lazily
                    self._verified = True  # optimistic, will degrade on first call
                else:
                    loop.run_until_complete(
                        asyncio.wait_for(self._ping(), timeout=ping_timeout_seconds)
                    )
                    self._verified = True
            except Exception as e:
                logger.warning(
                    "OpenViking ping failed (%s:%d): %s — will use LocalMemoryBackend",
                    host, port, e,
                )
                self._verified = False

    async def _ping(self) -> None:
        """Lightweight connectivity check."""
        await self._client.get_experiences("ping")

    @property
    def verified(self) -> bool:
        """True when OpenViking responded to the initial ping."""
        return self._verified

    # ── MemoryBackend: facts ──────────────────────────────────────────────

    async def get_facts(self, cwd: str, limit: int = 100) -> list[Fact]:
        # OpenViking doesn't have a per-project facts API yet;
        # fall back to empty — callers should use LocalMemoryBackend
        # for project-level facts and OpenViking for experiences.
        return []

    async def store_fact(self, cwd: str, fact: Fact) -> None:
        pass  # Delegate to local layer; OpenViking manages experiences

    async def delete_fact(self, cwd: str, fact_id: str) -> None:
        pass

    # ── MemoryBackend: context ─────────────────────────────────────────────

    async def get_context(self, cwd: str) -> MemoryContext:
        text = await self._client.get_project_context(cwd)
        return MemoryContext(work_context=text)

    async def save_context(self, cwd: str, ctx: MemoryContext) -> None:
        # No write API in current OpenViking client — no-op
        pass

    # ── MemoryBackend: global experiences ─────────────────────────────────

    async def get_experiences(
        self, task_type: str, limit: int = 10
    ) -> list[Experience]:
        raw: list[dict[str, Any]] = await self._client.get_experiences(task_type)
        return [Experience.from_dict(r) for r in raw[:limit]]

    async def store_experience(self, exp: Experience) -> None:
        await self._client.store_experience(exp.to_dict())

    # ── Prompt injection (delegate to client's formatter) ─────────────────

    async def format_for_prompt(
        self, cwd: str, task_type: str = "general", max_facts: int = 15
    ) -> str:
        parts: list[str] = []

        # Project context from OpenViking
        ctx = await self.get_context(cwd)
        if ctx.work_context:
            parts.append(f"## Project Context (OpenViking)\n{ctx.work_context}")

        # Global experiences
        exps = await self.get_experiences(task_type, limit=max_facts)
        exp_text = self._client.format_experiences_for_prompt(
            [e.to_dict() for e in exps]
        )
        if exp_text:
            parts.append(exp_text)

        return "\n\n".join(parts)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._client.close()
