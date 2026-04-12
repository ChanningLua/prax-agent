"""Agent status monitoring widget."""
from __future__ import annotations

from textual.widgets import Static
from textual.reactive import reactive
from rich.table import Table
from rich.panel import Panel


class AgentStatus(Static):
    """Right pane: agent/iteration/token status."""

    agent_name: reactive[str] = reactive("unknown")
    model_name: reactive[str] = reactive("unknown")
    iteration: reactive[int] = reactive(0)
    total_tokens: reactive[int] = reactive(0)
    tool_calls: reactive[int] = reactive(0)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def render(self) -> Panel:
        """Render the status panel."""
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", justify="right")
        table.add_column(style="white")

        table.add_row("Agent:", self.agent_name)
        table.add_row("Model:", self.model_name)
        table.add_row("Iteration:", str(self.iteration))
        table.add_row("Tool Calls:", str(self.tool_calls))
        table.add_row("Total Tokens:", f"{self.total_tokens:,}")

        return Panel(
            table,
            title="[bold]Agent Status[/bold]",
            border_style="cyan",
        )

    def update_agent(self, name: str, model: str) -> None:
        """Update agent and model info."""
        self.agent_name = name
        self.model_name = model

    def increment_iteration(self) -> None:
        """Increment iteration counter."""
        self.iteration += 1

    def increment_tool_calls(self) -> None:
        """Increment tool call counter."""
        self.tool_calls += 1

    def add_tokens(self, count: int) -> None:
        """Add to token counter."""
        self.total_tokens += count

    def reset(self) -> None:
        """Reset all counters."""
        self.agent_name = "unknown"
        self.model_name = "unknown"
        self.iteration = 0
        self.total_tokens = 0
        self.tool_calls = 0
