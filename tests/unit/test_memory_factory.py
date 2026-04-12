"""Unit tests for memory backend factory (prax/core/memory/factory.py).

No real I/O — uses monkeypatch to control imports and the singleton state.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import prax.core.memory.factory as factory_mod
from prax.core.memory.backend import MemoryBackend
from prax.core.memory.factory import (
    get_memory_backend,
    reset_memory_backend,
    _build_backend,
)


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Ensure the singleton is cleared before and after each test."""
    reset_memory_backend()
    yield
    reset_memory_backend()


# ── "local" backend ──────────────────────────────────────────────────────────


def test_get_memory_backend_local_returns_local_backend() -> None:
    config = {"memory": {"backend": "local"}}
    backend = get_memory_backend(config)
    from prax.core.memory.local_backend import LocalMemoryBackend
    assert isinstance(backend, LocalMemoryBackend)


def test_get_memory_backend_default_is_local() -> None:
    backend = get_memory_backend({})
    from prax.core.memory.local_backend import LocalMemoryBackend
    assert isinstance(backend, LocalMemoryBackend)


# ── "sqlite" backend ─────────────────────────────────────────────────────────


def test_get_memory_backend_sqlite_returns_sqlite_backend() -> None:
    config = {"memory": {"backend": "sqlite"}}
    backend = get_memory_backend(config)
    from prax.core.memory.sqlite_backend import SQLiteMemoryBackend
    assert isinstance(backend, SQLiteMemoryBackend)


def test_sqlite_backend_respects_config_options() -> None:
    config = {
        "memory": {
            "backend": "sqlite",
            "sqlite": {
                "max_facts": 50,
                "fact_confidence_threshold": 0.6,
                "max_experiences": 200,
            },
        }
    }
    backend = get_memory_backend(config)
    from prax.core.memory.sqlite_backend import SQLiteMemoryBackend
    assert isinstance(backend, SQLiteMemoryBackend)
    assert backend._max_facts == 50
    assert backend._threshold == 0.6
    assert backend._max_experiences == 200


# ── "openviking" backend ─────────────────────────────────────────────────────


def test_get_memory_backend_openviking_falls_back_when_unavailable() -> None:
    """When OpenViking is unreachable it should fall back to LocalMemoryBackend."""
    config = {"memory": {"backend": "openviking"}}

    with patch("prax.core.memory.factory._try_openviking", return_value=None):
        backend = get_memory_backend(config)

    from prax.core.memory.local_backend import LocalMemoryBackend
    assert isinstance(backend, LocalMemoryBackend)


def test_get_memory_backend_openviking_returns_openviking_when_available() -> None:
    mock_ov = MagicMock(spec=MemoryBackend)
    config = {"memory": {"backend": "openviking"}}

    with patch("prax.core.memory.factory._try_openviking", return_value=mock_ov):
        backend = get_memory_backend(config)

    assert backend is mock_ov


# ── Unknown / custom backend ─────────────────────────────────────────────────


def test_get_memory_backend_unknown_falls_back_to_local() -> None:
    config = {"memory": {"backend": "nonexistent.module.Backend"}}
    backend = get_memory_backend(config)
    from prax.core.memory.local_backend import LocalMemoryBackend
    assert isinstance(backend, LocalMemoryBackend)


def test_get_memory_backend_custom_reflection_valid() -> None:
    """A valid MemoryBackend subclass loaded via dotted path is returned."""
    from prax.core.memory.local_backend import LocalMemoryBackend
    config = {"memory": {"backend": "prax.core.memory.local_backend.LocalMemoryBackend"}}
    backend = get_memory_backend(config)
    assert isinstance(backend, LocalMemoryBackend)


# ── Singleton behaviour ──────────────────────────────────────────────────────


def test_get_memory_backend_singleton_same_instance() -> None:
    config = {"memory": {"backend": "local"}}
    b1 = get_memory_backend(config)
    b2 = get_memory_backend(config)
    assert b1 is b2


def test_reset_memory_backend_clears_singleton() -> None:
    config = {"memory": {"backend": "local"}}
    b1 = get_memory_backend(config)
    reset_memory_backend()
    b2 = get_memory_backend(config)
    # After reset a new instance is created
    assert b1 is not b2


def test_singleton_ignores_subsequent_config() -> None:
    """Once built, config changes on subsequent calls are ignored."""
    get_memory_backend({"memory": {"backend": "local"}})
    b2 = get_memory_backend({"memory": {"backend": "sqlite"}})
    from prax.core.memory.local_backend import LocalMemoryBackend
    assert isinstance(b2, LocalMemoryBackend)
