"""LLM-driven planning engine for Prax.

Replaces the static 3-step template with an LLM decomposition pass that
produces actionable todos with explicit dependency edges.  The sync fallback
``generate_initial_plan()`` is preserved for callers that cannot await.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .llm_client import LLMClient, ModelConfig

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlannedTodo:
    content: str
    active_form: str
    status: str
    id: str = ""                                   # stable id for depends_on refs
    depends_on: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "content": self.content,
            "activeForm": self.active_form,
            "status": self.status,
        }
        if self.id:
            d["id"] = self.id
        if self.depends_on:
            d["dependsOn"] = list(self.depends_on)
        return d


# ── Static fallback ────────────────────────────────────────────────────────────

def generate_initial_plan(task: str) -> list[PlannedTodo]:
    """Synchronous fallback plan — three generic steps.

    Use this when an LLMPlanner is not available or when the caller
    cannot await an async call.
    """
    normalized = " ".join(task.split()).strip()
    if not normalized:
        raise ValueError("task must not be empty")

    return [
        PlannedTodo(
            id="1",
            content=f"Inspect the task context for: {normalized}",
            active_form="Inspecting context",
            status="in_progress",
        ),
        PlannedTodo(
            id="2",
            content=f"Execute the main work required for: {normalized}",
            active_form="Executing main work",
            status="pending",
            depends_on=("1",),
        ),
        PlannedTodo(
            id="3",
            content=f"Verify and summarize results for: {normalized}",
            active_form="Verifying results",
            status="pending",
            depends_on=("2",),
        ),
    ]


# ── LLM planner ───────────────────────────────────────────────────────────────

_DECOMPOSE_PROMPT = """\
Decompose the following task into 3-8 concrete, actionable todos.

Task: {task}

Rules:
- Each todo must describe a single, clearly scoped unit of work.
- Use depends_on to express ordering constraints (list of todo ids).
  Todos with no dependencies can run in parallel.
- Keep activeForm short — present-participle phrase (e.g. "Writing tests").
- Return ONLY a JSON array, no other text.

Format:
[
  {{
    "id": "1",
    "content": "Brief imperative description of the work",
    "activeForm": "Doing the work",
    "status": "pending",
    "depends_on": []
  }},
  {{
    "id": "2",
    "content": "Next step that builds on step 1",
    "activeForm": "Building on step 1",
    "status": "pending",
    "depends_on": ["1"]
  }}
]
"""


class LLMPlanner:
    """Decomposes a task into a dependency-aware todo list using an LLM call.

    Usage::

        planner = LLMPlanner()
        todos = await planner.decompose(task, llm_client=client, model_config=cfg)

    Falls back to ``generate_initial_plan()`` on any error so callers always
    get a usable plan.
    """

    async def decompose(
        self,
        task: str,
        *,
        llm_client: "LLMClient",
        model_config: "ModelConfig",
        context: Any = None,
    ) -> list[PlannedTodo]:
        """Ask the LLM to break the task into todos with dependencies.

        Args:
            task: The top-level task description.
            llm_client: Initialized LLMClient to use for the planning call.
            model_config: Resolved model config (provider / API key etc.).
            context: Optional ``Context`` for system-prompt injection.
                     If None a minimal context is built internally.

        Returns:
            List of PlannedTodo items.  Never empty — falls back to the
            static plan on any parsing or network error.
        """
        normalized = " ".join(task.split()).strip()
        if not normalized:
            raise ValueError("task must not be empty")

        prompt = _DECOMPOSE_PROMPT.format(task=normalized)

        try:
            from .agent_loop import run_agent_loop
            from .context import Context

            ctx = context
            if ctx is None:
                ctx = Context(model=model_config.model)

            raw_text = await run_agent_loop(
                prompt,
                context=ctx,
                llm_client=llm_client,
                model_config=model_config,
                tools=[],
                middlewares=[],
            )

            todos = self._parse(raw_text)
            if todos:
                logger.debug("LLMPlanner produced %d todos", len(todos))
                return todos

            logger.warning("LLMPlanner returned empty list; using static fallback")
        except Exception as exc:
            logger.warning("LLMPlanner.decompose failed (%s); using static fallback", exc)

        return generate_initial_plan(normalized)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _parse(self, text: str) -> list[PlannedTodo]:
        """Extract and parse the JSON array from LLM output."""
        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0 or end <= start:
            return []

        try:
            raw: list[dict[str, Any]] = json.loads(text[start:end])
        except json.JSONDecodeError as exc:
            logger.debug("LLMPlanner JSON parse error: %s", exc)
            return []

        todos: list[PlannedTodo] = []
        seen_ids: set[str] = set()

        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                continue

            content = str(item.get("content", "")).strip()
            if not content:
                continue

            active_form = str(item.get("activeForm", item.get("active_form", ""))).strip()
            if not active_form:
                active_form = f"Working on step {i + 1}"

            status = str(item.get("status", "pending")).strip()
            if status not in {"pending", "in_progress", "completed"}:
                status = "pending"

            todo_id = str(item.get("id", str(i + 1))).strip()

            raw_deps = item.get("depends_on", item.get("dependsOn", []))
            depends_on = tuple(
                str(d) for d in (raw_deps if isinstance(raw_deps, list) else [])
                if str(d) in seen_ids        # only reference ids seen so far
            )

            todos.append(PlannedTodo(
                id=todo_id,
                content=content,
                active_form=active_form,
                status=status,
                depends_on=depends_on,
            ))
            seen_ids.add(todo_id)

        return todos
