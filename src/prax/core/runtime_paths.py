"""Runtime path metadata for Prax execution modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RUNTIME_NATIVE = "native"
RUNTIME_CLAUDE_CODE = "claude_code"


@dataclass(frozen=True)
class RuntimePathInfo:
    runtime_path: str
    integration_mode: str
    executor: str


OPENPRAX_NATIVE = RuntimePathInfo(
    runtime_path=RUNTIME_NATIVE,
    integration_mode="native",
    executor="direct-api",
)

OPENPRAX_FOR_CLAUDE_CODE = RuntimePathInfo(
    runtime_path=RUNTIME_CLAUDE_CODE,
    integration_mode="claude_code",
    executor="claude-cli",
)

OPENPRAX_CLAUDE_DEBUG_BRIDGE = RuntimePathInfo(
    runtime_path=RUNTIME_CLAUDE_CODE,
    integration_mode="claude_cli_bridge",
    executor="claude-cli",
)


def build_last_run_metadata(
    *,
    model: str,
    runtime: RuntimePathInfo,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "model": model,
        "executor": runtime.executor,
        "runtime_path": runtime.runtime_path,
        "integration_mode": runtime.integration_mode,
    }
    if extra:
        data.update(extra)
    return data
