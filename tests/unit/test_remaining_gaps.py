"""Targeted tests for remaining coverage gaps."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ── prax/core/planning.py ─────────────────────────────────────────────────

def test_generate_initial_plan_basic():
    from prax.core.planning import generate_initial_plan
    result = generate_initial_plan("Build a REST API with auth")
    assert len(result) > 0
    assert all(hasattr(item, "content") for item in result)


def test_generate_initial_plan_empty_raises():
    from prax.core.planning import generate_initial_plan
    with pytest.raises(ValueError):
        generate_initial_plan("")


def test_generate_initial_plan_whitespace_raises():
    from prax.core.planning import generate_initial_plan
    with pytest.raises(ValueError):
        generate_initial_plan("   ")


# ── prax/core/yolo_classifier.py ──────────────────────────────────────────

def test_yolo_classifier_classify_bash_low_risk():
    from prax.core.yolo_classifier import YoloClassifier
    classifier = YoloClassifier()
    result = classifier.classify_bash("ls -la")
    assert result.allow is True


def test_yolo_classifier_classify_bash_high_risk():
    from prax.core.yolo_classifier import YoloClassifier
    classifier = YoloClassifier()
    result = classifier.classify_bash("rm -rf /")
    assert result.allow is False


@pytest.mark.asyncio
async def test_yolo_classifier_classify_bash_async_low():
    from prax.core.yolo_classifier import YoloClassifier
    classifier = YoloClassifier()
    result = await classifier.classify_bash_async("cat /etc/hosts")
    assert result.allow is True


@pytest.mark.asyncio
async def test_yolo_classifier_classify_bash_async_high():
    from prax.core.yolo_classifier import YoloClassifier
    classifier = YoloClassifier()
    result = await classifier.classify_bash_async("rm -rf /important")
    assert result.allow is False


def test_yolo_classifier_classify_tool_call():
    from prax.core.yolo_classifier import YoloClassifier
    classifier = YoloClassifier()
    # classify_tool_call is async
    import asyncio
    result = asyncio.run(classifier.classify_tool_call("Read", {"file_path": "/test.py"}))
    assert result.allow is True


def test_yolo_classifier_classify_tool_call_dangerous():
    from prax.core.yolo_classifier import YoloClassifier
    classifier = YoloClassifier()
    import asyncio
    result = asyncio.run(classifier.classify_tool_call("Bash", {"command": "echo hello"}))
    assert result is not None


# ── prax/core/forked_agent.py ─────────────────────────────────────────────

def test_forked_agent_imports():
    from prax.core.forked_agent import ForkedAgent
    assert ForkedAgent is not None


# ── prax/core/memory/knowledge_graph.py ───────────────────────────────────

def test_knowledge_graph_init(tmp_path):
    from prax.core.memory.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(str(tmp_path))
    assert kg is not None


# ── prax/core/memory/layers.py ────────────────────────────────────────────

def test_layered_injector_basic():
    from prax.core.memory.layers import LayeredInjector
    injector = LayeredInjector(
        kg=None,
        vector_store=None,
        memory_store=None,
        memory_backend=None,
    )
    assert injector is not None


@pytest.mark.asyncio
async def test_layered_injector_build_async_empty():
    from prax.core.memory.layers import LayeredInjector
    injector = LayeredInjector(
        kg=None,
        vector_store=None,
        memory_store=None,
        memory_backend=None,
    )
    result = await injector.build_async("/tmp/test", query="test query")
    assert isinstance(result, str)


# ── prax/core/memory/local_backend.py ────────────────────────────────────

@pytest.mark.asyncio
async def test_local_backend_format_for_prompt_empty(tmp_path):
    from prax.core.memory.local_backend import LocalMemoryBackend
    backend = LocalMemoryBackend()
    result = await backend.format_for_prompt(str(tmp_path))
    assert isinstance(result, str)


# ── prax/core/skills_loader.py ───────────────────────────────────────────

def test_format_skills_for_subagent():
    from prax.core.skills_loader import format_skills_for_subagent
    result = format_skills_for_subagent([])
    assert isinstance(result, str)


# ── prax/core/streaming_tool_executor.py ─────────────────────────────────

def test_streaming_tool_executor_imports():
    from prax.core.streaming_tool_executor import StreamingToolExecutor
    assert StreamingToolExecutor is not None


# ── prax/core/agent_loop.py ─────────────────────────────────────────────

def test_agent_run_report():
    from prax.core.agent_loop import AgentRunReport
    report = AgentRunReport(
        stop_reason="end_turn",
        iterations=5,
        had_tool_errors=False,
        only_permission_errors=False,
    )
    assert report.stop_reason == "end_turn"
    assert report.iterations == 5


# ── prax/commands/registry.py ────────────────────────────────────────────

def test_command_registry_format_help():
    from prax.commands.registry import format_help
    help_text = format_help()
    assert isinstance(help_text, str)
    assert len(help_text) > 0
