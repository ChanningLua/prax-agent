"""PermissionGuard middleware — enforces tool permission levels.

Checks each tool's permission_level against the active PermissionMode
before execution, blocking or warning as appropriate.

Permission matrix:
    PermissionMode     | SAFE  | REVIEW   | DANGEROUS
    -------------------|-------|----------|----------
    READ_ONLY          | allow | block    | block
    WORKSPACE_WRITE    | allow | allow    | block
    DANGER_FULL_ACCESS | allow | allow    | allow

Additionally applies a 4-axis risk score check (tool_risk, file_sensitivity,
impact_scope, reversibility). Calls with total score > risk_threshold are
blocked unless the mode is DANGER_FULL_ACCESS.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .middleware import AgentMiddleware, RuntimeState
from .permissions import ExecutionPolicy, PermissionMode
from .risk_scorer import RiskScorer
from ..tools.base import PermissionLevel, Tool, ToolCall, ToolResult

if TYPE_CHECKING:
    from .governance import GovernanceConfig

logger = logging.getLogger(__name__)


# Maps PermissionMode to the maximum PermissionLevel it allows
_MODE_MAX_LEVEL: dict[PermissionMode, set[PermissionLevel]] = {
    PermissionMode.READ_ONLY: {PermissionLevel.SAFE},
    PermissionMode.WORKSPACE_WRITE: {PermissionLevel.SAFE, PermissionLevel.REVIEW},
    PermissionMode.DANGER_FULL_ACCESS: {
        PermissionLevel.SAFE,
        PermissionLevel.REVIEW,
        PermissionLevel.DANGEROUS,
    },
}


class PermissionGuardMiddleware(AgentMiddleware):
    """Enforce tool permission levels based on the active permission mode.

    Blocks tool execution when the tool's required permission exceeds
    what the current PermissionMode allows.

    Also applies a 4-axis risk score check. Calls with total score above
    risk_threshold are blocked unless mode is DANGER_FULL_ACCESS.
    """

    def __init__(
        self,
        *,
        permission_mode: PermissionMode = PermissionMode.WORKSPACE_WRITE,
        risk_threshold: int = 15,
        on_permission_denied: Any | None = None,
        governance: "GovernanceConfig | None" = None,
    ):
        # GovernanceConfig overrides individual params when provided
        if governance is not None:
            try:
                permission_mode = PermissionMode(governance.permission_mode.replace("_", "-"))
            except ValueError:
                pass
            risk_threshold = governance.risk_threshold

        self._mode = permission_mode
        self._allowed = _MODE_MAX_LEVEL.get(permission_mode, {PermissionLevel.SAFE})
        self._risk_threshold = risk_threshold
        self._scorer = RiskScorer()
        self._denied_count = 0
        self._on_denied = on_permission_denied

    @property
    def denied_count(self) -> int:
        return self._denied_count

    async def before_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        tool: Tool | None,
    ) -> ToolResult | None:
        if tool is None:
            # Unknown tool — let agent_loop handle the "unknown tool" error
            return None

        # 1. Classic permission-level check
        required = tool.required_permission(tool_call.input)
        if required not in self._allowed:
            self._denied_count += 1
            msg = (
                f"Permission denied: tool '{tool_call.name}' requires "
                f"'{required.value}' permission, but current mode is "
                f"'{self._mode.value}'. Change --permission-mode to allow this."
            )
            logger.warning(msg)
            if self._on_denied:
                self._on_denied(tool_call, required, self._mode)
            return ToolResult(
                content=f"Permission denied: {tool_call.name} requires '{required.value}' "
                        f"access (current mode: '{self._mode.value}')",
                is_error=True,
            )

        policy = ExecutionPolicy(state.context.cwd, self._mode)
        accesses = tool.file_accesses(tool_call.input)
        if not isinstance(accesses, list):
            accesses = []
        for access in accesses:
            decision = policy.authorize_path(access.path, write=access.write)
            if not decision.allowed:
                self._denied_count += 1
                logger.warning(
                    "Path access denied for %s: %s",
                    tool_call.name,
                    decision.reason,
                )
                return ToolResult(
                    content=f"Permission denied: {decision.reason}",
                    is_error=True,
                )

        # 2. Risk-score check (skip for DANGER_FULL_ACCESS)
        if self._mode != PermissionMode.DANGER_FULL_ACCESS:
            risk = self._scorer.score(tool_call.name, tool_call.input)
            if risk.total > self._risk_threshold:
                self._denied_count += 1
                logger.warning(
                    "High-risk operation blocked: %s — %s",
                    tool_call.name,
                    risk.summary(),
                )
                return ToolResult(
                    content=(
                        f"High-risk operation blocked: {tool_call.name}\n"
                        f"{risk.summary()}\n"
                        f"Use --permission-mode=danger to allow high-risk operations."
                    ),
                    is_error=True,
                )

        return None
