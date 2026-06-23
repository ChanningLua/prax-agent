"""Unit tests for prax/core/subprocess_env.py — child process env hygiene."""
from __future__ import annotations

from prax.core.subprocess_env import child_env, keep_proxy


class TestChildEnv:
    def test_strips_proxy_by_default(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://121.4.45.119:31038")
        monkeypatch.setenv("https_proxy", "http://121.4.45.119:31038")
        monkeypatch.setenv("ALL_PROXY", "socks5://x")
        monkeypatch.delenv("PRAX_KEEP_PROXY", raising=False)
        env = child_env()
        for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "HTTP_PROXY", "http_proxy", "all_proxy"):
            assert k not in env
        # forces direct everywhere
        assert env.get("NO_PROXY") == "*"
        assert env.get("no_proxy") == "*"

    def test_keep_proxy_escape_hatch(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        monkeypatch.setenv("PRAX_KEEP_PROXY", "1")
        env = child_env()
        assert env.get("HTTPS_PROXY") == "http://proxy:8080"
        assert "NO_PROXY" not in env or env.get("NO_PROXY") != "*"
        assert keep_proxy() is True

    def test_extra_pop_removes_keys(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("PRAX_KEEP_PROXY", raising=False)
        env = child_env(extra_pop=("CLAUDECODE",))
        assert "CLAUDECODE" not in env

    def test_passes_through_non_proxy_env(self, monkeypatch):
        monkeypatch.setenv("PATH", "/custom/bin")
        monkeypatch.delenv("PRAX_KEEP_PROXY", raising=False)
        env = child_env()
        assert env.get("PATH") == "/custom/bin"  # normal env survives
