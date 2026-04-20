"""Unit tests for prax/core/config_files.py."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# load_models_config
# ---------------------------------------------------------------------------

class TestLoadModelsConfig:
    def test_no_files_returns_default(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_models_config
        result = load_models_config(str(tmp_path))

        assert result == {"default_model": "gpt-4.1", "providers": {}}

    def test_global_config_only(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()
        _write_yaml(fake_config_dir / "models.yaml", {"default_model": "gpt-4o", "providers": {}})

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_models_config
        result = load_models_config(str(tmp_path))

        assert result["default_model"] == "gpt-4o"

    def test_user_config_merges_over_global(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()
        _write_yaml(fake_config_dir / "models.yaml", {"default_model": "gpt-4o", "providers": {}})
        _write_yaml(fake_home / ".prax" / "models.yaml", {"default_model": "claude-3-5-sonnet", "providers": {}})

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_models_config
        result = load_models_config(str(tmp_path))

        assert result["default_model"] == "claude-3-5-sonnet"

    def test_local_config_merges_over_user(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()
        _write_yaml(fake_config_dir / "models.yaml", {"default_model": "gpt-4o", "providers": {}})
        _write_yaml(fake_home / ".prax" / "models.yaml", {"default_model": "claude-3-5-sonnet", "providers": {}})
        _write_yaml(tmp_path / ".prax" / "models.yaml", {"default_model": "local-model", "providers": {}})

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_models_config
        result = load_models_config(str(tmp_path))

        assert result["default_model"] == "local-model"


# ---------------------------------------------------------------------------
# load_rules_config
# ---------------------------------------------------------------------------

class TestLoadRulesConfig:
    def test_local_rules_returned(self, tmp_path, monkeypatch):
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()
        _write_yaml(tmp_path / ".prax" / "rules.yaml", {"rules": ["no-debug"], "tier_models": {}})

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)

        from prax.core.config_files import load_rules_config
        result = load_rules_config(str(tmp_path))

        assert result["rules"] == ["no-debug"]

    def test_global_rules_fallback(self, tmp_path, monkeypatch):
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()
        _write_yaml(fake_config_dir / "rules.yaml", {"rules": ["global-rule"], "tier_models": {}})

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)

        from prax.core.config_files import load_rules_config
        result = load_rules_config(str(tmp_path))

        assert result["rules"] == ["global-rule"]

    def test_neither_returns_default(self, tmp_path, monkeypatch):
        fake_config_dir = tmp_path / "config"
        fake_config_dir.mkdir()

        monkeypatch.setattr("prax.core.config_files.CONFIG_DIR", fake_config_dir)

        from prax.core.config_files import load_rules_config
        result = load_rules_config(str(tmp_path))

        assert result == {"rules": [], "tier_models": {}}


# ---------------------------------------------------------------------------
# load_mcp_config
# ---------------------------------------------------------------------------

class TestLoadMcpConfig:
    def test_no_config_returns_empty(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_mcp_config
        assert load_mcp_config(str(tmp_path)) == []

    def test_user_global_config_is_loaded(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        servers = [{"name": "global-fs", "command": "mcp-fs"}]
        _write_yaml(fake_home / ".prax" / "config.yaml", {"mcp_servers": servers})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_mcp_config
        result = load_mcp_config(str(tmp_path))

        assert result == servers

    def test_config_with_mcp_servers(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        servers = [{"name": "fs", "command": "mcp-fs"}]
        _write_yaml(tmp_path / ".prax" / "config.yaml", {"mcp_servers": servers})

        from prax.core.config_files import load_mcp_config
        result = load_mcp_config(str(tmp_path))

        assert result == servers

    def test_local_mcp_servers_override_user_global_by_name(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _write_yaml(
            fake_home / ".prax" / "config.yaml",
            {
                "mcp_servers": [
                    {"name": "shared", "command": "global-shared"},
                    {"name": "global-only", "command": "global-only-cmd"},
                ]
            },
        )
        _write_yaml(
            tmp_path / ".prax" / "config.yaml",
            {
                "mcp_servers": [
                    {"name": "shared", "command": "local-shared"},
                    {"name": "local-only", "command": "local-only-cmd"},
                ]
            },
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_mcp_config
        result = load_mcp_config(str(tmp_path))

        assert result == [
            {"name": "shared", "command": "local-shared"},
            {"name": "global-only", "command": "global-only-cmd"},
            {"name": "local-only", "command": "local-only-cmd"},
        ]

    def test_config_without_mcp_servers_key(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        _write_yaml(tmp_path / ".prax" / "config.yaml", {"model": "claude-opus-4"})

        from prax.core.config_files import load_mcp_config
        result = load_mcp_config(str(tmp_path))

        assert result == []

    def test_invalid_yaml_returns_empty(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        config_path = tmp_path / ".prax" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("key: [unclosed", encoding="utf-8")

        from prax.core.config_files import load_mcp_config
        result = load_mcp_config(str(tmp_path))

        assert result == []


# ---------------------------------------------------------------------------
# load_memory_config
# ---------------------------------------------------------------------------

class TestLoadMemoryConfig:
    def test_defaults_when_no_files(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_memory_config
        result = load_memory_config(str(tmp_path))

        assert result["memory"]["backend"] == "local"
        assert result["memory"]["local"]["max_facts"] == 100

    def test_user_override(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        _write_yaml(fake_home / ".prax" / "config.yaml", {"memory": {"backend": "openviking"}})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        from prax.core.config_files import load_memory_config
        result = load_memory_config(str(tmp_path))

        assert result["memory"]["backend"] == "openviking"

    def test_local_override(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        _write_yaml(tmp_path / ".prax" / "config.yaml", {"memory": {"backend": "sqlite"}})

        from prax.core.config_files import load_memory_config
        result = load_memory_config(str(tmp_path))

        assert result["memory"]["backend"] == "sqlite"

    def test_local_overrides_user(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        _write_yaml(fake_home / ".prax" / "config.yaml", {"memory": {"backend": "openviking"}})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        _write_yaml(tmp_path / ".prax" / "config.yaml", {"memory": {"backend": "local"}})

        from prax.core.config_files import load_memory_config
        result = load_memory_config(str(tmp_path))

        assert result["memory"]["backend"] == "local"


# ---------------------------------------------------------------------------
# load_governance_config
# ---------------------------------------------------------------------------

class TestLoadGovernanceConfig:
    def test_no_file_returns_none(self, tmp_path):
        from prax.core.config_files import load_governance_config
        assert load_governance_config(str(tmp_path)) is None

    def test_valid_file_returns_config(self, tmp_path):
        _write_yaml(tmp_path / ".prax" / "governance.yaml", {"max_iterations": 10, "risk_threshold": 5})

        from prax.core.config_files import load_governance_config
        result = load_governance_config(str(tmp_path))

        assert result is not None
        assert result.max_iterations == 10
        assert result.risk_threshold == 5

    def test_invalid_yaml_returns_none(self, tmp_path):
        gov_path = tmp_path / ".prax" / "governance.yaml"
        gov_path.parent.mkdir(parents=True, exist_ok=True)
        gov_path.write_text("key: [unclosed", encoding="utf-8")

        from prax.core.config_files import load_governance_config
        assert load_governance_config(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# load_agent_spec
# ---------------------------------------------------------------------------

class TestLoadAgentSpec:
    def test_no_file_returns_none(self, tmp_path):
        from prax.core.config_files import load_agent_spec
        assert load_agent_spec("myagent", str(tmp_path)) is None

    def test_valid_file_returns_spec(self, tmp_path):
        _write_yaml(
            tmp_path / ".prax" / "agents" / "myagent.yaml",
            {"name": "myagent", "description": "A test agent", "model": "claude-opus-4"},
        )

        from prax.core.config_files import load_agent_spec
        result = load_agent_spec("myagent", str(tmp_path))

        assert result is not None
        assert result.name == "myagent"
        assert result.model == "claude-opus-4"


# ---------------------------------------------------------------------------
# list_agent_specs
# ---------------------------------------------------------------------------

class TestListAgentSpecs:
    def test_no_dir_returns_empty(self, tmp_path):
        from prax.core.config_files import list_agent_specs
        assert list_agent_specs(str(tmp_path)) == []

    def test_dir_with_yaml_files(self, tmp_path):
        agents_dir = tmp_path / ".prax" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "alpha.yaml").write_text("name: alpha", encoding="utf-8")
        (agents_dir / "beta.yaml").write_text("name: beta", encoding="utf-8")

        from prax.core.config_files import list_agent_specs
        result = list_agent_specs(str(tmp_path))

        assert sorted(result) == ["alpha", "beta"]
