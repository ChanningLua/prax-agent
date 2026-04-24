"""Ralph Loop workflow — continuous execution loop.

Wraps RalphAgent for use as a standalone workflow entry point.
"""

from __future__ import annotations

from typing import Any, Callable

from ..agents.ralph import RalphAgent
from ..core.config_files import load_memory_config
from ..core.memory import get_memory_backend
from ..core.model_router import ModelRouter


async def ralph_loop(
    task: str,
    *,
    cwd: str,
    models_config: dict | None = None,
    memory_config: dict | None = None,
    # Legacy params kept for backward compat (ignored when memory_config given)
    openviking_host: str = "localhost",
    openviking_port: int = 50051,
    on_text: Callable[[str], None] | None = None,
    max_iterations: int = 50,
) -> str:
    """Run Ralph continuous execution loop.

    Executes task and continues until all todos are complete.

    Args:
        task: The task to execute
        cwd: Working directory
        models_config: Model configuration dict
        memory_config: Memory backend config dict.  If None, loaded from
            .prax/config.yaml automatically.
        openviking_host / openviking_port: Kept for backward compat.
            Prefer setting memory.backend=openviking in config.yaml instead.
        on_text: Optional text output callback
        max_iterations: Maximum loop iterations

    Returns:
        Final result text
    """
    # Resolve memory backend from config
    if memory_config is None:
        memory_config = load_memory_config(cwd)

    memory = get_memory_backend(memory_config)
    router = ModelRouter.from_cwd(cwd)
    model = router.route(task)

    agent = RalphAgent(
        cwd=cwd,
        model=model,
        models_config=models_config or {},
        memory_backend=memory,
        on_text=on_text,
        max_iterations=max_iterations,
    )

    result = await agent.run(task)
    await memory.close()
    return result.text
