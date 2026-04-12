"""Unit tests for prax/core/runtime_paths.py."""
from __future__ import annotations

import pytest

from prax.core.runtime_paths import (
    RUNTIME_NATIVE,
    RUNTIME_CLAUDE_CODE,
    OPENPRAX_NATIVE,
    OPENPRAX_FOR_CLAUDE_CODE,
    OPENPRAX_CLAUDE_DEBUG_BRIDGE,
    RuntimePathInfo,
    build_last_run_metadata,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_runtime_native_value():
    assert RUNTIME_NATIVE == "native"


def test_runtime_claude_code_value():
    assert RUNTIME_CLAUDE_CODE == "claude_code"


# ---------------------------------------------------------------------------
# RuntimePathInfo instances
# ---------------------------------------------------------------------------

def test_native_runtime_fields():
    assert OPENPRAX_NATIVE.runtime_path == RUNTIME_NATIVE
    assert OPENPRAX_NATIVE.integration_mode == "native"
    assert OPENPRAX_NATIVE.executor == "direct-api"


def test_openprax_for_claude_code_fields():
    assert OPENPRAX_FOR_CLAUDE_CODE.runtime_path == RUNTIME_CLAUDE_CODE
    assert OPENPRAX_FOR_CLAUDE_CODE.integration_mode == "claude_code"
    assert OPENPRAX_FOR_CLAUDE_CODE.executor == "claude-cli"


def test_openprax_claude_debug_bridge_fields():
    assert OPENPRAX_CLAUDE_DEBUG_BRIDGE.runtime_path == RUNTIME_CLAUDE_CODE
    assert OPENPRAX_CLAUDE_DEBUG_BRIDGE.integration_mode == "claude_cli_bridge"
    assert OPENPRAX_CLAUDE_DEBUG_BRIDGE.executor == "claude-cli"


def test_runtime_path_info_is_frozen():
    with pytest.raises((AttributeError, TypeError)):
        OPENPRAX_NATIVE.executor = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_last_run_metadata
# ---------------------------------------------------------------------------

def test_build_last_run_metadata_basic():
    result = build_last_run_metadata(model="claude-opus-4", runtime=OPENPRAX_NATIVE)
    assert result["model"] == "claude-opus-4"
    assert result["executor"] == "direct-api"
    assert result["runtime_path"] == RUNTIME_NATIVE
    assert result["integration_mode"] == "native"


def test_build_last_run_metadata_with_extra():
    result = build_last_run_metadata(
        model="claude-sonnet-4",
        runtime=OPENPRAX_FOR_CLAUDE_CODE,
        extra={"session_id": "s-42", "version": "1.0"},
    )
    assert result["model"] == "claude-sonnet-4"
    assert result["executor"] == "claude-cli"
    assert result["runtime_path"] == RUNTIME_CLAUDE_CODE
    assert result["session_id"] == "s-42"
    assert result["version"] == "1.0"


def test_build_last_run_metadata_without_extra():
    result = build_last_run_metadata(model="claude-haiku-3", runtime=OPENPRAX_CLAUDE_DEBUG_BRIDGE)
    assert "session_id" not in result
    assert result["integration_mode"] == "claude_cli_bridge"
    assert len(result) == 4


def test_build_last_run_metadata_extra_none_not_merged():
    result = build_last_run_metadata(model="m", runtime=OPENPRAX_NATIVE, extra=None)
    assert len(result) == 4


def test_build_last_run_metadata_extra_empty_dict_not_merged():
    result = build_last_run_metadata(model="m", runtime=OPENPRAX_NATIVE, extra={})
    assert len(result) == 4
