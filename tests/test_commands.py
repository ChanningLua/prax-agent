from __future__ import annotations

import json

from prax.commands.handlers import CommandContext, run_command
from prax.commands.registry import ParsedCommand, parse_command_tokens, parse_slash_command
from prax.core.model_catalog import get_first_available_model, iter_model_catalog
from prax.core.permissions import PermissionMode
from prax.core.session_store import FileSessionStore, SessionData


def _models_config() -> dict:
    return {
        "default_model": "gpt-5.4",
        "upgrade_chain": ["gpt-5.4", "claude-sonnet-4-7"],
        "providers": {
            "zhipu": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key_env": "ZHIPU_API_KEY",
                "format": "openai",
                "models": [
                    {
                        "name": "glm-5",
                        "api_model": "glm-5",
                        "aliases": ["glm"],
                        "request_mode": "chat_completions",
                        "tier": "high",
                        "supports_tools": True,
                        "supports_streaming": True,
                    },
                ],
            },
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [
                    {
                        "name": "gpt-5.4",
                        "api_model": "gpt-5.4",
                        "aliases": ["gpt"],
                        "request_mode": "chat_completions",
                        "tier": "standard",
                        "cost_per_1m_tokens": 5.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_reasoning_effort": True,
                        "default_reasoning_effort": "medium",
                    },
                    {
                        "name": "codex",
                        "api_model": "<replace-with-codex-model>",
                        "aliases": ["codex"],
                        "request_mode": "responses",
                        "tier": "high",
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_reasoning_effort": True,
                        "default_reasoning_effort": "medium",
                    },
                ],
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": "ANTHROPIC_API_KEY",
                "format": "anthropic",
                "models": [
                    {
                        "name": "claude-sonnet-4-7",
                        "api_model": "claude-sonnet-4-7",
                        "aliases": ["sonnet"],
                        "request_mode": "chat_completions",
                        "tier": "premium",
                        "cost_per_1m_tokens": 15.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_thinking": True,
                        "default_thinking_budget_tokens": 12000,
                    },
                ],
            },
        },
    }


def test_parse_command_tokens_and_slash():
    command = parse_command_tokens(["status"])
    slash = parse_slash_command("/model claude-sonnet-4-7")

    assert command == ParsedCommand(name="status", args=[])
    assert slash == ParsedCommand(name="model", args=["claude-sonnet-4-7"])


def test_model_catalog_marks_availability(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)

    catalog = iter_model_catalog(_models_config())

    by_name = {entry.name: entry for entry in catalog}
    assert by_name["gpt-5.4"].available is True
    assert by_name["gpt-5.4"].matches("gpt") is True
    assert by_name["claude-sonnet-4-7"].available is False
    assert by_name["codex"].available is False
    assert get_first_available_model(["claude-sonnet-4-7", "gpt-5.4"], _models_config()).name == "gpt-5.4"


def test_run_command_session_and_cost(tmp_path):
    store = FileSessionStore(str(tmp_path / ".prax" / "sessions"))
    todo_store_path = tmp_path / ".prax" / "todos.json"
    todo_store_path.parent.mkdir(parents=True, exist_ok=True)
    todo_store_path.write_text(
        json.dumps([{"content": "Plan", "activeForm": "Planning", "status": "in_progress"}]),
        encoding="utf-8",
    )
    session = SessionData(
        session_id="session_demo",
        cwd=str(tmp_path),
        model="gpt-5.4",
        messages=[{"role": "user", "content": "hello"}],
        metadata={
            "usage": {"total_tokens": 2000},
            "upgrade_history": [{"from": "gpt-5.4", "to": "claude-sonnet-4-7", "reason": "tool_error"}],
            "last_run": {
                "model": "gpt-5.4",
                "executor": "direct-api",
                "runtime_path": "native",
                "integration_mode": "native",
            },
        },
    )
    store.save(session)
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=store,
        session_id="session_demo",
        permission_mode=PermissionMode.WORKSPACE_WRITE,
    )

    status = run_command(ParsedCommand(name="status", args=[]), ctx)
    cost = run_command(ParsedCommand(name="cost", args=[]), ctx)
    sessions = run_command(ParsedCommand(name="session", args=["list"]), ctx)

    status_data = json.loads(status.text)
    cost_data = json.loads(cost.text)
    sessions_data = json.loads(sessions.text)

    assert status_data["fallback_count"] == 1
    assert status_data["provider"] == "openai"
    assert status_data["request_mode"] == "chat_completions"
    assert status_data["executor"] == "direct-api"
    assert status_data["runtime_path"] == "native"
    assert status_data["integration_mode"] == "native"
    assert status_data["supports_reasoning_effort"] is True
    assert "glm:" in status_data["flow_status"]
    assert status_data["todos"][0]["content"] == "Plan"
    assert cost_data["estimated_cost"] == 0.01
    assert sessions_data["sessions"][0]["session_id"] == "session_demo"


