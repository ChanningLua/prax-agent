"""Unit tests for prax/core/remote_approval_client.

The HTTP call is faked by routing it straight through the server's ``dispatch``
against a local gate — exercising client + relay logic together, no sockets.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from prax.core.approval_gate import ApprovalGate
from prax.core.approval_server import dispatch, route_approval
from prax.core.remote_approval_client import remote_check_or_request


def _fake_http(gate, admin_token, now_box):
    def http(method, url, *, body=None, timeout=10):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        return dispatch(method, u.path, q, body, gate=gate, now=now_box[0], admin_token=admin_token)

    return http


class TestRemoteApprovalClient:
    def test_park_notify_then_approve_cycle(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        now = [100.0]
        http = _fake_http(gate, "ADMIN", now)
        notes: list = []

        # cycle 1: parks on the relay + notifies with tap links
        s1 = remote_check_or_request(
            "http://relay", "ADMIN", "d1", "deploy prod",
            notify=lambda t, b: notes.append(b), http=http,
        )
        assert s1 == "pending"
        assert gate.get("d1").status == "pending"
        assert len(notes) == 1 and "d=approve" in notes[0] and "d=reject" in notes[0]

        # phone taps the relay's approve link (resolve on the same gate)
        token = gate.get("d1").token
        st, _ = route_approval("/a/d1", {"t": token, "d": "approve"}, gate=gate, now=now[0])
        assert st == 200

        # cycle 2: agent polls the relay → approved, and does NOT re-notify
        s2 = remote_check_or_request(
            "http://relay", "ADMIN", "d1", "deploy prod",
            notify=lambda t, b: notes.append(b), http=http,
        )
        assert s2 == "approved"
        assert len(notes) == 1
