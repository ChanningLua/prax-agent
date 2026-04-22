"""Unit tests for ModelRouter mtime-based cache (prax/core/model_router.py)."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
import yaml

from prax.core.model_router import ModelRouter, _router_cache, _cache_lock


def _clear_cache():
    with _cache_lock:
        _router_cache.clear()


def _write_config(path: Path, rules: dict) -> None:
    path.write_text(yaml.dump({"routing": {"rules": rules}}), encoding="utf-8")


class TestModelRouterMtimeCache:
    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_same_file_returns_same_instance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / ".prax" / "models.yaml"
            cfg.parent.mkdir(parents=True)
            _write_config(cfg, {"default": "gpt-5.4"})

            r1 = ModelRouter.from_cwd(tmpdir)
            r2 = ModelRouter.from_cwd(tmpdir)
            assert r1 is r2

    def test_changed_file_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / ".prax" / "models.yaml"
            cfg.parent.mkdir(parents=True)
            _write_config(cfg, {"default": "gpt-5.4"})

            r1 = ModelRouter.from_cwd(tmpdir)

            # Ensure mtime changes (sleep briefly or force touch)
            time.sleep(0.05)
            _write_config(cfg, {"default": "glm-4-flash"})

            r2 = ModelRouter.from_cwd(tmpdir)
            assert r1 is not r2

    def test_no_config_returns_default_router(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = ModelRouter.from_cwd(tmpdir)
            # Should return a router with default rules (no crash)
            assert isinstance(router, ModelRouter)

    def test_cache_cleared_between_tests(self):
        # After teardown, cache should be empty
        with _cache_lock:
            assert len(_router_cache) == 0
