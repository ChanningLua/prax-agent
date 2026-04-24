"""Team Agent — DAG-aware parallel collaboration with async sub-agents.

Upgraded from naive parallel-all to dependency-aware execution:
- LLM decomposes task into subtasks with explicit dependencies
- Topological sort determines execution waves
- Independent tasks run in parallel; dependent tasks wait
- Results are merged and conflicts resolved
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .base import AgentResult, BaseAgent
from ..core.agent_loop import run_agent_loop
from ..core.context import Context
from ..core.llm_client import LLMClient
from ..core.middleware import LoopDetectionMiddleware
from ..tools.todo_write import TodoWriteTool

logger = logging.getLogger(__name__)

DECOMPOSE_PROMPT = """\
Decompose the following task into 2-6 subtasks. Some subtasks may depend on others.

Task: {task}

Return a JSON array of subtask objects:
[
  {{
    "id": "1",
    "description": "brief title",
    "prompt": "detailed instructions for this subtask",
    "depends_on": []
  }},
  {{
    "id": "2",
    "description": "another subtask that depends on subtask 1",
    "prompt": "detailed instructions...",
    "depends_on": ["1"]
  }}
]

Rules:
- Each subtask should be completable in a single agent loop
- Use depends_on to declare ordering constraints (list of subtask ids)
- Tasks with no dependencies (depends_on: []) can run in parallel
- Keep prompts specific and actionable
- Return ONLY the JSON array, no other text
"""

MERGE_PROMPT = """\
Merge the following subtask results into a coherent final response.

Original task: {task}

Subtask results (in execution order):
{results}

