"""Unit tests for prax/core/approval_policy (pure policy + gate builder)."""
from __future__ import annotations

from prax.core.approval_policy import (
    DEFAULT_DENY_PATTERNS,
    build_relay_gate,
    needs_approval,
)


class TestNeedsApproval:
    def test_matches_default_prod_keyword(self):
        assert needs_approval("部署到生产服务器", DEFAULT_DENY_PATTERNS) is True

    def test_non_prod_goal_is_free(self):
        assert needs_approval("写一个登录页并跑测试", DEFAULT_DENY_PATTERNS) is False

    def test_case_insensitive(self):
        assert needs_approval("Deploy to PRODUCTION now", ["production"]) is True

    def test_empty_goal_or_patterns(self):
        assert needs_approval("", DEFAULT_DENY_PATTERNS) is False
        assert needs_approval("生产", []) is False
        assert needs_approval("生产", None) is False


class TestBuildRelayGate:
    def test_non_prod_auto_approved_without_consulting_relay(self):
        calls = []
        gate = build_relay_gate(
            relay_url="u", admin_token="t", approval_id="r1",
            check=lambda *a, **k: calls.append(a) or "pending",
        )
        assert gate("普通任务，跑测试") == "approved"
        assert calls == []  # relay never consulted for non-prod work

    def test_prod_goal_routes_to_relay_with_expected_args(self):
        seen = {}

        def fake_check(url, token, aid, instruction, *, notify=None, **k):
            seen.update(url=url, token=token, aid=aid, instruction=instruction)
            return "pending"

        gate = build_relay_gate(
            relay_url="http://relay", admin_token="ADM", approval_id="run-9",
            check=fake_check,
        )
        assert gate("部署到生产") == "pending"
        assert seen == {
            "url": "http://relay", "token": "ADM",
            "aid": "run-9", "instruction": "部署到生产",
        }

    def test_prod_goal_approved_passthrough(self):
        gate = build_relay_gate(
            relay_url="u", admin_token="t", approval_id="r",
            check=lambda *a, **k: "approved",
        )
        assert gate("发布到生产") == "approved"

    def test_custom_deny_patterns_replace_defaults(self):
        gate = build_relay_gate(
            relay_url="u", admin_token="t", approval_id="r",
            deny_patterns=["danger-zone"],
            check=lambda *a, **k: "pending",
        )
        assert gate("touch danger-zone") == "pending"
        # default "生产" pattern is NOT used once deny_patterns is overridden
        assert gate("部署到生产") == "approved"

    def test_notify_is_forwarded_to_check(self):
        got = {}

        def fake_check(url, token, aid, instruction, *, notify=None, **k):
            got["notify"] = notify
            return "pending"

        sentinel = lambda t, b: None
        gate = build_relay_gate(
            relay_url="u", admin_token="t", approval_id="r",
            notify=sentinel, check=fake_check,
        )
        gate("部署到生产")
        assert got["notify"] is sentinel
