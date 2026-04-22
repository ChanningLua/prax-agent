"""Tests for prax.commands.handlers (pure unit tests, no real I/O)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prax.commands.handlers import (
    CommandContext,
    CommandResult,
    run_command,
    get_flow_status_summary,
)
from prax.commands.registry import ParsedCommand
from prax.core.session_store import FileSessionStore, SessionData
from prax.core.permissions import PermissionMode


# ── Helpers ───────────────────────────────────────────────

def _minimal_models_config(model_name="test-model"):
    """Minimal models config with one model entry."""
    return {
        "default_model": model_name,
        "providers": {
            "anthropic": {
                "format": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
                "base_url": "https://api.anthropic.com",
                "models": [
                    {
                        "name": model_name,
                        "api_model": "claude-sonnet-4-7",
                        "tier": "mid",
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_thinking": False,
                        "supports_reasoning_effort": False,
                    }
                ],
            }
        },
    }


def _make_ctx(tmp_path, session_id=None, models_config=None):
    store = FileSessionStore(str(tmp_path / "sessions"))
    return CommandContext(
        cwd=str(tmp_path),
        models_config=models_config or _minimal_models_config(),
        session_store=store,
        session_id=session_id,
    )


def _save_session(ctx, model=None, messages=None, metadata=None):
    sid = ctx.session_store.create_session_id()
    session = SessionData(
        session_id=sid,
        cwd=ctx.cwd,
        model=model or ctx.models_config.get("default_model"),
        messages=messages or [],
        metadata=metadata or {},
    )
    ctx.session_store.save(session)
    return session


# ── run_command routing ───────────────────────────────────

def test_run_command_unknown_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    cmd = ParsedCommand(name="nonexistent", args=[])
    with pytest.raises(ValueError, match="Unsupported command"):
        run_command(cmd, ctx)


def test_run_command_help_returns_result(tmp_path):
    ctx = _make_ctx(tmp_path)
    cmd = ParsedCommand(name="help", args=[])
    result = run_command(cmd, ctx)
    assert isinstance(result, CommandResult)
    assert result.text


def test_run_command_status_returns_result(tmp_path):
    ctx = _make_ctx(tmp_path)
    cmd = ParsedCommand(name="status", args=[])
    result = run_command(cmd, ctx)
    assert isinstance(result, CommandResult)


# ── _handle_help ──────────────────────────────────────────

def test_handle_help_text_not_empty(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="help", args=[]), ctx)
    assert len(result.text) > 0


def test_handle_help_data_has_commands(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="help", args=[]), ctx)
    assert "commands" in (result.data or {})


# ── _handle_status ────────────────────────────────────────

def test_handle_status_no_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="status", args=[]), ctx)
    data = result.data
    assert "cwd" in data
    assert "default_model" in data
    assert "permission_mode" in data


def test_handle_status_with_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx)
    ctx.session_id = session.session_id
    result = run_command(ParsedCommand(name="status", args=[]), ctx)
    data = result.data
    assert data["session_id"] == session.session_id


def test_handle_status_includes_flow_status(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="status", args=[]), ctx)
    assert "flow_status" in result.data


def test_handle_status_includes_todos(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="status", args=[]), ctx)
    assert "todos" in result.data


# ── _handle_providers ─────────────────────────────────────

def test_handle_providers_lists_providers(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="providers", args=[]), ctx)
    assert "anthropic" in result.text


def test_handle_providers_data_has_providers_key(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="providers", args=[]), ctx)
    assert "providers" in result.data


def test_handle_providers_empty_config(tmp_path):
    ctx = _make_ctx(tmp_path, models_config={"default_model": None, "providers": {}})
    result = run_command(ParsedCommand(name="providers", args=[]), ctx)
    assert "No providers" in result.text or result.data["providers"] == {}


# ── _handle_model ─────────────────────────────────────────

def test_handle_model_no_args_shows_default(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="model", args=[]), ctx)
    data = result.data
    assert "default_model" in data


def test_handle_model_set_creates_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="model", args=["test-model"]), ctx)
    data = result.data
    assert data["preferred_model"] == "test-model"
    assert "session_id" in data


def test_handle_model_unknown_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="Unknown model"):
        run_command(ParsedCommand(name="model", args=["unknown-xyz"]), ctx)


def test_handle_model_set_with_existing_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx)
    ctx.session_id = session.session_id
    result = run_command(ParsedCommand(name="model", args=["test-model"]), ctx)
    assert result.data["preferred_model"] == "test-model"


# ── _handle_thinking ──────────────────────────────────────

def test_handle_thinking_no_args_returns_status(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="thinking", args=[]), ctx)
    assert "preferred_thinking_enabled" in result.data


def test_handle_thinking_on(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="thinking", args=["on"]), ctx)
    assert result.data["preferred_thinking_enabled"] is True


def test_handle_thinking_off(tmp_path):
    ctx = _make_ctx(tmp_path)
    run_command(ParsedCommand(name="thinking", args=["on"]), ctx)  # set on first
    # Now turn off (need session_id from previous call)
    ctx.session_id = result_sid = run_command(
        ParsedCommand(name="thinking", args=["on"]), ctx
    ).data["session_id"]
    result = run_command(ParsedCommand(name="thinking", args=["off"]), ctx)
    assert result.data["preferred_thinking_enabled"] is False


def test_handle_thinking_invalid_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="thinking must be"):
        run_command(ParsedCommand(name="thinking", args=["maybe"]), ctx)


# ── _handle_reasoning ─────────────────────────────────────

def test_handle_reasoning_no_args(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="reasoning", args=[]), ctx)
    assert "preferred_reasoning_effort" in result.data


def test_handle_reasoning_set_high(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="reasoning", args=["high"]), ctx)
    assert result.data["preferred_reasoning_effort"] == "high"


def test_handle_reasoning_set_none(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="reasoning", args=["none"]), ctx)
    assert result.data["preferred_reasoning_effort"] == "none"


def test_handle_reasoning_invalid_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="reasoning must be"):
        run_command(ParsedCommand(name="reasoning", args=["extreme"]), ctx)


# ── _handle_permissions ───────────────────────────────────

def test_handle_permissions_no_args_shows_current(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="permissions", args=[]), ctx)
    assert "default_permission_mode" in result.data


def test_handle_permissions_set_mode(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(
        ParsedCommand(name="permissions", args=["workspace-write"]), ctx
    )
    assert "preferred_permission_mode" in result.data


def test_handle_permissions_invalid_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(Exception):
        run_command(ParsedCommand(name="permissions", args=["god-mode"]), ctx)


# ── _handle_session ───────────────────────────────────────

def test_handle_session_list_empty(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="session", args=["list"]), ctx)
    assert result.data["sessions"] == []


def test_handle_session_list_with_sessions(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx)
    result = run_command(ParsedCommand(name="session", args=["list"]), ctx)
    ids = [s["session_id"] for s in result.data["sessions"]]
    assert session.session_id in ids


def test_handle_session_show(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx)
    result = run_command(ParsedCommand(name="session", args=["show", session.session_id]), ctx)
    assert result.data["session_id"] == session.session_id


def test_handle_session_show_missing_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="Session not found"):
        run_command(ParsedCommand(name="session", args=["show", "missing"]), ctx)


def test_handle_session_delete(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx)
    result = run_command(ParsedCommand(name="session", args=["delete", session.session_id]), ctx)
    assert result.data["deleted"] == session.session_id


def test_handle_session_delete_missing_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="Session not found"):
        run_command(ParsedCommand(name="session", args=["delete", "missing"]), ctx)


def test_handle_session_requires_id_for_show(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="requires an id"):
        run_command(ParsedCommand(name="session", args=["show"]), ctx)


def test_handle_session_unknown_action_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="Unsupported session action"):
        run_command(ParsedCommand(name="session", args=["zap", "x"]), ctx)


# ── _handle_todo ──────────────────────────────────────────

def test_handle_todo_show_empty(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="todo", args=["show"]), ctx)
    assert result.data["todos"] == []


def test_handle_todo_clear(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="todo", args=["clear"]), ctx)
    assert result.data["cleared"] is True


def test_handle_todo_default_action_is_show(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="todo", args=[]), ctx)
    assert "todos" in result.data


def test_handle_todo_invalid_action_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="todo action"):
        run_command(ParsedCommand(name="todo", args=["delete"]), ctx)


# ── _handle_compact ───────────────────────────────────────

def test_handle_compact_no_session_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="No session found"):
        run_command(ParsedCommand(name="compact", args=[]), ctx)


def test_handle_compact_with_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx, messages=[
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    ctx.session_id = session.session_id
    result = run_command(ParsedCommand(name="compact", args=[]), ctx)
    assert "before" in result.data
    assert "after" in result.data


# ── _handle_clear ─────────────────────────────────────────

def test_handle_clear_no_session_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="No session found"):
        run_command(ParsedCommand(name="clear", args=[]), ctx)


def test_handle_clear_with_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx, messages=[{"role": "user", "content": "test"}])
    ctx.session_id = session.session_id
    result = run_command(ParsedCommand(name="clear", args=[]), ctx)
    assert result.data["cleared"] is True


# ── _handle_cost ──────────────────────────────────────────

def test_handle_cost_no_session_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="No session found"):
        run_command(ParsedCommand(name="cost", args=[]), ctx)


def test_handle_cost_with_session(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx, metadata={"usage": {"total_tokens": 500}})
    ctx.session_id = session.session_id
    result = run_command(ParsedCommand(name="cost", args=[]), ctx)
    assert result.data["usage"]["total_tokens"] == 500


# ── _handle_resume ────────────────────────────────────────

def test_handle_resume_no_args_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="resume requires"):
        run_command(ParsedCommand(name="resume", args=[]), ctx)


def test_handle_resume_missing_session_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ValueError, match="Session not found"):
        run_command(ParsedCommand(name="resume", args=["nonexistent"]), ctx)


def test_handle_resume_success(tmp_path):
    ctx = _make_ctx(tmp_path)
    session = _save_session(ctx)
    result = run_command(ParsedCommand(name="resume", args=[session.session_id]), ctx)
    assert result.data["session_id"] == session.session_id
    assert "resume_hint" in result.data


# ── _handle_budget ────────────────────────────────────────

def test_handle_budget_no_args_shows_unlimited(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="budget", args=[]), ctx)
    assert result.data["max_budget_tokens"] is None


def test_handle_budget_set_value(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="budget", args=["50000"]), ctx)
    assert result.data["max_budget_tokens"] == 50000


def test_handle_budget_invalid_returns_error(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="budget", args=["notanumber"]), ctx)
    assert "error" in result.data


def test_handle_budget_zero_returns_error(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="budget", args=["0"]), ctx)
    assert "error" in result.data


# ── _handle_skills ────────────────────────────────────────

def test_handle_skills_no_skills_found(tmp_path, monkeypatch):
    monkeypatch.setattr("prax.commands.handlers.load_skills", lambda cwd: [])
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="skills", args=[]), ctx)
    assert result.data["skills"] == []


def test_handle_skills_with_skill(tmp_path):
    skill_dir = tmp_path / ".prax" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# My Skill\nDoes things.\n")
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="skills", args=[]), ctx)
    names = [s["name"] for s in result.data["skills"]]
    assert "my-skill" in names


def test_handle_skills_show_specific(tmp_path):
    skill_dir = tmp_path / ".prax" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# My Skill\nContent here.\n")
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="skills", args=["show", "my-skill"]), ctx)
    assert "Content here" in result.text


def test_handle_skills_show_missing(tmp_path):
    skill_dir = tmp_path / ".prax" / "skills" / "real-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Real Skill\n")
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="skills", args=["show", "missing"]), ctx)
    assert "not found" in result.text.lower() or result.data.get("error") == "not_found"


# ── _handle_governance ───────────────────────────────────

def test_handle_governance_returns_result(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="governance", args=[]), ctx)
    assert isinstance(result, CommandResult)
    assert result.data is not None


def test_handle_governance_data_has_agent_count(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = run_command(ParsedCommand(name="governance", args=[]), ctx)
    assert "agents" in result.data


# ── get_flow_status_summary ───────────────────────────────

def test_get_flow_status_summary_returns_string(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = get_flow_status_summary(ctx)
    assert isinstance(result, str)


def test_get_flow_status_summary_contains_all_flows(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = get_flow_status_summary(ctx)
    assert "glm:" in result
    assert "codex:" in result
    assert "claude:" in result


def test_get_flow_status_summary_off_when_no_entries(tmp_path):
    ctx = _make_ctx(tmp_path, models_config={"providers": {}})
    result = get_flow_status_summary(ctx)
    assert "glm:off" in result
    assert "codex:off" in result
    assert "claude:off" in result


# ── CommandResult.render ──────────────────────────────────

def test_command_result_render_text():
    r = CommandResult(text="hello", data={"x": 1})
    assert r.render("text") == "hello"


def test_command_result_render_json():
    r = CommandResult(text="ignored", data={"x": 1})
    output = r.render("json")
    parsed = json.loads(output)
    assert parsed["x"] == 1


def test_command_result_render_json_fallback():
    r = CommandResult(text="hello", data=None)
    output = r.render("json")
    parsed = json.loads(output)
    assert parsed["text"] == "hello"
