"""Unit tests for prax/core/remote_approval (park-and-continue)."""
from __future__ import annotations

from prax.core.approval_gate import APPROVED, ApprovalGate
from prax.core.remote_approval import check_or_request, request_remote_approval


def _recording_notifier():
    calls: list = []

    def notify(title, body):
        calls.append((title, body))

    notify.calls = calls  # type: ignore[attr-defined]
    return notify


class TestRemoteApproval:
    def test_request_builds_token_links_and_notifies(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        nf = _recording_notifier()
        req, approve, reject = request_remote_approval(
            gate, "d1", "部署到生产", base_url="http://h:7879/", created_at=0.0, notify=nf
        )
        assert approve == f"http://h:7879/a/d1?t={req.token}&d=approve"
        assert reject.endswith("&d=reject")
        assert len(nf.calls) == 1
        _, body = nf.calls[0]
        assert "部署到生产" in body
        assert req.token in body  # the link carries the secret token

    def test_first_call_parks_and_returns_pending(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        nf = _recording_notifier()
        status = check_or_request(gate, "d2", "ship", base_url="http://h", now=10.0, notify=nf)
        assert status == "pending"
        assert gate.get("d2").status == "pending"
        assert len(nf.calls) == 1

    def test_repeat_call_does_not_re_notify_or_block(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        nf = _recording_notifier()
        check_or_request(gate, "d3", "ship", base_url="http://h", now=1.0, notify=nf)
        status = check_or_request(gate, "d3", "ship", base_url="http://h", now=2.0, notify=nf)
        assert status == "pending"
        assert len(nf.calls) == 1  # parked/notified once, never re-spams

    def test_reads_approved_after_resolution(self, tmp_path):
        gate = ApprovalGate(str(tmp_path))
        nf = _recording_notifier()
        check_or_request(gate, "d4", "ship", base_url="http://h", now=1.0, notify=nf)
        gate.resolve("d4", APPROVED, resolved_at=5.0)
        assert (
            check_or_request(gate, "d4", "ship", base_url="http://h", now=6.0, notify=nf)
            == APPROVED
        )
