"""Approval gate — file-based human-in-the-loop for "draft-don't-send" steps.

A risky / ship step writes a pending request to ``.prax/approvals/<id>.json``
and notifies a human; the human (or a callback) resolves it via the
``prax approval`` CLI. The orchestrator polls :meth:`status` and only proceeds
on an explicit decision. State lives on disk so a crash / quota-stop between
"ask" and "answer" never loses the pending approval (research: never keep
approval state only in memory).

Decisions are three-state — ``approved`` / ``rejected`` / ``needs_revision`` —
so a reviewer can bounce work back with a note. ``timed_out`` is a *computed*
status (a deadline passed while still pending); it is never written to disk, so
a late human answer still resolves cleanly.

Timestamps are epoch seconds supplied by the caller (the orchestrator passes
``time.time()``), which keeps timeout logic deterministic and testable.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from .persistence import atomic_write_json

APPROVAL_SCHEMA_VERSION = "prax.approval.v1"

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
NEEDS_REVISION = "needs_revision"
TIMED_OUT = "timed_out"  # computed-only, never persisted

_DECISIONS = {APPROVED, REJECTED, NEEDS_REVISION}


@dataclass
class ApprovalRequest:
    id: str
    instruction: str
    status: str
    created_at: float
    deadline_at: float | None = None
    resolved_at: float | None = None
    note: str = ""
    token: str = ""  # unguessable secret for remote (phone-tap) resolution
    schema_version: str = APPROVAL_SCHEMA_VERSION


class ApprovalGate:
    """Persist approval requests to ``.prax/approvals/<id>.json``."""

    def __init__(self, cwd: str) -> None:
        self._dir = Path(cwd) / ".prax" / "approvals"

    def _path(self, approval_id: str) -> Path:
        return self._dir / f"{approval_id}.json"

    def request(
        self,
        approval_id: str,
        instruction: str,
        *,
        created_at: float,
        deadline_at: float | None = None,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            id=approval_id,
            instruction=instruction,
            status=PENDING,
            created_at=created_at,
            deadline_at=deadline_at,
            token=secrets.token_urlsafe(16),
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path(approval_id), asdict(req))
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        path = self._path(approval_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            allowed = {f.name for f in ApprovalRequest.__dataclass_fields__.values()}
            return ApprovalRequest(**{k: v for k, v in data.items() if k in allowed})
        except Exception:
            return None

    def resolve(
        self,
        approval_id: str,
        decision: str,
        *,
        resolved_at: float,
        note: str = "",
    ) -> ApprovalRequest:
        if decision not in _DECISIONS:
            raise ValueError(
                f"Invalid decision {decision!r}; must be one of {sorted(_DECISIONS)}"
            )
        req = self.get(approval_id)
        if req is None:
            raise KeyError(f"approval {approval_id!r} not found")
        req.status = decision
        req.resolved_at = resolved_at
        req.note = note
        atomic_write_json(self._path(approval_id), asdict(req))
        return req

    def status(self, approval_id: str, *, now: float) -> str | None:
        """Current status, or ``None`` if unknown. A still-pending request past
        its deadline reports ``timed_out`` without mutating the stored record."""
        req = self.get(approval_id)
        if req is None:
            return None
        if (
            req.status == PENDING
            and req.deadline_at is not None
            and now > req.deadline_at
        ):
            return TIMED_OUT
        return req.status

    def check_token(self, approval_id: str, token: str) -> bool:
        """Constant-time check that *token* matches the request's secret."""
        req = self.get(approval_id)
        if req is None or not req.token:
            return False
        return secrets.compare_digest(req.token, token or "")
