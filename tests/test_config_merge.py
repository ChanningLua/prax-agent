"""Tests for Batch 4 config merge and budget command."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from prax.core.config_merge import deep_merge, merge_providers, load_merged_models_config
from prax.core.config_files import load_models_config
from prax.commands.handlers import CommandContext, _handle_budget
from prax.commands.registry import parse_slash_command
from prax.core.permissions import PermissionMode
from prax.core.session_store import FileSessionStore


class TestDeepMerge:
    """Tests for deep_merge utility."""

    def test_simple_override(self):
        """Test simple key override."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        """Test nested dict merge."""
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_list_replacement(self):
        """Test that lists are replaced, not merged."""
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = deep_merge(base, override)
        assert result == {"items": [4, 5]}


class TestMergeProviders:
    """Tests for merge_providers utility."""

    def test_new_provider_added(self):
        """Test that new providers are added."""
        global_providers = {"openai": {"models": []}}
        local_providers = {"anthropic": {"models": []}}
        result = merge_providers(global_providers, local_providers)
        assert "openai" in result
        assert "anthropic" in result

    def test_model_field_override(self):
        """Test that model fields are overridden."""
        global_providers = {
            "openai": {
                "models": [{"name": "gpt-4", "max_tokens": 4096}]
            }
        }
        local_providers = {
            "openai": {
                "models": [{"name": "gpt-4", "max_tokens": 8192}]
            }
        }
        result = merge_providers(global_providers, local_providers)
        models = {m["name"]: m for m in result["openai"]["models"]}
        assert models["gpt-4"]["max_tokens"] == 8192


class TestLoadMergedModelsConfig:
    """Tests for load_merged_models_config."""

    def test_local_overrides_default_model(self):
        """Test that local config overrides default_model."""
        global_cfg = {"default_model": "gpt-4", "providers": {}}
        local_cfg = {"default_model": "claude-3"}
        result = load_merged_models_config(global_cfg, local_cfg)
        assert result["default_model"] == "claude-3"

    def test_new_provider_appended(self):
        """Test that new providers are appended."""
        global_cfg = {"providers": {"openai": {"models": []}}}
        local_cfg = {"providers": {"anthropic": {"models": []}}}
        result = load_merged_models_config(global_cfg, local_cfg)
        assert "openai" in result["providers"]
        assert "anthropic" in result["providers"]


class TestLoadModelsConfig:
    """Tests for load_models_config with three-tier loading."""

    def test_user_global_config_merged(self):
        """Test that user global config is merged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create user global config
            user_prax_dir = Path(tmpdir) / ".prax"
            user_prax_dir.mkdir()
            user_config = user_prax_dir / "models.yaml"
            user_config.write_text("default_model: user-model\nproviders: {}")

            # Monkeypatch HOME
            with patch.dict(os.environ, {"HOME": tmpdir}):
                with patch("pathlib.Path.home", return_value=Path(tmpdir)):
                    config = load_models_config(tmpdir)
                    assert config.get("default_model") == "user-model"


class TestBudgetCommand:
    """Tests for /budget command."""

    def test_budget_show_no_session(self):
        """Test showing budget with no session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileSessionStore(tmpdir)
            ctx = CommandContext(
                cwd=tmpdir,
                models_config={"default_model": "gpt-4"},
                session_store=store,
                session_id=None
            )

            result = _handle_budget([], ctx)
            assert "unlimited" in result.text.lower() or "No token budget" in result.text

    def test_budget_set(self):
        """Test setting budget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileSessionStore(tmpdir)
            ctx = CommandContext(
                cwd=tmpdir,
                models_config={"default_model": "gpt-4"},
                session_store=store,
                session_id=None
            )

            result = _handle_budget(["50000"], ctx)
            assert "50,000" in result.text
            assert result.data["max_budget_tokens"] == 50000

    def test_budget_invalid_value(self):
        """Test setting invalid budget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileSessionStore(tmpdir)
            ctx = CommandContext(
                cwd=tmpdir,
                models_config={"default_model": "gpt-4"},
                session_store=store,
                session_id=None
            )

            result = _handle_budget(["invalid"], ctx)
            assert "Error" in result.text

    def test_budget_command_registered(self):
        """Test that /budget command is registered."""
        cmd = parse_slash_command("/budget")
        assert cmd is not None
        assert cmd.name == "budget"
