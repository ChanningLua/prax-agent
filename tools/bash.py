"""Bash tool — thin wrapper around SandboxBashTool for backwards compat."""

from __future__ import annotations

from .sandbox_bash import SandboxBashTool
from .base import PermissionLevel


class BashTool(SandboxBashTool):
    """Alias for SandboxBashTool with DANGEROUS permission level."""

    permission_level = PermissionLevel.DANGEROUS

    def __init__(self, cwd: str | None = None):
        super().__init__(cwd=cwd or ".")
