"""Agent base class for Prax orchestration layer."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.context import Context
from ..core.llm_client import LLMClient, ModelConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result from an agent run."""
    text: str
    stop_reason: str
    iterations: int
    had_errors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Base class for all Prax agents.

    Provides shared execution helpers to avoid duplication across agents:
    - _resolve_model(): model config resolution with error AgentResult
    - _build_context(): Context construction with memory_backend wiring
    - _run_with_retry(): generic async retry wrapper
    """

    name: str = "base"
    description: str = ""

    def __init__(
        self,
        *,
        cwd: str,
        model: str = "glm-4-flash",
        openviking: Any = None,           # legacy, kept for compat
        memory_backend: Any = None,       # MemoryBackend (preferred)
        on_text: Callable[[str], None] | None = None,
    ):
        self.cwd = cwd
        self.model = model
        self.openviking = openviking       # retained for subclass backward compat
        self.memory_backend = memory_backend
        self.on_text = on_text

    @abstractmethod
    async def run(self, task: str, **kwargs: Any) -> AgentResult:
        """Execute the agent on the given task."""
        ...

    def _emit(self, text: str) -> None:
        if self.on_text:
            self.on_text(text)

    def _resolve_model(
        self,
        client: LLMClient,
        models_config: dict,
        *,
        agent_name: str | None = None,
    ) -> ModelConfig | AgentResult:
        """Resolve model configuration.

        Returns ModelConfig on success, or AgentResult(had_errors=True) on failure.
        Callers should check ``isinstance(result, AgentResult)`` and early-return.
        """
        label = agent_name or self.name
        try:
            return client.resolve_model(self.model, models_config)
        except ValueError:
            return AgentResult(
                text=f"[{label}] Model '{self.model}' not found in configuration",
                stop_reason="config_error",
                iterations=0,
                had_errors=True,
            )

    def _build_context(
        self,
        *,
        memory_backend: Any = None,
    ) -> Context:
        """Build a Context wired to memory_backend (or legacy openviking).

        Priority: explicit ``memory_backend`` arg → self.memory_backend → self.openviking
        """
        backend = memory_backend if memory_backend is not None else self.memory_backend
        if backend is not None:
            return Context(cwd=self.cwd, model=self.model, memory_backend=backend)
        # legacy fallback
        return Context(cwd=self.cwd, model=self.model, openviking=self.openviking)

    async def _run_with_retry(
        self,
        coro_factory: Callable[[], Any],
        *,
        max_retries: int = 3,
        agent_name: str | None = None,
    ) -> Any:
        """Run an async coroutine factory with retry on exception.

        ``coro_factory`` must be a zero-arg callable returning a fresh coroutine
        each time (needed because coroutines can only be awaited once).

        Raises the last exception if all attempts fail.
        """
        label = agent_name or self.name
        if max_retries <= 0:
            return await coro_factory()

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_factory()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[%s] Attempt %d/%d failed: %s",
                    label, attempt, max_retries, exc,
                )
        raise last_exc  # type: ignore[misc]
