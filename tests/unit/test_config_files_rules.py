"""Regression tests for load_rules_config against empty / malformed YAML."""

from pathlib import Path

from prax.core.config_files import load_rules_config


def test_empty_local_rules_yaml_does_not_return_none(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "rules.yaml").write_text("")
    assert load_rules_config(str(tmp_path)) == {}


def test_comment_only_local_rules_yaml_returns_empty_dict(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "rules.yaml").write_text("# only a comment\n")
    assert load_rules_config(str(tmp_path)) == {}


def test_populated_local_rules_yaml_round_trips(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "rules.yaml").write_text("rules: []\ntier_models: {}\n")
    cfg = load_rules_config(str(tmp_path))
    assert cfg == {"rules": [], "tier_models": {}}
