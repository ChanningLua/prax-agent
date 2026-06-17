"""Unit tests for prax/core/approval_gate.py.

Timestamps are injected (created_at / resolved_at / now) so timeout behaviour
is deterministic without sleeping or a real clock.
"""
from __future__ import annotations

import json

import pytest

from prax.core.approval_gate import (
    ApprovalGate,
    APPROVED,
    NEEDS_REVISION,
    PENDING,
    TIMED_OUT,
)


class TestApprovalGate:
    def test_request_writes_pending(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        req = gate.request("deploy_1", "部署 v1.2 到生产", created_at=100.0)

        assert req.status == PENDING
        assert req.instruction == "部署 v1.2 到生产"
        path = tmp_path / ".prax" / "approvals" / "deploy_1.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == PENDING
        assert data["schema_version"].startswith("prax.approval")

    def test_get_missing_returns_none(self, tmp_path):
        assert ApprovalGate(str(tmp_path)).get("nope") is None

    def test_resolve_approved_persists(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("a1", "do X", created_at=0.0)
        updated = gate.resolve("a1", APPROVED, resolved_at=5.0, note="ok 上线")

        assert updated.status == APPROVED
        assert updated.note == "ok 上线"
        assert updated.resolved_at == 5.0
        assert gate.get("a1").status == APPROVED

    def test_resolve_needs_revision_carries_note(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("a2", "do Y", created_at=0.0)
        updated = gate.resolve("a2", NEEDS_REVISION, resolved_at=1.0, note="间距改 8px")
        assert updated.status == NEEDS_REVISION
        assert updated.note == "间距改 8px"

    def test_resolve_rejects_invalid_decision(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("a3", "do Z", created_at=0.0)
        with pytest.raises(ValueError):
            gate.resolve("a3", "maybe", resolved_at=1.0)

    def test_resolve_missing_raises(self, tmp_path):
        with pytest.raises(KeyError):
            ApprovalGate(str(tmp_path)).resolve("ghost", APPROVED, resolved_at=1.0)

    def test_status_pending_then_resolved(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("a4", "do", created_at=0.0)
        assert gate.status("a4", now=1.0) == PENDING
        gate.resolve("a4", APPROVED, resolved_at=2.0)
        assert gate.status("a4", now=3.0) == APPROVED

    def test_status_times_out_past_deadline_when_still_pending(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("a5", "do", created_at=0.0, deadline_at=10.0)
        assert gate.status("a5", now=5.0) == PENDING      # before deadline
        assert gate.status("a5", now=11.0) == TIMED_OUT   # past deadline, unresolved

    def test_resolved_before_deadline_is_not_timed_out(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("a6", "do", created_at=0.0, deadline_at=10.0)
        gate.resolve("a6", APPROVED, resolved_at=5.0)
        assert gate.status("a6", now=99.0) == APPROVED    # decision sticks

    def test_status_missing_returns_none(self, tmp_path):
        assert ApprovalGate(str(tmp_path)).status("ghost", now=1.0) is None