def test_run_command_status_without_session_includes_model_details(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    status = run_command(ParsedCommand(name="status", args=[]), ctx)
    data = json.loads(status.text)

    assert data["default_model"] == "gpt-5.4"
    assert data["provider"] == "openai"
    assert data["api_model"] == "gpt-5.4"
    assert data["request_mode"] == "chat_completions"
    assert data["supports_reasoning_effort"] is True


def test_run_command_providers_includes_capabilities(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    result = run_command(ParsedCommand(name="providers", args=[]), ctx)
    data = result.data

    openai_model = data["providers"]["openai"][0]
    assert openai_model["aliases"] == ["gpt"]
    assert openai_model["request_mode"] == "chat_completions"
    assert openai_model["supports_tools"] is True


def test_run_command_doctor_and_template(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    doctor = run_command(ParsedCommand(name="doctor", args=["all"]), ctx)
    template = run_command(ParsedCommand(name="template", args=["codex"]), ctx)

    doctor_data = doctor.data["flows"]
    assert doctor_data["claude"]["status"] == "ready"
    assert doctor_data["glm"]["status"] == "missing-key"
    assert doctor_data["codex"]["status"] == "template"
    assert doctor_data["claude"]["ready"] is True
    assert doctor_data["glm"]["ready"] is False
    assert doctor_data["codex"]["ready"] is False
    assert "replace-with-codex-model" in template.text


def test_run_command_doctor_export_env_hints(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    result = run_command(ParsedCommand(name="doctor", args=["codex", "--export-env-hint"]), ctx)

    codex = result.data["flows"]["codex"]
    assert codex["missing_env_names"] == ["OPENAI_API_KEY"]
    assert codex["export_hints"] == ["export OPENAI_API_KEY=<your-openai_api_key>"]
    assert ".prax/.env" in codex["next_step"]


def test_run_command_init_models_and_doctor_fix(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    init_result = run_command(ParsedCommand(name="init-models", args=["codex"]), ctx)
    doctor_fix_result = run_command(ParsedCommand(name="doctor", args=["glm", "--fix"]), ctx)

    local_models = (tmp_path / ".prax" / "models.yaml").read_text(encoding="utf-8")

    assert "codex" in init_result.text
    assert "zhipu" in local_models
    assert "<replace-with-codex-model>" in local_models
    assert doctor_fix_result.data["flows"]["fix"]["flow"] == "glm"


def test_run_command_init_models_force_overwrites_existing(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    run_command(ParsedCommand(name="init-models", args=["codex"]), ctx)
    force_result = run_command(ParsedCommand(name="init-models", args=["claude", "--force"]), ctx)

    local_models = (tmp_path / ".prax" / "models.yaml").read_text(encoding="utf-8")

    assert force_result.data["force"] is True
    assert "anthropic:" in local_models
    assert "openai:" not in local_models


def test_run_command_doctor_all_fix(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    result = run_command(ParsedCommand(name="doctor", args=["all", "--fix"]), ctx)
    local_models = (tmp_path / ".prax" / "models.yaml").read_text(encoding="utf-8")

    assert result.data["flows"]["fix"]["flow"] == "all"
    assert "zhipu:" in local_models
    assert "anthropic:" in local_models
    assert "codex" in local_models


def test_run_command_init_models_set_default(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    result = run_command(ParsedCommand(name="init-models", args=["claude", "--set-default"]), ctx)
    local_models = (tmp_path / ".prax" / "models.yaml").read_text(encoding="utf-8")

    assert result.data["set_default"] is True
    assert "default_model: claude-sonnet-4-7" in local_models


def test_run_command_doctor_fix_writes_env_file(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    result = run_command(
        ParsedCommand(name="doctor", args=["codex", "--fix", "--write-env-file"]),
        ctx,
    )

    env_text = (tmp_path / ".prax" / ".env.example").read_text(encoding="utf-8")
    local_models = (tmp_path / ".prax" / "models.yaml").read_text(encoding="utf-8")

    assert result.data["flows"]["fix"]["env_path"].endswith(".prax/.env.example")
    assert "OPENAI_API_KEY=<your-openai_api_key>" in env_text
    assert "codex" in local_models


def test_run_command_doctor_all_fix_set_default(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    run_command(
        ParsedCommand(name="doctor", args=["all", "--fix", "--set-default"]),
        ctx,
    )

    local_models = (tmp_path / ".prax" / "models.yaml").read_text(encoding="utf-8")

    assert "default_model: glm-5" in local_models


def test_run_command_model_updates_session(tmp_path):
    store = FileSessionStore(str(tmp_path / ".prax" / "sessions"))
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=store,
        session_id="session_demo",
        permission_mode=PermissionMode.WORKSPACE_WRITE,
    )

    result = run_command(ParsedCommand(name="model", args=["claude-sonnet-4-7"]), ctx)
    saved = store.load("session_demo")

    assert json.loads(result.text)["preferred_model"] == "claude-sonnet-4-7"
    assert saved is not None
    assert saved.metadata == {"preferred_model": "claude-sonnet-4-7"}


def test_run_command_thinking_and_reasoning_update_session(tmp_path):
    store = FileSessionStore(str(tmp_path / ".prax" / "sessions"))
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=store,
        session_id="session_demo",
        permission_mode=PermissionMode.WORKSPACE_WRITE,
    )

    run_command(ParsedCommand(name="thinking", args=["on"]), ctx)
    run_command(ParsedCommand(name="reasoning", args=["high"]), ctx)
    saved = store.load("session_demo")

    assert saved is not None
    assert saved.metadata == {
        "preferred_thinking_enabled": True,
        "preferred_reasoning_effort": "high",
    }


def test_run_command_todo_show_and_clear(tmp_path):
    todo_path = tmp_path / ".prax" / "todos.json"
    todo_path.parent.mkdir(parents=True, exist_ok=True)
    todo_path.write_text(
        json.dumps([{"content": "Plan", "activeForm": "Planning", "status": "in_progress"}]),
        encoding="utf-8",
    )
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    shown = run_command(ParsedCommand(name="todo", args=["show"]), ctx)
    cleared = run_command(ParsedCommand(name="todo", args=["clear"]), ctx)

    assert json.loads(shown.text)["todos"][0]["content"] == "Plan"
    assert json.loads(cleared.text)["cleared"] is True
    assert not todo_path.exists()


def test_run_command_plan_seeds_todos(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    result = run_command(ParsedCommand(name="plan", args=["add", "user", "login"]), ctx)
    data = json.loads(result.text)

    assert data["task"] == "add user login"
    assert len(data["todos"]) == 3
    assert data["todos"][0]["status"] == "in_progress"
    assert all(t["status"] in {"pending", "in_progress"} for t in data["todos"])
    todo_path = tmp_path / ".prax" / "todos.json"
    assert todo_path.exists()
    persisted = json.loads(todo_path.read_text(encoding="utf-8"))
    assert len(persisted) == 3


def test_run_command_plan_requires_task(tmp_path):
    ctx = CommandContext(
        cwd=str(tmp_path),
        models_config=_models_config(),
        session_store=FileSessionStore(str(tmp_path / ".prax" / "sessions")),
    )

    import pytest
    with pytest.raises(ValueError, match="plan requires a task description"):
        run_command(ParsedCommand(name="plan", args=[]), ctx)
