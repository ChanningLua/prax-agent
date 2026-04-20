"""Unit tests for prax.core.runtime_env."""

from __future__ import annotations

import os
from pathlib import Path

from prax.core.runtime_env import hydrate_runtime_env


def _models_config() -> dict:
    return {
        "providers": {
            "anthropic": {
                "api_key_env": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
                "base_url": "https://api.anthropic.com",
                "base_url_env": "ANTHROPIC_BASE_URL",
                "models": [{"name": "claude-sonnet-4-6", "api_model": "claude-sonnet-4-6"}],
            }
        }
    }


def test_hydrate_runtime_env_uses_expected_precedence(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".prax").mkdir(parents=True, exist_ok=True)
    (fake_home / ".prax" / ".env").write_text(
        "ANTHROPIC_API_KEY=home-key\nANTHROPIC_BASE_URL=https://home.example\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=project-key\nANTHROPIC_BASE_URL=https://project.example\n",
        encoding="utf-8",
    )
    (tmp_path / ".prax").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".prax" / ".env").write_text(
        "ANTHROPIC_API_KEY=local-key\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    loaded = hydrate_runtime_env(_models_config(), str(tmp_path))

    assert os.environ["ANTHROPIC_API_KEY"] == "local-key"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://project.example"
    assert loaded["ANTHROPIC_API_KEY"].endswith(".prax/.env")
    assert loaded["ANTHROPIC_BASE_URL"].endswith("/.env")


def test_hydrate_runtime_env_preserves_existing_process_env(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".prax").mkdir(parents=True, exist_ok=True)
    (fake_home / ".prax" / ".env").write_text(
        "ANTHROPIC_API_KEY=home-key\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "existing-key")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    loaded = hydrate_runtime_env(_models_config(), str(tmp_path))

    assert os.environ["ANTHROPIC_API_KEY"] == "existing-key"
    assert "ANTHROPIC_API_KEY" not in loaded