Provide a unified summary of what was accomplished, noting any conflicts or gaps.
"""


@dataclass
class SubtaskDef:
    """Parsed subtask definition from LLM decomposition."""
    id: str
    description: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SubtaskResult:
    subtask_id: str
    description: str
    result: str
    had_errors: bool = False


def topological_waves(subtasks: list[SubtaskDef]) -> list[list[SubtaskDef]]:
    """Compute execution waves via Kahn's algorithm (topological sort).

    Returns a list of waves. Tasks in the same wave have all dependencies
    satisfied by earlier waves and can run in parallel.

    Raises ValueError on cycles.
    """
    id_map = {st.id: st for st in subtasks}
    in_degree: dict[str, int] = {st.id: 0 for st in subtasks}
    dependents: dict[str, list[str]] = defaultdict(list)

    for st in subtasks:
        for dep_id in st.depends_on:
            if dep_id in id_map:
                in_degree[st.id] += 1
                dependents[dep_id].append(st.id)

    waves: list[list[SubtaskDef]] = []
    queue = deque(sid for sid, deg in in_degree.items() if deg == 0)

    processed = 0
    while queue:
        wave_ids = list(queue)
        queue.clear()
        wave = [id_map[sid] for sid in wave_ids]
        waves.append(wave)
        processed += len(wave)

        for sid in wave_ids:
            for dep_id in dependents[sid]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(dep_id)

    if processed < len(subtasks):
        # Cycle detected — fall back to sequential execution
        logger.warning("Cycle detected in subtask dependencies, falling back to sequential")
        return [[st] for st in subtasks]

    return waves


class TeamAgent(BaseAgent):
    """DAG-aware parallel collaboration agent.

    Implements dependency-aware task decomposition:
    1. LLM decomposes task into subtasks with depends_on
    2. Topological sort produces execution waves
    3. Tasks in each wave run in parallel
    4. Results from earlier waves are available as context
    5. Final merge produces coherent response
    """

    name = "team"
    description = "DAG-aware parallel multi-agent collaboration"

    def __init__(
        self,
        *,
        cwd: str,
        model: str = "glm-4-flash",
        models_config: dict | None = None,
        openviking: Any = None,           # legacy, kept for compat
        memory_backend: Any = None,       # MemoryBackend (preferred)
        on_text: Callable[[str], None] | None = None,
        max_parallel: int = 4,
    ):
        super().__init__(
            cwd=cwd,
            model=model,
            openviking=openviking,
            memory_backend=memory_backend,
            on_text=on_text,
        )
        self.models_config = models_config or {}
        self.max_parallel = max_parallel

    async def run(self, task: str, **kwargs: Any) -> AgentResult:
        """Run Team: decompose → topological sort → wave execution → merge."""
        self._emit(f"[Team] Starting: {task}")

        client = LLMClient()
        model_config = self._resolve_model(client, self.models_config)
        if isinstance(model_config, AgentResult):
            await client.close()
            return model_config

        # Step 1: Decompose task into subtasks with dependencies
        self._emit("[Team] Decomposing task...")
        subtasks = await self._decompose(task, client, model_config)

        if not subtasks:
            # Fallback: run as single task
            self._emit("[Team] Decomposition failed, running as single task")
            context = self._build_context()
            tools = [TodoWriteTool(cwd=self.cwd)]
            result = await run_agent_loop(
                task,
                context=context,
                llm_client=client,
                model_config=model_config,
                tools=tools,
                middlewares=[LoopDetectionMiddleware()],
                on_text=self.on_text,
            )
            await client.close()
            return AgentResult(text=result, stop_reason="end_turn", iterations=1)

        # Step 2: Topological sort into execution waves
        waves = topological_waves(subtasks)
        self._emit(f"[Team] {len(subtasks)} subtasks in {len(waves)} waves")

        # Step 3: Execute waves sequentially, tasks within each wave in parallel
        all_results: list[SubtaskResult] = []
        completed_context: dict[str, str] = {}  # subtask_id -> result text
        semaphore = asyncio.Semaphore(self.max_parallel)
        total_iterations = 1  # counting decompose

        for wave_idx, wave in enumerate(waves):
            self._emit(f"[Team] Wave {wave_idx + 1}/{len(waves)}: "
                       f"{[st.id for st in wave]}")

            wave_results = await asyncio.gather(
                *[
                    self._run_subtask(
                        st, client, model_config, semaphore,
                        prior_results=completed_context,
                    )
                    for st in wave
                ],
                return_exceptions=True,
            )

            for i, res in enumerate(wave_results):
                st = wave[i]
                if isinstance(res, Exception):
                    sr = SubtaskResult(
                        subtask_id=st.id,
                        description=st.description,
                        result=f"Error: {res}",
                        had_errors=True,
                    )
                else:
                    sr = res  # type: ignore
                all_results.append(sr)
                completed_context[sr.subtask_id] = sr.result
                total_iterations += 1

        # Step 4: Merge results
        self._emit("[Team] Merging results...")
        merged = await self._merge(task, all_results, client, model_config)
        total_iterations += 1

        await client.close()

        had_errors = any(r.had_errors for r in all_results)
        return AgentResult(
            text=merged,
            stop_reason="merged",
            iterations=total_iterations,
            had_errors=had_errors,
            metadata={
                "subtask_count": len(subtasks),
                "wave_count": len(waves),
            },
        )

    async def _decompose(
        self,
        task: str,
        client: LLMClient,
        model_config: Any,
    ) -> list[SubtaskDef]:
        """Use LLM to decompose task into subtasks with dependencies."""
        context = Context(cwd=self.cwd, model=self.model)
        prompt = DECOMPOSE_PROMPT.format(task=task)
        try:
            result = await run_agent_loop(
                prompt,
                context=context,
                llm_client=client,
                model_config=model_config,
                tools=[],
                middlewares=[],
            )
            # Parse JSON from result
            start = result.find("[")
            end = result.rfind("]") + 1
            if start >= 0 and end > start:
                raw = json.loads(result[start:end])
                return [
                    SubtaskDef(
                        id=str(item.get("id", str(i))),
                        description=item.get("description", ""),
                        prompt=item.get("prompt", item.get("description", "")),
                        depends_on=[str(d) for d in item.get("depends_on", [])],
                    )
                    for i, item in enumerate(raw)
                ]
        except Exception as e:
            logger.warning("Task decomposition failed: %s", e)
        return []

    async def _run_subtask(
        self,
        subtask: SubtaskDef,
        client: LLMClient,
        model_config: Any,
        semaphore: asyncio.Semaphore,
        prior_results: dict[str, str] | None = None,
    ) -> SubtaskResult:
        """Run a single subtask, injecting results from dependencies as context."""
        async with semaphore:
            self._emit(f"[Team] Subtask {subtask.id}: {subtask.description}")

            # Build prompt with dependency context
            prompt_parts = [subtask.prompt]
            if subtask.depends_on and prior_results:
                dep_context = []
                for dep_id in subtask.depends_on:
                    if dep_id in prior_results:
                        dep_context.append(
                            f"Result from subtask {dep_id}:\n{prior_results[dep_id][:500]}"
                        )
                if dep_context:
                    prompt_parts.insert(
                        0,
                        "## Context from completed dependencies\n"
                        + "\n\n".join(dep_context)
                        + "\n\n## Your task\n",
                    )

            full_prompt = "\n".join(prompt_parts)

            # Isolated context — no shared state between subtasks
            context = self._build_context()
            tools = [TodoWriteTool(cwd=self.cwd)]

            try:
                result = await run_agent_loop(
                    full_prompt,
                    context=context,
                    llm_client=client,
                    model_config=model_config,
                    tools=tools,
                    middlewares=[LoopDetectionMiddleware(hard_limit=5)],
                )
                return SubtaskResult(
                    subtask_id=subtask.id,
                    description=subtask.description,
                    result=result,
                )
            except Exception as e:
                return SubtaskResult(
                    subtask_id=subtask.id,
                    description=subtask.description,
                    result=f"Error: {e}",
                    had_errors=True,
                )

    async def _merge(
        self,
        task: str,
        results: list[SubtaskResult],
        client: LLMClient,
        model_config: Any,
    ) -> str:
        """Merge subtask results into a coherent response."""
        results_text = "\n\n".join(
            f"### Subtask {r.subtask_id}: {r.description}\n{r.result}"
            for r in results
        )
        prompt = MERGE_PROMPT.format(task=task, results=results_text)
        context = Context(cwd=self.cwd, model=self.model)
        try:
            return await run_agent_loop(
                prompt,
                context=context,
                llm_client=client,
                model_config=model_config,
                tools=[],
                middlewares=[],
                on_text=self.on_text,
            )
        except Exception as e:
            logger.warning("Result merge failed: %s", e)
            return results_text
