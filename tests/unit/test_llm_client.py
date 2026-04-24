"""Unit tests for LLMClient and LLMResponse (prax/core/llm_client.py).

All HTTP calls are mocked via httpx.AsyncClient.  No real network I/O.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prax.core.llm_client import LLMClient, LLMResponse, ModelConfig


# ── Helpers ──────────────────────────────────────────────────────────────────


def _cfg(
    provider: str = "openai",
    model: str = "gpt-4",
    api_format: str = "openai",
    supports_tools: bool = True,
    supports_streaming: bool = True,
    request_mode: str = "chat_completions",
    supports_reasoning_effort: bool = False,
    supports_thinking: bool = False,
) -> ModelConfig:
    return ModelConfig(
        provider=provider,
        model=model,
        base_url="https://api.example.com",
        api_key="test-key",
        api_format=api_format,
        supports_tools=supports_tools,
        supports_streaming=supports_streaming,
        request_mode=request_mode,
        supports_reasoning_effort=supports_reasoning_effort,
        supports_thinking=supports_thinking,
    )


def _anthropic_cfg(**kw) -> ModelConfig:
    return _cfg(provider="anthropic", model="claude-3-5-sonnet", api_format="anthropic", **kw)


def _mock_http_response(status: int = 200, data: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = data or {}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


def _openai_response(content: str = "hello", tool_calls: list | None = None) -> dict:
    msg: dict[str, Any] = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": "stop"}],
        "usage": {"total_tokens": 42},
    }


def _anthropic_response(
    content: list | None = None,
    stop_reason: str = "end_turn",
) -> dict:
    return {
        "content": content or [{"type": "text", "text": "hi"}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


# ── LLMResponse properties ────────────────────────────────────────────────────


def test_llm_response_text_joins_text_blocks() -> None:
    resp = LLMResponse(
        content=[
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
    )
    assert resp.text == "hello\nworld"


def test_llm_response_text_empty_when_no_text_blocks() -> None:
    resp = LLMResponse(content=[{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}])
    assert resp.text == ""


def test_llm_response_tool_calls_returns_tool_call_list() -> None:
    resp = LLMResponse(
        content=[
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "/foo"}},
        ]
    )
    calls = resp.tool_calls
    assert len(calls) == 1
    assert calls[0].name == "Read"
    assert calls[0].id == "t1"
    assert calls[0].input == {"path": "/foo"}


def test_llm_response_has_tool_calls_true() -> None:
    resp = LLMResponse(
        content=[{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
    )
    assert resp.has_tool_calls is True


def test_llm_response_has_tool_calls_false() -> None:
    resp = LLMResponse(content=[{"type": "text", "text": "plain text"}])
    assert resp.has_tool_calls is False


# ── resolve_model ─────────────────────────────────────────────────────────────


def test_resolve_model_finds_by_name() -> None:
    client = LLMClient.__new__(LLMClient)
    models_config = {
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [{"name": "gpt-4", "supports_tools": True}],
            }
        }
    }
    cfg = client.resolve_model("gpt-4", models_config)
    assert cfg.model == "gpt-4"
    assert cfg.provider == "openai"


def test_resolve_model_finds_by_alias() -> None:
    client = LLMClient.__new__(LLMClient)
    models_config = {
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [
                    {"name": "gpt-4-turbo", "aliases": ["gpt4", "gpt-4t"], "supports_tools": True}
                ],
            }
        }
    }
    cfg = client.resolve_model("gpt4", models_config)
    assert cfg.model == "gpt-4-turbo"


def test_resolve_model_raises_for_unknown_model() -> None:
    client = LLMClient.__new__(LLMClient)
    with pytest.raises(ValueError, match="not found"):
        client.resolve_model("nonexistent", {})


def test_resolve_model_reads_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "secret123")
    client = LLMClient.__new__(LLMClient)
    models_config = {
        "providers": {
            "test": {
                "base_url": "https://test.api",
                "api_key_env": "MY_API_KEY",
                "format": "openai",
                "models": [{"name": "test-model"}],
            }
        }
    }
    cfg = client.resolve_model("test-model", models_config)
    assert cfg.api_key == "secret123"


def test_resolve_model_uses_api_model_field() -> None:
    """When api_model differs from name, the ModelConfig.model should use api_model."""
    client = LLMClient.__new__(LLMClient)
    models_config = {
        "providers": {
            "p": {
                "base_url": "https://x.com",
                "api_key_env": "K",
                "format": "openai",
                "models": [{"name": "friendly-name", "api_model": "vendor-id-v2"}],
            }
        }
    }
    cfg = client.resolve_model("friendly-name", models_config)
    assert cfg.model == "vendor-id-v2"


def test_resolve_model_prefers_provider_with_api_key(monkeypatch) -> None:
    # Mirror of the "user reuses a bundled model name" scenario:
    # bundled provider declares gpt-5.4 with OPENAI_API_KEY (unset),
    # user's proxy provider declares the same name with USER_KEY (set).
    # resolve_model must pick the user's provider so outbound headers carry
    # a real Bearer token instead of an empty string.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("USER_KEY", "sk-user-real")
    client = LLMClient.__new__(LLMClient)
    models_config = {
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [{"name": "gpt-5.4"}],
            },
            "proxy": {
                "base_url": "https://proxy.example/v1",
                "api_key_env": "USER_KEY",
                "format": "openai",
                "models": [{"name": "gpt-5.4"}],
            },
        }
    }
    cfg = client.resolve_model("gpt-5.4", models_config)
    assert cfg.provider == "proxy"
    assert cfg.api_key == "sk-user-real"
    assert cfg.base_url == "https://proxy.example/v1"


def test_resolve_model_falls_back_to_first_match_when_no_keys(monkeypatch) -> None:
    # When nobody has credentials, still return the first match so upstream
    # layers can surface a meaningful missing-credentials error.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    client = LLMClient.__new__(LLMClient)
    models_config = {
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [{"name": "gpt-5.4"}],
            },
            "other": {
                "base_url": "https://other.example/v1",
                "api_key_env": "OTHER_KEY",
                "format": "openai",
                "models": [{"name": "gpt-5.4"}],
            },
        }
    }
    cfg = client.resolve_model("gpt-5.4", models_config)
    assert cfg.provider == "openai"
    assert cfg.api_key == ""


# ── complete — raises when tools + model doesn't support tools ────────────────


@pytest.mark.asyncio
async def test_complete_raises_when_tools_not_supported() -> None:
    client = LLMClient.__new__(LLMClient)
    cfg = _cfg(supports_tools=False)
    tool = MagicMock()
    with pytest.raises(RuntimeError, match="does not support tool calling"):
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            tools=[tool],
            model_config=cfg,
        )


# ── _complete_openai ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_openai_format_posts_to_chat_completions() -> None:
    client = LLMClient.__new__(LLMClient)
    mock_http = AsyncMock()
    resp = _mock_http_response(data=_openai_response("hello"))
    mock_http.post = AsyncMock(return_value=resp)
    client._http = mock_http

    result = await client.complete(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model_config=_cfg(),
    )

    assert "hello" in result.text
    posted_url = mock_http.post.call_args[0][0]
    assert "chat/completions" in posted_url


@pytest.mark.asyncio
async def test_complete_openai_returns_tool_use_block() -> None:
    client = LLMClient.__new__(LLMClient)
    mock_http = AsyncMock()
    tool_call_data = {
        "id": "tc1",
        "type": "function",
        "function": {"name": "Bash", "arguments": json.dumps({"command": "ls"})},
    }
    resp = _mock_http_response(data=_openai_response("", [tool_call_data]))
    mock_http.post = AsyncMock(return_value=resp)
    client._http = mock_http

    result = await client.complete(
        messages=[{"role": "user", "content": "run ls"}],
        tools=[],
        model_config=_cfg(),
    )

    assert result.has_tool_calls
    assert result.tool_calls[0].name == "Bash"


# ── _complete_anthropic ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_anthropic_posts_to_v1_messages() -> None:
    client = LLMClient.__new__(LLMClient)
    mock_http = AsyncMock()
    resp = _mock_http_response(data=_anthropic_response())
    mock_http.post = AsyncMock(return_value=resp)
    client._http = mock_http

    result = await client.complete(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model_config=_anthropic_cfg(),
    )

    posted_url = mock_http.post.call_args[0][0]
    assert "/v1/messages" in posted_url
    assert "hi" in result.text or result.text == "hi"


@pytest.mark.asyncio
async def test_complete_anthropic_raises_on_non_200() -> None:
    client = LLMClient.__new__(LLMClient)
    mock_http = AsyncMock()
    resp = _mock_http_response(status=429, text="rate limited")
    mock_http.post = AsyncMock(return_value=resp)
    client._http = mock_http

    with pytest.raises(RuntimeError, match="429"):
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            model_config=_anthropic_cfg(),
        )


# ── _claude_to_openai_messages ────────────────────────────────────────────────


def test_claude_to_openai_messages_simple_text() -> None:
    client = LLMClient.__new__(LLMClient)
    result = client._claude_to_openai_messages(
        [{"role": "user", "content": "hello"}], system_prompt=""
    )
    assert result == [{"role": "user", "content": "hello"}]


def test_claude_to_openai_messages_prepends_system() -> None:
    client = LLMClient.__new__(LLMClient)
    result = client._claude_to_openai_messages([], system_prompt="You are helpful.")
    assert result[0] == {"role": "system", "content": "You are helpful."}


def test_claude_to_openai_messages_converts_tool_use_block() -> None:
    client = LLMClient.__new__(LLMClient)
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ],
        }
    ]
    result = client._claude_to_openai_messages(messages, "")
    assert result[0]["role"] == "assistant"
    assert "tool_calls" in result[0]
    assert result[0]["tool_calls"][0]["function"]["name"] == "Bash"


def test_claude_to_openai_messages_converts_tool_result() -> None:
    client = LLMClient.__new__(LLMClient)
    messages = [
        {
            "role": "tool",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "done"},
            ],
        }
    ]
    result = client._claude_to_openai_messages(messages, "")
    assert result[0]["role"] == "tool"
    assert result[0]["tool_call_id"] == "t1"


# ── _openai_response_to_claude ────────────────────────────────────────────────


def test_openai_response_to_claude_text_only() -> None:
    client = LLMClient.__new__(LLMClient)
    data = _openai_response("text only")
    resp = client._openai_response_to_claude(data)
    assert resp.text == "text only"
    assert resp.stop_reason == "stop"


def test_openai_response_to_claude_with_tool_calls() -> None:
    client = LLMClient.__new__(LLMClient)
    data = _openai_response(
        "",
        [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": '{"path": "/f"}'}}],
    )
    resp = client._openai_response_to_claude(data)
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "Read"


def test_openai_response_to_claude_invalid_json_args() -> None:
    """Malformed JSON arguments should not raise; raw value stored."""
    client = LLMClient.__new__(LLMClient)
    data = _openai_response(
        "",
        [{"id": "tc2", "type": "function", "function": {"name": "X", "arguments": "{{bad}}"}}],
    )
    resp = client._openai_response_to_claude(data)
    assert resp.has_tool_calls
    assert "raw" in resp.tool_calls[0].input


# ── close ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_calls_http_aclose() -> None:
    client = LLMClient.__new__(LLMClient)
    mock_http = AsyncMock()
    client._http = mock_http

    await client.close()
    mock_http.aclose.assert_awaited_once()
