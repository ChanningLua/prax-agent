"""Unit tests for prax/core/agent_message.py."""
from __future__ import annotations

import pytest

from prax.core.agent_message import AgentMessage


# ---------------------------------------------------------------------------
# Field access
# ---------------------------------------------------------------------------

def test_all_field_access():
    msg = AgentMessage(
        sender="alice",
        content="hello world",
        status="ok",
        usage={"input_tokens": 10, "output_tokens": 5},
        metadata={"run_id": "abc"},
        trace_id="trace-1",
    )
    assert msg.sender == "alice"
    assert msg.content == "hello world"
    assert msg.status == "ok"
    assert msg.usage == {"input_tokens": 10, "output_tokens": 5}
    assert msg.metadata == {"run_id": "abc"}
    assert msg.trace_id == "trace-1"


# ---------------------------------------------------------------------------
# Default field values
# ---------------------------------------------------------------------------

def test_default_status():
    msg = AgentMessage(sender="bot", content="hi")
    assert msg.status == "ok"


def test_default_usage_is_empty_dict():
    msg = AgentMessage(sender="bot", content="hi")
    assert msg.usage == {}


def test_default_metadata_is_empty_dict():
    msg = AgentMessage(sender="bot", content="hi")
    assert msg.metadata == {}


def test_default_trace_id_is_none():
    msg = AgentMessage(sender="bot", content="hi")
    assert msg.trace_id is None


# ---------------------------------------------------------------------------
# to_text()
# ---------------------------------------------------------------------------

def test_to_text_ok_returns_content():
    msg = AgentMessage(sender="bot", content="result text", status="ok")
    assert msg.to_text() == "result text"


def test_to_text_error_prefixes_status():
    msg = AgentMessage(sender="bot", content="something went wrong", status="error")
    assert msg.to_text() == "[ERROR] something went wrong"


def test_to_text_timeout_prefixes_status():
    msg = AgentMessage(sender="bot", content="timed out", status="timeout")
    assert msg.to_text() == "[TIMEOUT] timed out"


# ---------------------------------------------------------------------------
# ok() factory
# ---------------------------------------------------------------------------

def test_ok_factory_basic():
    msg = AgentMessage.ok(sender="planner", content="plan done")
    assert msg.sender == "planner"
    assert msg.content == "plan done"
    assert msg.status == "ok"


def test_ok_factory_with_kwargs():
    msg = AgentMessage.ok(
        sender="planner",
        content="done",
        usage={"input_tokens": 42},
        trace_id="t-99",
    )
    assert msg.usage == {"input_tokens": 42}
    assert msg.trace_id == "t-99"


# ---------------------------------------------------------------------------
# error() factory
# ---------------------------------------------------------------------------

def test_error_factory_basic():
    msg = AgentMessage.error(sender="worker", content="failed to execute")
    assert msg.sender == "worker"
    assert msg.content == "failed to execute"
    assert msg.status == "error"


def test_error_factory_to_text():
    msg = AgentMessage.error(sender="worker", content="disk full")
    assert msg.to_text() == "[ERROR] disk full"


# ---------------------------------------------------------------------------
# Custom usage / metadata
# ---------------------------------------------------------------------------

def test_custom_usage():
    msg = AgentMessage(
        sender="bot",
        content="x",
        usage={"input_tokens": 100, "output_tokens": 200, "cache_tokens": 50},
    )
    assert msg.usage["cache_tokens"] == 50


def test_custom_metadata():
    msg = AgentMessage(
        sender="bot",
        content="x",
        metadata={"agent_version": "1.2.3", "session_id": "s-001"},
    )
    assert msg.metadata["agent_version"] == "1.2.3"
    assert msg.metadata["session_id"] == "s-001"
