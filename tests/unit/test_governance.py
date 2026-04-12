"""Unit tests for prax/core/governance.py."""
from __future__ import annotations

import pytest

from prax.core import governance as gov_module
from prax.core.governance import GovernanceConfig


@pytest.fixture(autouse=True)
def clear_gov_cache():
    """Prevent cross-test cache contamination."""
    gov_module._gov_cache.clear()
    yield
    gov_module._gov_cache.clear()


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

def test_defaults():
    cfg = GovernanceConfig()
    assert cfg.max_budget_tokens is None
    assert cfg.max_iterations == 25
    assert cfg.max_tool_calls_per_tool is None
    assert cfg.risk_threshold == 15
    assert cfg.permission_mode == "workspace_write"
    assert cfg.require_approval_above_risk is None
    assert cfg.max_llm_calls_per_minute is None
    assert cfg.config_version == 0
    assert cfg.extra == {}


# ---------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------

def test_from_dict_known_keys():
    cfg = GovernanceConfig.from_dict({
        "max_iterations": 10,
        "risk_threshold": 12,
        "permission_mode": "ask",
    })
    assert cfg.max_iterations == 10
    assert cfg.risk_threshold == 12
    assert cfg.permission_mode == "ask"
    assert cfg.extra == {}


def test_from_dict_extra_keys():
    cfg = GovernanceConfig.from_dict({
        "max_iterations": 5,
        "custom_flag": True,
        "team_name": "alpha",
    })
    assert cfg.max_iterations == 5
    assert cfg.extra == {"custom_flag": True, "team_name": "alpha"}


def test_from_dict_empty():
    cfg = GovernanceConfig.from_dict({})
    assert cfg.max_iterations == 25
    assert cfg.extra == {}


def test_from_dict_all_known_fields():
    cfg = GovernanceConfig.from_dict({
        "max_budget_tokens": 50000,
        "max_iterations": 30,
        "max_tool_calls_per_tool": 5,
        "risk_threshold": 10,
        "permission_mode": "readonly",
        "require_approval_above_risk": 12,
        "max_llm_calls_per_minute": 20,
        "config_version": 2,
    })
    assert cfg.max_budget_tokens == 50000
    assert cfg.max_iterations == 30
    assert cfg.max_tool_calls_per_tool == 5
    assert cfg.risk_threshold == 10
    assert cfg.permission_mode == "readonly"
    assert cfg.require_approval_above_risk == 12
    assert cfg.max_llm_calls_per_minute == 20
    assert cfg.config_version == 2
    assert cfg.extra == {}


def test_from_dict_only_extra_keys():
    cfg = GovernanceConfig.from_dict({"foo": "bar", "baz": 42})
    assert cfg.extra == {"foo": "bar", "baz": 42}
    assert cfg.max_iterations == 25  # default preserved


# ---------------------------------------------------------------------------
# from_yaml
# ---------------------------------------------------------------------------

def test_from_yaml(tmp_path):
    yaml_file = tmp_path / "governance.yaml"
    yaml_file.write_text(
        "max_iterations: 7\nrisk_threshold: 9\npermission_mode: ask\n",
        encoding="utf-8",
    )
    cfg = GovernanceConfig.from_yaml(str(yaml_file))
    assert cfg.max_iterations == 7
    assert cfg.risk_threshold == 9
    assert cfg.permission_mode == "ask"


def test_from_yaml_empty_file(tmp_path):
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("", encoding="utf-8")
    cfg = GovernanceConfig.from_yaml(str(yaml_file))
    assert cfg.max_iterations == 25  # defaults


def test_from_yaml_with_extra_keys(tmp_path):
    yaml_file = tmp_path / "gov.yaml"
    yaml_file.write_text("max_iterations: 3\nmy_custom: hello\n", encoding="utf-8")
    cfg = GovernanceConfig.from_yaml(str(yaml_file))
    assert cfg.max_iterations == 3
    assert cfg.extra == {"my_custom": "hello"}


# ---------------------------------------------------------------------------
# from_file_with_reload — cache hit
# ---------------------------------------------------------------------------

def test_from_file_with_reload_cache_hit(tmp_path):
    yaml_file = tmp_path / "gov.yaml"
    yaml_file.write_text("max_iterations: 4\n", encoding="utf-8")
    path_str = str(yaml_file)

    cfg1 = GovernanceConfig.from_file_with_reload(path_str)
    cfg2 = GovernanceConfig.from_file_with_reload(path_str)

    assert cfg1 is cfg2  # same object returned from cache
    assert cfg1.max_iterations == 4


# ---------------------------------------------------------------------------
# from_file_with_reload — cache invalidation
# ---------------------------------------------------------------------------

def test_from_file_with_reload_cache_invalidation(tmp_path):
    yaml_file = tmp_path / "gov.yaml"
    yaml_file.write_text("max_iterations: 4\n", encoding="utf-8")
    path_str = str(yaml_file)

    cfg1 = GovernanceConfig.from_file_with_reload(path_str)
    assert cfg1.max_iterations == 4

    # Overwrite with new content and bump mtime
    import time
    time.sleep(0.01)
    yaml_file.write_text("max_iterations: 99\n", encoding="utf-8")
    # Force a different mtime by touching with a future timestamp
    new_mtime = yaml_file.stat().st_mtime + 1.0
    import os
    os.utime(str(yaml_file), (new_mtime, new_mtime))

    cfg2 = GovernanceConfig.from_file_with_reload(path_str)
    assert cfg2.max_iterations == 99
    assert cfg1 is not cfg2


# ---------------------------------------------------------------------------
# from_file_with_reload — missing file → default
# ---------------------------------------------------------------------------

def test_from_file_with_reload_missing_file(tmp_path):
    missing = str(tmp_path / "nonexistent.yaml")
    cfg = GovernanceConfig.from_file_with_reload(missing)
    assert isinstance(cfg, GovernanceConfig)
    assert cfg.max_iterations == 25  # defaults
