"""Sisyphus Agent — main orchestrator with intelligent task decomposition.

- Classifies task intent (research/implement/diagnose/refactor)
- Routes to appropriate specialist agent (Ralph/Team/direct)
- Adapts plan based on intermediate results
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .base import AgentResult, BaseAgent
from .ralph import RalphAgent
from .team import TeamAgent
from ..core.agent_loop import run_agent_loop
from ..core.context import Context
from ..core.llm_client import LLMClient
from ..core.middleware import LoopDetectionMiddleware, TodoReminderMiddleware
from ..tools.todo_write import TodoWriteTool
from ..tools.task import TaskTool

logger = logging.getLogger(__name__)

SISYPHUS_ROUTING_PROMPT = """Analyze this task and determine the best execution strategy.

Task: {task}

Context:
- Pending todos in store: {todo_count}
- Task length: {task_length} chars

Choose ONE strategy:
1. "ralph" — Long-running task with multiple todos, needs continuous execution
2. "team" — Task can be split into 2-6 independent parallel subtasks
3. "direct" — Simple question/answer or single-step task

Examples:
- "实现用户认证功能" → ralph: needs multiple sequential todos
- "分析 3 个竞品的 API 设计" → team: 3 independent parallel subtasks
- "解释这个函数怎么工作" → direct: simple explanation, no todos needed
- "Fix the login bug" → ralph: needs inspect → fix → verify todos
- "Write tests for modules A, B, C" → team: 3 independent test tasks

Respond with ONLY valid JSON, no other text:
{{"strategy": "ralph", "reason": "one-line reason"}}
"""


class SisyphusAgent(BaseAgent):
    """Main orchestrator — intelligently routes tasks to specialist agents.

    Routing logic:
    - Research/explain tasks → direct execution
    - Implementation with multiple todos → Ralph (continuous)
    - Parallelizable work → Team (concurrent)
    - Complex multi-phase → sub-Sisyphus recursion
    """

    name = "sisyphus"
    description = "Intelligent task orchestrator with adaptive routing"

    def __init__(
        self,
        *,
        cwd: str,
        model: str = "glm-4-flash",
        models_config: dict | None = None,
        openviking: Any = None,           # legacy, kept for compat
        memory_backend: Any = None,       # MemoryBackend (preferred)
        on_text: Callable[[str], None] | None = None,
    ):
        super().__init__(
            cwd=cwd,
            model=model,
            openviking=openviking,
            memory_backend=memory_backend,
            on_text=on_text,
        )
        self.models_config = models_config or {}

    async def run(self, task: str, **kwargs: Any) -> AgentResult:
        """Run Sisyphus: classify task and route to appropriate agent."""
        self._emit(f"[Sisyphus] Analyzing: {task[:80]}...")

        client = LLMClient()
        model_config = self._resolve_model(client, self.models_config)
        if isinstance(model_config, AgentResult):
            await client.close()
            return model_config

        # Classify task and determine routing
        strategy = await self._classify_strategy(task, client, model_config)
        await client.close()
        self._emit(f"[Sisyphus] Strategy: {strategy}")

        if strategy == "ralph":
            agent = RalphAgent(
                cwd=self.cwd,
                model=self.model,
                models_config=self.models_config,
                memory_backend=self.memory_backend,
                openviking=self.openviking,
                on_text=self.on_text,
            )
            return await agent.run(task)

        elif strategy == "team":
            agent = TeamAgent(
                cwd=self.cwd,
                model=self.model,
                models_config=self.models_config,
                memory_backend=self.memory_backend,
                openviking=self.openviking,
                on_text=self.on_text,
            )
            return await agent.run(task)

        else:
            # Direct execution
            return await self._run_direct(task)

    async def _classify_strategy(
        self,
        task: str,
        client: LLMClient,
        model_config: Any,
    ) -> str:
        """Use LLM to classify the best execution strategy.

        Requests a JSON response ``{"strategy": "...", "reason": "..."}`` for
        robust parsing. Falls back to "direct" on any error or ambiguous output.
        """
        from ..core.todo_store import TodoStore
        todo_count = len(TodoStore(self.cwd).load())
        context = Context(cwd=self.cwd, model=self.model)
        prompt = SISYPHUS_ROUTING_PROMPT.format(
            task=task,
            todo_count=todo_count,
            task_length=len(task),
        )
        try:
            result = await run_agent_loop(
                prompt,
                context=context,
                llm_client=client,
                model_config=model_config,
                tools=[],
                middlewares=[],
            )
            # Try JSON parsing first
            start = result.find("{")
            end = result.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(result[start:end])
                strategy = str(data.get("strategy", "")).lower().strip()
                if strategy in ("ralph", "team", "direct"):
                    return strategy
            # Fallback: prefix matching on raw text
            result_lower = result.lower().strip()
            if result_lower.startswith("ralph"):
                return "ralph"
            elif result_lower.startswith("team"):
                return "team"
        except Exception as e:
            logger.warning("Sisyphus classification failed: %s", e)

        return "direct"

    async def _run_direct(self, task: str) -> AgentResult:
        """Execute task directly without sub-agent routing."""
        client = LLMClient()
        model_config = self._resolve_model(client, self.models_config)
        if isinstance(model_config, AgentResult):
            await client.close()
            return model_config

        context = self._build_context()
        tools: list[Any] = [TodoWriteTool(cwd=self.cwd)]

        def _make_task_executor() -> Any:
            async def _executor(description: str, prompt: str, subagent_type: str, max_turns: int | None) -> str:
                sub = SisyphusAgent(
                    cwd=self.cwd,
                    model=self.model,
                    models_config=self.models_config,
                    memory_backend=self.memory_backend,
                    openviking=self.openviking,
                )
                result = await sub.run(prompt)
                return result.text
            return _executor

        tools.append(TaskTool(executor=_make_task_executor()))

        middlewares = [
            LoopDetectionMiddleware(),
            TodoReminderMiddleware(cwd=self.cwd),
        ]

        try:
            result = await run_agent_loop(
                task,
                context=context,
                llm_client=client,
                model_config=model_config,
                tools=tools,
                middlewares=middlewares,
                on_text=self.on_text,
            )
            return AgentResult(text=result, stop_reason="end_turn", iterations=1)
        except Exception as e:
            return AgentResult(
                text=f"[Sisyphus] Error: {e}",
                stop_reason="error",
                iterations=1,
                had_errors=True,
            )
        finally:
            await client.close()
