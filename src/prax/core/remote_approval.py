"""Park-and-continue remote approval — never blocks the loop.

When the agent hits a step needing human sign-off (per the operator's
"allow-all except prod" policy → a prod-touching deny-list), it PARKS the
request (:class:`ApprovalGate` + token), pushes a notification carrying
approve/reject links, and KEEPS RUNNING. Each later cycle reads the decision via
the same call: it returns ``"pending"`` until you tap a link (resolved by the
``approval_server`` endpoint), then ``"approved"`` / ``"rejected"``. The agent
proceeds only on ``approved`` and continues other work on ``pending`` — so an
unattended overnight run is never stuck on a yes/no.
"""
from __future__ import annotations

from typing import Callable

from .approval_gate import ApprovalGate, PENDING

Notifier = Callable[[str, str], None]  # (title, body) -> None


def approval_links(base_url: str, approval_id: str, token: str) -> "tuple[str, str]":
    base = base_url.rstrip("/")
    return (
        f"{base}/a/{approval_id}?t={token}&d=approve",
        f"{base}/a/{approval_id}?t={token}&d=reject",
    )


def request_remote_approval(
    gate: ApprovalGate,
    approval_id: str,
    instruction: str,
    *,
    base_url: str,
    created_at: float,
    deadline_at: float | None = None,
    notify: Notifier | None = None,
):
    """Park a pending approval and (optionally) notify with tap-to-resolve links."""
    req = gate.request(
        approval_id, instruction, created_at=created_at, deadline_at=deadline_at
    )
    approve, reject = approval_links(base_url, approval_id, req.token)
    if notify is not None:
        notify(
            f"[审批] {approval_id}",
            f"需批准：{instruction}\n✅ 批准: {approve}\n❌ 拒绝: {reject}",
        )
    return req, approve, reject


def check_or_request(
    gate: ApprovalGate,
    approval_id: str,
    instruction: str,
    *,
    base_url: str,
    now: float,
    deadline_at: float | None = None,
    notify: Notifier | None = None,
) -> str:
    """Non-blocking gate check (the park-and-continue primitive).

    First call for *approval_id*: parks + notifies, returns ``PENDING``.
    Later calls: return the current status (``approved`` / ``rejected`` /
    ``pending`` / ``timed_out``). Proceed only on ``approved``; continue other
    work on ``pending`` rather than blocking.
    """
    if gate.get(approval_id) is None:
        request_remote_approval(
            gate,
            approval_id,
            instruction,
            base_url=base_url,
            created_at=now,
            deadline_at=deadline_at,
            notify=notify,
        )
        return PENDING
    return gate.status(approval_id, now=now)
