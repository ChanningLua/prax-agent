"""Prax TUI control plane."""
from __future__ import annotations

from typing import Any

from .app import PraxTUI


def launch_tui(
    cwd: str,
    event_bus: Any = None,
    agent_name: str = "unknown",
    model_name: str = "unknown",
    **kwargs: Any,
) -> None:
    """Launch the TUI control plane.

    Args:
        cwd: Current working directory (project root)
        event_bus: EventBus instance. If None, a fresh one is created.
        agent_name: Name of the current agent
        model_name: Name of the current model
    """
    if event_bus is None:
        from ..core.event_bus import EventBus
        event_bus = EventBus()

    app = PraxTUI(
        cwd=cwd,
        event_bus=event_bus,
        agent_name=agent_name,
        model_name=model_name,
        **kwargs,
    )
    app.run()


__all__ = ["PraxTUI", "launch_tui"]
