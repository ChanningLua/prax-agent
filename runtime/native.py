from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prax.core.config_files import load_models_config
from prax.core.runtime_env import hydrate_runtime_env
from prax.core.runtime_paths import RUNTIME_NATIVE


@dataclass(frozen=True)
class NativeRunResult:
    session_id: str
    model: str
    runtime_path: str
    integration_mode: str
    executor: str
    usage: dict[str, int] | None = None


class NativeRuntime:
    """Native runtime facade.

    This wraps the existing Prax execution primitives but exposes them
    through a stable runtime-oriented interface for native execution.
    """

    async def run_task(
        self,
        task: str,
        *,
        cwd: str | None = None,
        model_override: str | None = None,
        permission_mode: Any | None = None,
        session_id: str | None = None,
    ) -> NativeRunResult:
        from prax.main import _bootstrap_session, _build_pipeline, _execute

        effective_cwd = str(Path(cwd or Path.cwd()))
        models_config = load_models_config(effective_cwd)
        hydrate_runtime_env(models_config, effective_cwd)

        model_name, agent_name, agent_system_prompt, session, session_store = _bootstrap_session(
            cwd=effective_cwd,
            task=task,
            model_override=model_override,
            session_id=session_id,
            models_config=models_config,
        )

        print(f"\033[90m[prax] path={RUNTIME_NATIVE} model={model_name} cwd={effective_cwd}\033[0m", flush=True)
        print(f"\033[90m[prax] session={session.session_id}\033[0m", flush=True)

        context, client, tools, middlewares = await _build_pipeline(
            cwd=effective_cwd,
            model_name=model_name,
            models_config=models_config,
            permission_mode=permission_mode,
            agent_name=agent_name,
            agent_system_prompt=agent_system_prompt,
            session=session,
        )

        await _execute(
            task,
            context=context,
            client=client,
            tools=tools,
            middlewares=middlewares,
            models_config=models_config,
            model_name=model_name,
            session=session,
            session_store=session_store,
        )

        final_session = session_store.load(session.session_id) or session
        last_run = (final_session.metadata or {}).get("last_run", {})
        return NativeRunResult(
            session_id=final_session.session_id,
            model=final_session.model or model_name,
            runtime_path=str(last_run.get("runtime_path", RUNTIME_NATIVE)),
            integration_mode=str(last_run.get("integration_mode", "native")),
            executor=str(last_run.get("executor", "direct-api")),
            usage=(final_session.metadata or {}).get("usage"),
        )

    def run_task_sync(
        self,
        task: str,
        *,
        cwd: str | None = None,
        model_override: str | None = None,
        permission_mode: Any | None = None,
        session_id: str | None = None,
    ) -> NativeRunResult:
        return asyncio.run(
            self.run_task(
                task,
                cwd=cwd,
                model_override=model_override,
                permission_mode=permission_mode,
                session_id=session_id,
            )
        )
