"""Unit tests for prax/core/approval_server.route_approval (pure routing logic)."""
from __future__ import annotations

import json

from prax.core.approval_gate import APPROVED, PENDING, REJECTED, ApprovalGate
from prax.core.approval_server import dispatch, route_approval


def _gate_with(tmp_path, approval_id="a1"):
    gate = ApprovalGate(str(tmp_path))
    req = gate.request(approval_id, "deploy v1.2", created_at=0.0)
    return gate, req


class TestRouteApproval:
    def test_approve_with_valid_token(self, tmp_path):
        gate, req = _gate_with(tmp_path)
        status, _ = route_approval("/a/a1", {"t": req.token, "d": "approve"}, gate=gate, now=5.0)
        assert status == 200
        assert gate.get("a1").status == APPROVED
        assert gate.get("a1").resolved_at == 5.0

    def test_reject_with_valid_token(self, tmp_path):
        gate, req = _gate_with(tmp_path, "a2")
        status, _ = route_approval("/a/a2", {"t": req.token, "d": "reject"}, gate=gate, now=1.0)
        assert status == 200
        assert gate.get("a2").status == REJECTED

    def test_wrong_token_403_stays_pending(self, tmp_path):
        gate, _ = _gate_with(tmp_path, "a3")
        status, _ = route_approval("/a/a3", {"t": "bad", "d": "approve"}, gate=gate, now=1.0)
        assert status == 403
        assert gate.get("a3").status == PENDING

    def test_unknown_id_404(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        status, _ = route_approval("/a/ghost", {"t": "x", "d": "approve"}, gate=gate, now=1.0)
        assert status == 404

    def test_bad_decision_400(self, tmp_path):
        gate, req = _gate_with(tmp_path, "a4")
        status, _ = route_approval("/a/a4", {"t": req.token, "d": "maybe"}, gate=gate, now=1.0)
        assert status == 400

    def test_bad_path_404(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        status, _ = route_approval("/nope", {"d": "approve"}, gate=gate, now=1.0)
        assert status == 404


class TestDispatch:
    def test_post_request_creates_pending(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        st, body = dispatch(
            "POST", "/request", {}, {"id": "r1", "instruction": "deploy", "k": "A"},
            gate=gate, now=5.0, admin_token="A",
        )
        assert st == 200
        token = json.loads(body)["token"]
        assert gate.get("r1").status == PENDING and gate.get("r1").token == token

    def test_status_endpoint(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        gate.request("r2", "x", created_at=0.0)
        st, body = dispatch("GET", "/status/r2", {"k": "A"}, None, gate=gate, now=1.0, admin_token="A")
        assert st == 200 and json.loads(body)["status"] == PENDING

    def test_admin_token_required_for_agent_endpoints(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        st, _ = dispatch(
            "POST", "/request", {}, {"id": "r3", "instruction": "x", "k": "WRONG"},
            gate=gate, now=1.0, admin_token="A",
        )
        assert st == 401

    def test_tap_does_not_need_admin_token(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        req = gate.request("r4", "x", created_at=0.0)
        st, _ = dispatch(
            "GET", "/a/r4", {"t": req.token, "d": "approve"}, None,
            gate=gate, now=1.0, admin_token="A",
        )
        assert st == 200 and gate.get("r4").status == APPROVED

    def test_open_when_no_admin_token_configured(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        st, _ = dispatch(
            "POST", "/request", {}, {"id": "r5", "instruction": "x"},
            gate=gate, now=1.0, admin_token="",
        )
        assert st == 200
