"""Unit tests for prax.integrations.wechat_ilink.store."""

from __future__ import annotations

import json
from pathlib import Path

from prax.integrations.wechat_ilink.store import (
    AccountRecord,
    delete_account,
    list_accounts,
    load_account,
    save_account,
    wechat_dir,
)


def test_wechat_dir_is_created(tmp_path):
    d = wechat_dir(tmp_path)
    assert d.exists()
    assert d == tmp_path / "wechat"


def test_save_account_roundtrip(tmp_path):
    save_account(
        account_id="ilink_abc",
        token="bot-token-xyz",
        base_url="https://ilinkai.weixin.qq.com",
        user_id="user_123",
        prax_home=tmp_path,
    )
    loaded = load_account("ilink_abc", prax_home=tmp_path)
    assert loaded is not None
    assert loaded.account_id == "ilink_abc"
    assert loaded.token == "bot-token-xyz"
    assert loaded.base_url == "https://ilinkai.weixin.qq.com"
    assert loaded.user_id == "user_123"
    assert loaded.saved_at  # ISO 8601 timestamp


def test_load_account_missing_returns_none(tmp_path):
    assert load_account("nope", prax_home=tmp_path) is None


def test_load_account_corrupt_json_returns_none(tmp_path):
    target = wechat_dir(tmp_path) / "ilink_corrupt.json"
    target.write_text("{ not valid json", encoding="utf-8")
    assert load_account("ilink_corrupt", prax_home=tmp_path) is None


def test_load_account_tolerates_unknown_fields(tmp_path):
    """Forward-compat: future schema additions must not crash old loaders."""
    target = wechat_dir(tmp_path) / "ilink_future.json"
    target.write_text(
        json.dumps({
            "account_id": "ilink_future",
            "token": "t",
            "base_url": "u",
            "user_id": "uid",
            "saved_at": "2026-04-25T00:00:00Z",
            "future_field_we_dont_know_about": "shrug",
        }),
        encoding="utf-8",
    )
    loaded = load_account("ilink_future", prax_home=tmp_path)
    assert loaded is not None
    assert loaded.account_id == "ilink_future"


def test_list_accounts_empty(tmp_path):
    assert list_accounts(prax_home=tmp_path) == []


def test_list_accounts_returns_all_records(tmp_path):
    for i in range(3):
        save_account(
            account_id=f"ilink_{i}",
            token=f"tok{i}",
            base_url="https://x",
            user_id=f"u{i}",
            prax_home=tmp_path,
        )
    accounts = list_accounts(prax_home=tmp_path)
    assert {a.account_id for a in accounts} == {"ilink_0", "ilink_1", "ilink_2"}


def test_list_accounts_skips_context_token_caches(tmp_path):
    save_account(
        account_id="ilink_real",
        token="t",
        base_url="u",
        prax_home=tmp_path,
    )
    # Hermes drops context-token caches as ``<account>.context-tokens.json``
    # in the same directory; we shouldn't pick those up as accounts.
    (wechat_dir(tmp_path) / "ilink_real.context-tokens.json").write_text("{}")
    accounts = list_accounts(prax_home=tmp_path)
    assert len(accounts) == 1
    assert accounts[0].account_id == "ilink_real"


def test_delete_account_removes_file(tmp_path):
    save_account(
        account_id="ilink_x",
        token="t",
        base_url="u",
        prax_home=tmp_path,
    )
    assert delete_account("ilink_x", prax_home=tmp_path) is True
    assert load_account("ilink_x", prax_home=tmp_path) is None


def test_delete_account_missing_returns_false(tmp_path):
    assert delete_account("nope", prax_home=tmp_path) is False


def test_save_account_sets_owner_only_mode_on_posix(tmp_path):
    save_account(
        account_id="ilink_perm",
        token="t",
        base_url="u",
        prax_home=tmp_path,
    )
    target = wechat_dir(tmp_path) / "ilink_perm.json"
    if hasattr(target, "stat"):
        import os
        if os.name == "posix":
            mode = target.stat().st_mode & 0o777
            assert mode == 0o600
