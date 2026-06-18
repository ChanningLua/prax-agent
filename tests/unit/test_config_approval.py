"""Unit tests for config_files.load_approval_config (approval relay config)."""
from __future__ import annotations

from pathlib import Path

from prax.core.config_files import load_approval_config


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TestLoadApprovalConfig:
    def test_none_when_no_config(self, tmp_path):
        assert load_approval_config(str(tmp_path)) is None

    def test_full_block_returns_dict(self, tmp_path):
        _write(
            tmp_path / ".prax" / "config.yaml",
            "approval:\n"
            "  relay_url: http://129.211.6.55/prax-approval\n"
            "  admin_token: SECRET\n"
            "  deny_patterns: ['生产', 'prod']\n"
            "  notify_channel: wx\n",
        )
        cfg = load_approval_config(str(tmp_path))
        assert cfg == {
            "relay_url": "http://129.211.6.55/prax-approval",
            "admin_token": "SECRET",
            "deny_patterns": ["生产", "prod"],
            "notify_channel": "wx",
        }

    def test_none_when_admin_token_missing(self, tmp_path):
        _write(
            tmp_path / ".prax" / "config.yaml",
            "approval:\n  relay_url: http://relay\n",
        )
        assert load_approval_config(str(tmp_path)) is None

    def test_none_when_relay_url_missing(self, tmp_path):
        _write(
            tmp_path / ".prax" / "config.yaml",
            "approval:\n  admin_token: SECRET\n",
        )
        assert load_approval_config(str(tmp_path)) is None

    def test_optional_fields_default_none(self, tmp_path):
        _write(
            tmp_path / ".prax" / "config.yaml",
            "approval:\n  relay_url: http://relay\n  admin_token: T\n",
        )
        cfg = load_approval_config(str(tmp_path))
        assert cfg["deny_patterns"] is None and cfg["notify_channel"] is None

    def test_no_approval_block_returns_none(self, tmp_path):
        _write(tmp_path / ".prax" / "config.yaml", "permission_mode: dangerous\n")
        assert load_approval_config(str(tmp_path)) is None
