"""Unit tests for the sandbox provider singleton (prax/core/sandbox/provider.py).

No real Docker / subprocess calls — DockerSandboxProvider.is_available is mocked.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import prax.core.sandbox.provider as provider_mod
from prax.core.sandbox.provider import (
    get_sandbox_provider,
    reset_sandbox_provider,
    set_sandbox_provider,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Clear the singleton before and after every test."""
    reset_sandbox_provider()
    yield
    reset_sandbox_provider()


# ── explicit backend="local" ──────────────────────────────────────────────────


def test_get_sandbox_provider_local_returns_local_provider() -> None:
    provider = get_sandbox_provider(backend="local")
    from prax.core.sandbox.local import LocalSandboxProvider

    assert isinstance(provider, LocalSandboxProvider)


# ── explicit backend="docker" ─────────────────────────────────────────────────


def test_get_sandbox_provider_docker_returns_docker_provider() -> None:
    from prax.core.sandbox.docker import DockerSandboxProvider

    provider = get_sandbox_provider(backend="docker")
    assert isinstance(provider, DockerSandboxProvider)


# ── auto mode: Docker available ───────────────────────────────────────────────


def test_get_sandbox_provider_auto_uses_docker_when_available(monkeypatch) -> None:
    monkeypatch.delenv("PRAX_SANDBOX_BACKEND", raising=False)
    with patch(
        "prax.core.sandbox.docker.DockerSandboxProvider.is_available",
        return_value=True,
    ):
        provider = get_sandbox_provider()

    from prax.core.sandbox.docker import DockerSandboxProvider

    assert isinstance(provider, DockerSandboxProvider)


# ── auto mode: Docker unavailable ────────────────────────────────────────────


def test_get_sandbox_provider_auto_falls_back_to_local_when_docker_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PRAX_SANDBOX_BACKEND", raising=False)
    with patch(
        "prax.core.sandbox.docker.DockerSandboxProvider.is_available",
        return_value=False,
    ):
        provider = get_sandbox_provider()

    from prax.core.sandbox.local import LocalSandboxProvider

    assert isinstance(provider, LocalSandboxProvider)


# ── env variable override ─────────────────────────────────────────────────────


def test_get_sandbox_provider_respects_env_variable(monkeypatch) -> None:
    monkeypatch.setenv("PRAX_SANDBOX_BACKEND", "local")
    provider = get_sandbox_provider()
    from prax.core.sandbox.local import LocalSandboxProvider

    assert isinstance(provider, LocalSandboxProvider)


# ── singleton behaviour ───────────────────────────────────────────────────────


def test_get_sandbox_provider_returns_same_instance() -> None:
    p1 = get_sandbox_provider(backend="local")
    p2 = get_sandbox_provider(backend="local")
    assert p1 is p2


def test_reset_sandbox_provider_clears_singleton() -> None:
    p1 = get_sandbox_provider(backend="local")
    reset_sandbox_provider()
    p2 = get_sandbox_provider(backend="local")
    assert p1 is not p2


# ── set_sandbox_provider injection ───────────────────────────────────────────


def test_set_sandbox_provider_injects_custom_provider() -> None:
    mock_provider = MagicMock()
    set_sandbox_provider(mock_provider)
    assert get_sandbox_provider() is mock_provider


# ── reset calls shutdown ──────────────────────────────────────────────────────


def test_reset_calls_shutdown_on_existing_provider() -> None:
    mock_provider = MagicMock()
    set_sandbox_provider(mock_provider)
    reset_sandbox_provider()
    mock_provider.shutdown.assert_called_once()
