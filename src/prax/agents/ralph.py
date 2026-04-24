"""Ralph Agent — continuous execution until all todos are complete.

- Runs until all TodoWrite items are marked done
- Structured error recovery: classify → strategize → recover
- Reports progress via MemoryBackend for cross-session visibility
- Full toolset: TodoWrite + Task delegation + Background tasks
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from typing import Any, Callable

from .base import AgentResult, BaseAgent
from ..core.agent_loop import run_agent_loop
from ..core.checkpoint import CheckpointStore
from ..core.context import Context
from ..core.error_recovery import (
    ErrorTracker,
    RecoveryAction,
    classify_error,
    compute_recovery,
)
from ..core.intent_gate import IntentGateMiddleware
from ..core.llm_client import LLMClient
from ..core.middleware import LoopDetectionMiddleware, TodoReminderMiddleware
from ..core.model_upgrade import get_upgrade_path
from ..core.planning import LLMPlanner
from ..core.skills_loader import filter_skills_by_task_type, format_skills_for_subagent, load_skills
from ..core.todo_store import TodoItem, TodoStore
from ..core.background_store import BackgroundTaskStore
from ..tools.todo_write import TodoWriteTool
from ..tools.task import TaskTool
from ..tools.background_task import (
    CancelTaskTool,
    CheckTaskTool,
    ListTasksTool,
    StartTaskTool,
    UpdateTaskTool,
)

logger = logging.getLogger(__name__)


class _OpenVikingMemoryShim:
    """Thin adapter so legacy OpenVikingClient quacks like a MemoryBackend
    for the subset of methods RalphAgent actually calls."""

    def __init__(self, openviking: Any) -> None:
        self._ov = openviking

    async def store_experience(self, exp: Any) -> None:
        if self._ov and self._ov.available:
            try:
                d = exp.to_dict() if hasattr(exp, "to_dict") else exp
                await self._ov.store_experience(d)
            except Exception:
                pass

    async def close(self) -> None:
        if self._ov:
            try:
                await self._ov.close()
            except Exception:
                pass

RALPH_SYSTEM_ADDENDUM = """
## Ralph Mode: Continuous Execution

You are running in Ralph mode. Your goal is to complete ALL pending todos before stopping.

