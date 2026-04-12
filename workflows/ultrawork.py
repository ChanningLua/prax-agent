"""Ultrawork workflow — one-click fully automated task execution.

- Automatically selects the best agent (Sisyphus/Ralph/Team)
- Injects memory context via MemoryBackend
- Reports progress and stores experiences
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..agents.sisyphus import SisyphusAgent
from ..core.config_files import load_memory_config
from ..core.memory import get_memory_backend
from ..core.memory.backend import Experience
from ..core.model_router import ModelRouter

logger = logging.getLogger(__name__)


async def ultrawork(
    task: str,
    *,
    cwd: str,
    models_config: dict | None = None,
    memory_config: dict | None = None,
    # Legacy params kept for backward compat — ignored when memory_config given
    openviking_host: str = "localhost",
    openviking_port: int = 50051,
    on_text: Callable[[str], None] | None = None,
) -> str:
    """One-click fully automated workflow.

    1. Resolve MemoryBackend from config (auto-loaded from .prax/config.yaml)
    2. Route to optimal model via ModelRouter
    3. Execute via Sisyphus (intelligent orchestration)
    4. Store experience back via MemoryBackend

    Args:
        task: The task to execute
        cwd: Working directory
        models_config: Model configuration dict
        memory_config: Memory backend config dict.  If None, loaded from
            .prax/config.yaml automatically.
        openviking_host / openviking_port: Deprecated — kept for backward compat.
            Prefer setting memory.backend=openviking in config.yaml instead.
        on_text: Optional text output callback

    Returns:
        Final result text
    """
    # Resolve memory backend from config
    if memory_config is None:
        memory_config = load_memory_config(cwd)

    memory = get_memory_backend(memory_config)

    # Route to optimal model
    router = ModelRouter.from_cwd(cwd)
    model = router.route(task)

    if on_text:
        on_text(f"[Ultrawork] model={model}, memory={type(memory).__name__}")

    # Execute via Sisyphus
    agent = SisyphusAgent(
        cwd=cwd,
        model=model,
        models_config=models_config or {},
        memory_backend=memory,
        on_text=on_text,
    )

    result = await agent.run(task)

    # Store experience via MemoryBackend
    try:
        import uuid
        from datetime import datetime, timezone

        task_type = router.classify_task(task)
        await memory.store_experience(Experience(
            id=f"exp_{uuid.uuid4().hex[:8]}",
            task_type=task_type,
            context=task[:300],
            insight=(
                f"Completed via {result.stop_reason} "
                f"in {result.iterations} iterations"
            ),
            outcome="success" if not result.had_errors else "partial",
            tags=["ultrawork", task_type, model],
            timestamp=datetime.now(timezone.utc).isoformat(),
            project=cwd,
        ))
    except Exception as e:
        logger.debug("Failed to store ultrawork experience: %s", e)

    await memory.close()
    return result.text
