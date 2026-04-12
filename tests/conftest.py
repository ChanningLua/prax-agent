from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "asyncio: run the test function inside an asyncio event loop",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if "asyncio" not in pyfuncitem.keywords:
        return None

    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(test_func(**kwargs))
    return True


# Shared fixtures for test coverage


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient that returns configurable responses."""
    from prax.core.llm_client import LLMResponse

    client = AsyncMock()
    client.request.return_value = LLMResponse(
        content="Mock response",
        model="claude-opus-4",
        usage={"input_tokens": 100, "output_tokens": 50},
        stop_reason="end_turn",
    )
    return client


@pytest.fixture
def mock_memory_backend():
    """In-memory implementation of MemoryBackend interface."""
    from prax.core.memory.backend import MemoryBackend

    class MockMemoryBackend(MemoryBackend):
        def __init__(self):
            self.facts: dict[str, Any] = {}
            self.experiences: list[dict] = []
            self.kg_triples: list[tuple] = []

        async def store_fact(self, key: str, value: Any, metadata: dict | None = None):
            self.facts[key] = {"value": value, "metadata": metadata or {}}

        async def get_facts(self, keys: list[str] | None = None) -> dict:
            if keys is None:
                return self.facts
            return {k: v for k, v in self.facts.items() if k in keys}

        async def search_facts(self, query: str, limit: int = 10) -> list[dict]:
            return [
                {"key": k, **v}
                for k, v in self.facts.items()
                if query.lower() in k.lower()
            ][:limit]

        async def delete_fact(self, key: str):
            self.facts.pop(key, None)

        async def store_experience(self, experience: dict):
            self.experiences.append(experience)

        async def get_experiences(self, limit: int = 10) -> list[dict]:
            return self.experiences[-limit:]

        async def add_kg_triple(self, subject: str, predicate: str, obj: str):
            self.kg_triples.append((subject, predicate, obj))

        async def query_kg(self, subject: str | None = None) -> list[tuple]:
            if subject is None:
                return self.kg_triples
            return [t for t in self.kg_triples if t[0] == subject]

        def format_for_prompt(self, facts: dict) -> str:
            return "\n".join(f"{k}: {v['value']}" for k, v in facts.items())

    return MockMemoryBackend()


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Mock asyncio subprocess creation."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"stdout", b"stderr")
    mock_process.returncode = 0

    async def mock_create_subprocess_shell(*args, **kwargs):
        return mock_process

    async def mock_create_subprocess_exec(*args, **kwargs):
        return mock_process

    monkeypatch.setattr(
        "asyncio.create_subprocess_shell", mock_create_subprocess_shell
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create_subprocess_exec)

    return mock_process


@pytest.fixture
def make_runtime_state():
    """Factory for creating RuntimeState instances."""
    from prax.core.agent_message import AgentMessage

    def _make(
        messages: list[dict] | None = None,
        context: dict | None = None,
        **kwargs,
    ):
        from prax.core.context import RuntimeState

        return RuntimeState(
            messages=messages or [{"role": "user", "content": "test"}],
            context=context or {},
            **kwargs,
        )

    return _make


@pytest.fixture
def tmp_prax_project(tmp_path: Path):
    """Create a temporary .prax/ directory structure."""
    prax_dir = tmp_path / ".prax"
    prax_dir.mkdir()
    (prax_dir / "agents").mkdir()
    (prax_dir / "skills").mkdir()
    (prax_dir / "sessions").mkdir()
    (prax_dir / "memory").mkdir()

    # Create minimal config
    config_file = prax_dir / "config.yaml"
    config_file.write_text(
        """
model: claude-opus-4
permission_mode: ask
"""
    )

    return tmp_path
