"""Permission mode enum and execution policy.

The actual permission enforcement is delegated to Claude Code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from prax.tools.base import PermissionLevel


class PermissionMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


@dataclass
class AuthDecision:
    allowed: bool
    reason: str = ""


class ExecutionPolicy:
    """Decide whether a tool call or file access is allowed under the current mode."""

    def __init__(self, workspace_root: str, permission_mode: PermissionMode) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.permission_mode = permission_mode

    def authorize_tool(self, tool_name: str, level: PermissionLevel) -> AuthDecision:
        if self.permission_mode == PermissionMode.DANGER_FULL_ACCESS:
            return AuthDecision(allowed=True)
        if level == PermissionLevel.DANGEROUS:
            return AuthDecision(
                allowed=False,
                reason=f"Tool {tool_name!r} requires danger-full-access mode",
            )
        if self.permission_mode == PermissionMode.READ_ONLY and level != PermissionLevel.SAFE:
            return AuthDecision(
                allowed=False,
                reason=f"Tool {tool_name!r} is not allowed in read-only mode",
            )
        return AuthDecision(allowed=True)

    def authorize_path(self, path: str, *, write: bool = False) -> AuthDecision:
        resolved = Path(path).resolve()
        if self.permission_mode == PermissionMode.DANGER_FULL_ACCESS:
            return AuthDecision(allowed=True)
        if write and not resolved.is_relative_to(self.workspace_root):
            return AuthDecision(
                allowed=False,
                reason=f"Path {path!r} is outside the allowed workspace",
            )
        return AuthDecision(allowed=True)
