"""Unit tests for prax.core.model_router."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from prax.core.model_router import (
    DEFAULT_FALLBACK_CHAIN,
    DEFAULT_ROUTING_RULES,
    ModelRouter,
    _router_cache,
    _cache_lock,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_router_cache():
    """Clear the module-level mtime cache before each test."""
    with _cache_lock:
        _router_cache.clear()
    yield
    with _cache_lock:
        _router_cache.clear()


# ---------------------------------------------------------------------------
# 1. Default routing rules
# ---------------------------------------------------------------------------

def test_default_routing_rules_present():
    router = ModelRouter()
    assert router._routing_rules["default"] == "claude-opus-4-6"
    assert router._routing_rules["chinese_content"] == "glm-4-flash"
    assert router._routing_rules["debugging"] == "gpt-4.1"
    assert router._routing_rules["architecture"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# 2. classify_task — chinese keywords
# ---------------------------------------------------------------------------

def test_classify_task_chinese_content():
    router = ModelRouter()
    assert router.classify_task("请用中文写一篇文章") == "chinese_content"
    assert router.classify_task("这是一个中国项目") == "chinese_content"


# ---------------------------------------------------------------------------
# 3. classify_task — debug keywords
# ---------------------------------------------------------------------------

def test_classify_task_debug_keywords():
    router = ModelRouter()
    assert router.classify_task("debug this function") == "debugging"
    assert router.classify_task("fix bug in login") == "debugging"
    assert router.classify_task("why is this not working?") == "debugging"


# ---------------------------------------------------------------------------
# 4. classify_task — architecture keywords
# ---------------------------------------------------------------------------

def test_classify_task_architecture_keywords():
    router = ModelRouter()
    assert router.classify_task("architect a new microservice") == "architecture"
    assert router.classify_task("design the database schema") == "architecture"
    assert router.classify_task("refactor the module structure") == "architecture"


# ---------------------------------------------------------------------------
# 5. classify_task — unknown → "default"
# ---------------------------------------------------------------------------

def test_classify_task_unknown_returns_default():
    router = ModelRouter()
    assert router.classify_task("do the thing") == "default"
    assert router.classify_task("") == "default"
    assert router.classify_task("   ") == "default"


# ---------------------------------------------------------------------------
# 6. route — normal routing
# ---------------------------------------------------------------------------

def test_route_normal():
    router = ModelRouter()
    model = router.route("fix bug in auth flow")
    assert model == "gpt-4.1"


def test_route_default_task():
    router = ModelRouter()
    model = router.route("do the thing")
    assert model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# 7. route — force_model override
# ---------------------------------------------------------------------------

def test_route_force_model_override():
    router = ModelRouter()
    model = router.route("fix bug everywhere", context={"force_model": "glm-4-flash"})
    assert model == "glm-4-flash"


def test_route_force_model_overrides_classification():
    router = ModelRouter()
    # Even though this would classify as chinese_content, force_model wins
    model = router.route("用中文写代码", context={"force_model": "gpt-4.1"})
    assert model == "gpt-4.1"


# ---------------------------------------------------------------------------
# 8. get_fallback_chain — no starting model → full chain
# ---------------------------------------------------------------------------

def test_get_fallback_chain_no_starting_model():
    router = ModelRouter()
    chain = router.get_fallback_chain()
    assert chain == DEFAULT_FALLBACK_CHAIN
    # Should be a copy, not the same list
    assert chain is not router._fallback_chain


# ---------------------------------------------------------------------------
# 9. get_fallback_chain — starting model in chain → slice
# ---------------------------------------------------------------------------

def test_get_fallback_chain_starting_model_in_chain():
    router = ModelRouter()
    # DEFAULT_FALLBACK_CHAIN = ["claude-opus-4-6", "gpt-4.1", "glm-4-flash"]
    chain = router.get_fallback_chain("gpt-4.1")
    assert chain == ["gpt-4.1", "glm-4-flash"]


def test_get_fallback_chain_starting_model_first():
    router = ModelRouter()
    chain = router.get_fallback_chain("claude-opus-4-6")
    assert chain == DEFAULT_FALLBACK_CHAIN


# ---------------------------------------------------------------------------
# 10. get_fallback_chain — starting model not in chain → prepend
# ---------------------------------------------------------------------------

def test_get_fallback_chain_starting_model_not_in_chain():
    router = ModelRouter()
    chain = router.get_fallback_chain("custom-model")
    assert chain[0] == "custom-model"
    assert chain[1:] == DEFAULT_FALLBACK_CHAIN


# ---------------------------------------------------------------------------
# 11. is_chinese_task — True/False
# ---------------------------------------------------------------------------

def test_is_chinese_task_true():
    router = ModelRouter()
    assert router.is_chinese_task("这是中文内容") is True
    assert router.is_chinese_task("用简体字写") is True


def test_is_chinese_task_false():
    router = ModelRouter()
    assert router.is_chinese_task("Write a function in Python") is False
    assert router.is_chinese_task("debug the login") is False


# ---------------------------------------------------------------------------
# 12. _load_config — valid YAML with routing rules
# ---------------------------------------------------------------------------

def test_load_config_valid_yaml(tmp_path):
    config = {
        "routing": {
            "rules": {
                "default": "custom-model",
                "debugging": "custom-debug-model",
            },
            "fallback_chain": ["custom-model", "custom-debug-model"],
        }
    }
    config_file = tmp_path / "models.yaml"
    config_file.write_text(yaml.dump(config), encoding="utf-8")

    router = ModelRouter(str(config_file))
    assert router._routing_rules["default"] == "custom-model"
    assert router._routing_rules["debugging"] == "custom-debug-model"
    assert router._fallback_chain == ["custom-model", "custom-debug-model"]


# ---------------------------------------------------------------------------
# 13. _load_config — missing file → no-op
# ---------------------------------------------------------------------------

def test_load_config_missing_file(tmp_path):
    router = ModelRouter(str(tmp_path / "nonexistent.yaml"))
    # Should fall back to defaults silently
    assert router._routing_rules["default"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# 14. _load_config — invalid YAML → warning (no exception)
# ---------------------------------------------------------------------------

def test_load_config_invalid_yaml(tmp_path, caplog):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("key: [unclosed", encoding="utf-8")
    import logging
    with caplog.at_level(logging.WARNING, logger="prax.core.model_router"):
        router = ModelRouter(str(bad_file))
    # Should not raise, defaults intact
    assert router._routing_rules["default"] == "claude-opus-4-6"
    assert any("Failed to load" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 15. from_cwd — local config exists
# ---------------------------------------------------------------------------

def test_from_cwd_local_config_exists(tmp_path):
    prax_dir = tmp_path / ".prax"
    prax_dir.mkdir()
    config = {
        "routing": {
            "rules": {"default": "local-model"},
        }
    }
    (prax_dir / "models.yaml").write_text(yaml.dump(config), encoding="utf-8")

    router = ModelRouter.from_cwd(str(tmp_path))
    assert router._routing_rules["default"] == "local-model"


# ---------------------------------------------------------------------------
# 16. from_cwd — no config → default router
# ---------------------------------------------------------------------------

def test_from_cwd_no_config(tmp_path):
    # tmp_path has no .prax/models.yaml or package config
    with patch("prax.core.model_router.Path") as mock_path_cls:
        # Simulate that neither local nor pkg config exists
        mock_local = mock_path_cls.return_value.__truediv__.return_value.__truediv__.return_value
        mock_local.exists.return_value = False
        # Restore original ModelRouter constructor
        router = ModelRouter()
    assert router._routing_rules["default"] == "claude-opus-4-6"


def test_from_cwd_empty_directory(tmp_path):
    """No .prax directory exists; no package config at expected location."""
    # We can't guarantee the package config doesn't exist, so patch Path.exists
    original_from_cwd = ModelRouter.from_cwd.__func__

    with patch.object(Path, "exists", return_value=False):
        router = ModelRouter.from_cwd(str(tmp_path))
    assert isinstance(router, ModelRouter)
    assert router._routing_rules["default"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# 17. _from_config_cached — cache hit (same mtime)
# ---------------------------------------------------------------------------

def test_from_config_cached_cache_hit(tmp_path):
    config_file = tmp_path / "models.yaml"
    config_file.write_text("routing:\n  rules:\n    default: cached-model\n", encoding="utf-8")
    path_str = str(config_file)

    router1 = ModelRouter._from_config_cached(path_str)
    router2 = ModelRouter._from_config_cached(path_str)

    # Both calls should return the same object (cache hit)
    assert router1 is router2


# ---------------------------------------------------------------------------
# 18. _from_config_cached — cache miss (different mtime)
# ---------------------------------------------------------------------------

def test_from_config_cached_cache_miss_on_mtime_change(tmp_path):
    config_file = tmp_path / "models.yaml"
    config_file.write_text("routing:\n  rules:\n    default: model-v1\n", encoding="utf-8")
    path_str = str(config_file)

    router1 = ModelRouter._from_config_cached(path_str)

    # Manually corrupt the cached mtime to simulate a file change
    with _cache_lock:
        _router_cache[path_str] = (_router_cache[path_str][0] - 1.0, router1)

    router2 = ModelRouter._from_config_cached(path_str)

    # Should have built a fresh router (not the same object)
    assert router1 is not router2


# ---------------------------------------------------------------------------
# 19. _from_config_cached — OSError → fresh router
# ---------------------------------------------------------------------------

def test_from_config_cached_oserror_returns_fresh_router(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist.yaml")
    # stat() will raise FileNotFoundError (subclass of OSError)
    router = ModelRouter._from_config_cached(nonexistent)
    assert isinstance(router, ModelRouter)
    # Should use defaults
    assert router._routing_rules["default"] == "claude-opus-4-6"
