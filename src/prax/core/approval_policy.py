"""Production-approval policy + gate builder for the orchestrator.

Keeps the "what counts as production" decision OUT of the loop (which stays a
pure mechanism). A goal that matches the deny-list is production-affecting and
must clear the remote approval relay before the loop dispatches any work; every
other goal is auto-approved (allow-all-except-production).
"""
from __future__ import annotations

from typing import Callable

from .remote_approval_client import remote_check_or_request

# A goal mentioning any of these (case-insensitive substring) is treated as
# production-affecting. Operators override via .prax/config.yaml → approval.deny_patterns.
DEFAULT_DENY_PATTERNS = [
    "生产",
    "线上",
    "production",
    "prod deploy",
    "prod-deploy",
    "deploy to prod",
    "生产库",
    "生产数据库",
    "prod db",
]


def needs_approval(goal: str, deny_patterns) -> bool:
    """True when the goal text matches any deny pattern (case-insensitive)."""
    if not goal:
        return False
    text = goal.lower()
    return any(p.lower() in text for p in (deny_patterns or []) if p)


def build_relay_gate(
    *,
    relay_url: str,
    admin_token: str,
    approval_id: str,
    deny_patterns=None,
    notify: Callable[[str, str], None] | None = None,
    check: Callable[..., str] = remote_check_or_request,
) -> Callable[[str], str]:
    """Build the ApprovalGate the loop consults.

    Non-prod goals → ``"approved"`` immediately (no human in the loop). Prod
    goals → the remote relay via ``check`` (park-and-continue: first call parks
    + notifies, later calls poll). ``check`` is injectable so this unit-tests
    without HTTP. ``approval_id`` should be stable per run (the run id) so a
    resumed run re-checks the SAME approval rather than parking a new one.
    """
    patterns = DEFAULT_DENY_PATTERNS if deny_patterns is None else deny_patterns

    def gate(goal: str) -> str:
        if not needs_approval(goal, patterns):
            return "approved"
        return check(relay_url, admin_token, approval_id, goal, notify=notify)

    return gate