Rules:
1. Check the todo list at the start of each iteration.
2. Work through todos one by one, marking each as done when complete.
3. If you encounter an error, try an alternative approach before giving up.
4. Do NOT stop until all todos are marked [done] or [cancelled].
5. After completing all todos, provide a brief summary of what was accomplished.
6. Use Task to delegate complex subtasks to isolated subagents.
7. Use StartTask/CheckTask for background work that can run in parallel.
"""

MAX_RALPH_ITERATIONS = 50
MAX_RETRY_ATTEMPTS = 3


class RalphAgent(BaseAgent):
    """Continuous execution agent — runs until all todos are complete.

    - Persistent execution across multiple agent loop iterations
    - Full tool access: TodoWrite, Task delegation, Background tasks
    - Error recovery with strategy switching
    - Progress reporting via MemoryBackend (store_experience)
    """

    name = "ralph"
    description = "Continuous execution until all todos complete"

    def __init__(
        self,
        *,
        cwd: str,
        model: str = "glm-4-flash",
        models_config: dict | None = None,
        openviking: Any = None,           # legacy, kept for compat
        memory_backend: Any = None,       # MemoryBackend (preferred)
        on_text: Callable[[str], None] | None = None,
        max_iterations: int = MAX_RALPH_ITERATIONS,
        extra_tools: list[Any] | None = None,
        checkpoint_interval: int = 3,
        session_id: str | None = None,
        use_llm_planner: bool = True,
    ):
        super().__init__(cwd=cwd, model=model, openviking=openviking, memory_backend=memory_backend, on_text=on_text)
        self.models_config = models_config or {}
        self.max_iterations = max_iterations
        self._extra_tools = extra_tools or []
        self._checkpoint_interval = checkpoint_interval
        self._session_id = session_id or f"ralph_{uuid.uuid4().hex[:8]}"
        self._checkpoint_store = CheckpointStore(cwd=cwd)
        self._use_llm_planner = use_llm_planner
        # Prefer explicit MemoryBackend; fall back to OpenViking shim for compat
        if memory_backend is not None:
            self._memory = memory_backend
        elif openviking is not None:
            self._memory = _OpenVikingMemoryShim(openviking)
        else:
            self._memory = None

    def _make_task_executor(self) -> Callable:
        """Create a task executor that spawns sub-Ralph agents for delegation."""
        async def _executor(
            description: str, prompt: str, subagent_type: str, max_turns: int | None,
            load_skills_names: list[str] | None = None,
        ) -> str:
            # Inject skill content if requested
            if load_skills_names:
                try:
                    all_skills = load_skills(self.cwd)
                    matched = [s for s in all_skills if s.name in load_skills_names]
                    skill_text = format_skills_for_subagent(matched)
                    if skill_text:
                        prompt = skill_text + "\n\n" + prompt
                except Exception:
                    pass  # skill injection failure should not block execution

            sub_client = LLMClient()
            try:
                sub_context = Context(cwd=self.cwd, model=self.model)
                sub_tools = [TodoWriteTool(cwd=self.cwd)]
                sub_middlewares = [
                    LoopDetectionMiddleware(hard_limit=max_turns or 5),
                    TodoReminderMiddleware(cwd=self.cwd),
                ]
                model_config = sub_client.resolve_model(self.model, self.models_config)
                result_text = await run_agent_loop(
                    f"[Delegated: {description}]\n{prompt}",
                    context=sub_context,
                    llm_client=sub_client,
                    model_config=model_config,
                    tools=sub_tools,
                    message_history=[],
                    middlewares=sub_middlewares,
                )
                return json.dumps({
                    "description": description,
                    "subagent_type": subagent_type,
                    "model": self.model,
                    "result": result_text,
                }, ensure_ascii=False, indent=2)
            finally:
                await sub_client.close()

        return _executor

    def _build_tools(self) -> list[Any]:
        """Build the full tool set for Ralph execution."""
        tools: list[Any] = [TodoWriteTool(cwd=self.cwd)]

        # Task delegation tool
        task_executor = self._make_task_executor()
        tools.append(TaskTool(executor=task_executor))

        # Background task tools
        store = BackgroundTaskStore(cwd=self.cwd)
        tools.extend([
            StartTaskTool(store=store, executor=task_executor),
            CheckTaskTool(store=store),
            UpdateTaskTool(store=store),
            CancelTaskTool(store=store),
            ListTasksTool(store=store),
        ])

        # Extra tools injected by caller
        tools.extend(self._extra_tools)
        return tools

    async def run(self, task: str, **kwargs: Any) -> AgentResult:
        """Run Ralph: execute task and continue until all todos are done.

        Supports checkpoint/resume: if a previous checkpoint exists for this
        session_id, resumes from the saved state instead of starting fresh.
        """
        client = LLMClient()
        context = self._build_context()
        todo_store = TodoStore(self.cwd)

        # Build full tool set
        tools = self._build_tools()

        middlewares = [
            LoopDetectionMiddleware(hard_limit=8),
            TodoReminderMiddleware(cwd=self.cwd),
            IntentGateMiddleware(strict=False),
        ]

        # Resolve model config
        model_config = self._resolve_model(client, self.models_config)
        if isinstance(model_config, AgentResult):
            await client.close()
            return model_config

        total_iterations = 0
        last_result = ""
        error_tracker = ErrorTracker()
        active_model = self.model

        # Build upgrade path for model escalation
        available_models = get_upgrade_path(self.model, self.models_config)

        # Try to resume from checkpoint
        checkpoint = self._checkpoint_store.load(self._session_id)
        if checkpoint is not None:
            self._emit(f"[Ralph] Resuming from checkpoint (iter={checkpoint.iteration})")
            message_history = copy.deepcopy(checkpoint.message_history)
            total_iterations = checkpoint.iteration
        else:
            self._emit(f"[Ralph] Starting: {task}")
            message_history = []

            # LLM-driven planning: decompose task into todos before first loop
            if self._use_llm_planner:
                try:
                    planner = LLMPlanner()
                    planned = await planner.decompose(
                        task, llm_client=client, model_config=model_config
                    )
                    if planned:
                        todo_store.save([
                            TodoItem(
                                content=p.content,
                                active_form=p.active_form,
                                status=p.status,
                            )
                            for p in planned
                        ])
                        self._emit(f"[Ralph] Planned {len(planned)} todos via LLM")
                        plan_summary = "\n".join(f"- {p.content}" for p in planned)
                        task = f"{task}\n\n## Pre-planned todos:\n{plan_summary}"
                except Exception as e:
                    logger.warning("LLMPlanner failed, proceeding without pre-planning: %s", e)

            # Initial task execution
            try:
                last_result = await run_agent_loop(
                    task + RALPH_SYSTEM_ADDENDUM,
                    context=context,
                    llm_client=client,
                    model_config=model_config,
                    tools=tools,
                    message_history=message_history,
                    middlewares=middlewares,
                    on_text=self.on_text,
                )
                total_iterations += 1
            except Exception as e:
                logger.error("Ralph initial run failed: %s", e)
                await client.close()
                return AgentResult(
                    text=f"[Ralph] Failed: {e}",
                    stop_reason="error",
                    iterations=total_iterations,
                    had_errors=True,
                )

        # Continue loop until all todos done
        consecutive_errors = 0
        while total_iterations < self.max_iterations:
            todos = todo_store.load()
            pending = [t for t in todos if t.status not in ("done", "cancelled")]

            if not pending:
                self._emit("[Ralph] All todos complete.")
                break

            self._emit(f"[Ralph] {len(pending)} todos remaining, continuing...")

            # Save checkpoint at interval
            if total_iterations > 0 and total_iterations % self._checkpoint_interval == 0:
                try:
                    cp = CheckpointStore.create_checkpoint(
                        session_id=self._session_id,
                        iteration=total_iterations,
                        task=task,
                        model=active_model,
                        message_history=copy.deepcopy(message_history),
                        todo_snapshot=[
                            {"content": t.content, "status": t.status}
                            for t in todos
                        ],
                    )
                    self._checkpoint_store.save(cp)
                    self._emit(f"[Ralph] Checkpoint saved at iteration {total_iterations}")
                except Exception as e:
                    logger.warning("Checkpoint save failed: %s", e)

            # Report progress via MemoryBackend
            if self._memory is not None:
                try:
                    from ..core.memory.backend import Experience
                    import uuid as _uuid
                    from datetime import datetime, timezone
                    await self._memory.store_experience(Experience(
                        id=f"exp_{_uuid.uuid4().hex[:8]}",
                        task_type="ralph_progress",
                        context=task[:200],
                        insight=f"{len(todos) - len(pending)}/{len(todos)} todos complete",
                        outcome="in_progress",
                        tags=["ralph", "progress"],
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        project=self.cwd,
                    ))
                except Exception:
                    pass

            continuation = f"Continue working. {len(pending)} todos still pending: " + \
                ", ".join(t.content[:50] for t in pending[:5])

            try:
                last_result = await run_agent_loop(
                    continuation,
                    context=context,
                    llm_client=client,
                    model_config=model_config,
                    tools=tools,
                    message_history=message_history,
                    middlewares=middlewares,
                    on_text=self.on_text,
                )
                total_iterations += 1
                consecutive_errors = 0  # reset on success
            except Exception as e:
                consecutive_errors += 1
                classification = classify_error(e, source=f"ralph_iter_{total_iterations}")
                error_tracker.record(classification)

                self._emit(
                    f"[Ralph] Error at iteration {total_iterations}: "
                    f"{classification.error_type.value} — {str(e)[:100]}"
                )

                # Compute recovery strategy
                strategy = compute_recovery(
                    classification,
                    retry_count=consecutive_errors,
                    max_retries=MAX_RETRY_ATTEMPTS,
                    available_models=available_models,
                    current_model=active_model,
                )
                self._emit(f"[Ralph] Recovery: {strategy.action.value} — {strategy.reason}")

                # Save checkpoint on error
                try:
                    cp = CheckpointStore.create_checkpoint(
                        session_id=self._session_id,
                        iteration=total_iterations,
                        task=task,
                        model=active_model,
                        message_history=copy.deepcopy(message_history),
                        todo_snapshot=[
                            {"content": t.content, "status": t.status}
                            for t in todo_store.load()
                        ],
                        metadata={
                            "error": str(e),
                            "error_type": classification.error_type.value,
                            "recovery_action": strategy.action.value,
                            "error_tracker": error_tracker.summary(),
                        },
                    )
                    self._checkpoint_store.save(cp)
                except Exception:
                    pass

                # Execute recovery strategy
                if strategy.action == RecoveryAction.ABORT:
                    self._emit("[Ralph] Aborting — recovery exhausted")
                    break

                if strategy.action == RecoveryAction.UPGRADE_MODEL:
                    if strategy.suggested_model:
                        self._emit(f"[Ralph] Upgrading model: {active_model} → {strategy.suggested_model}")
                        active_model = strategy.suggested_model
                        context.model = active_model
                        try:
                            model_config = client.resolve_model(active_model, self.models_config)
                        except ValueError:
                            self._emit(f"[Ralph] Model upgrade failed — '{active_model}' not available")
                            break

                if strategy.action == RecoveryAction.REDUCE_SCOPE:
                    # Inject a scope-reduction hint into the next continuation
                    batch_hint = strategy.suggested_batch_size or 1
                    continuation = (
                        f"The previous attempt failed due to resource limits. "
                        f"Focus on only {batch_hint} todo(s) at a time. "
                        f"Pending: " + ", ".join(t.content[:50] for t in pending[:batch_hint])
                    )
                    message_history.append({
                        "role": "user",
                        "content": continuation,
                    })

                if strategy.action == RecoveryAction.SWITCH_TOOL:
                    # Inject a tool-switch hint
                    hint = strategy.tool_hint or "Try a different approach."
                    message_history.append({
                        "role": "user",
                        "content": (
                            f"The previous tool approach failed. {hint} "
                            f"Try an alternative strategy for the current todo."
                        ),
                    })

                if strategy.delay_seconds > 0:
                    await asyncio.sleep(strategy.delay_seconds)

        await client.close()

        # Clear checkpoint on successful completion
        todos = todo_store.load()
        all_done = not [t for t in todos if t.status not in ("done", "cancelled")]
        if all_done:
            self._checkpoint_store.clear(self._session_id)

        # Store completion experience via MemoryBackend
        if self._memory is not None:
            try:
                from ..core.memory.backend import Experience
                import uuid as _uuid
                from datetime import datetime, timezone
                done_count = sum(1 for t in todos if t.status == "done")
                await self._memory.store_experience(Experience(
                    id=f"exp_{_uuid.uuid4().hex[:8]}",
                    task_type="ralph_completion",
                    context=task[:200],
                    insight=f"Completed {done_count}/{len(todos)} todos in {total_iterations} iterations",
                    outcome="completed" if all_done else "partial",
                    tags=["ralph", "completion"],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    project=self.cwd,
                ))
            except Exception:
                pass

        return AgentResult(
            text=last_result,
            stop_reason="todos_complete" if all_done else "partial",
            iterations=total_iterations,
            had_errors=error_tracker.total_errors > 0,
            metadata={
                "error_summary": error_tracker.summary() if error_tracker.total_errors > 0 else None,
                "final_model": active_model,
            },
        )
