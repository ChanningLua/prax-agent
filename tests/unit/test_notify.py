"""Tests for NotifyTool and load_notify_config."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from prax.core.config_files import load_notify_config
from prax.tools.notify import (
    FeishuWebhookProvider,
    LarkWebhookProvider,
    NotifyTool,
    SmtpProvider,
    WechatWorkWebhookProvider,
    build_provider,
)


# ── load_notify_config ───────────────────────────────────────────────────────


def test_load_notify_config_missing_returns_empty(tmp_path):
    assert load_notify_config(str(tmp_path)) == {"channels": {}}


def test_load_notify_config_empty_yaml_does_not_crash(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "notify.yaml").write_text("")
    assert load_notify_config(str(tmp_path)) == {"channels": {}}


def test_load_notify_config_comment_only_returns_empty(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "notify.yaml").write_text("# nothing here\n")
    assert load_notify_config(str(tmp_path)) == {"channels": {}}


def test_load_notify_config_reads_channels(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "notify.yaml").write_text(
        "channels:\n"
        "  daily:\n"
        "    provider: feishu_webhook\n"
        "    url: https://open.feishu.cn/hook/abc\n"
    )
    cfg = load_notify_config(str(tmp_path))
    assert cfg == {
        "channels": {
            "daily": {
                "provider": "feishu_webhook",
                "url": "https://open.feishu.cn/hook/abc",
            }
        }
    }


def test_load_notify_config_project_overrides_user(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".prax").mkdir()
    (home / ".prax" / "notify.yaml").write_text(
        "channels:\n  daily:\n    provider: feishu_webhook\n    url: user-url\n"
    )

    project = tmp_path / "project"
    project.mkdir()
    (project / ".prax").mkdir()
    (project / ".prax" / "notify.yaml").write_text(
        "channels:\n  daily:\n    provider: lark_webhook\n    url: project-url\n"
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    cfg = load_notify_config(str(project))
    assert cfg["channels"]["daily"]["provider"] == "lark_webhook"
    assert cfg["channels"]["daily"]["url"] == "project-url"


# ── build_provider ───────────────────────────────────────────────────────────


def test_build_provider_feishu():
    p = build_provider({
        "provider": "feishu_webhook",
        "url": "https://example.com/hook",
        "default_title_prefix": "[Prax] ",
    })
    assert isinstance(p, FeishuWebhookProvider)


def test_build_provider_lark():
    p = build_provider({"provider": "lark_webhook", "url": "https://example.com/hook"})
    assert isinstance(p, LarkWebhookProvider)


def test_build_provider_smtp():
    p = build_provider({
        "provider": "smtp",
        "host": "smtp.example.com",
        "port": 587,
        "from": "me@example.com",
        "to": ["me@example.com"],
        "auth_env": "PRAX_SMTP_PASSWORD",
    })
    assert isinstance(p, SmtpProvider)


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown notify provider"):
        build_provider({"provider": "carrier_pigeon"})


def test_build_provider_wechat_work():
    p = build_provider({
        "provider": "wechat_work_webhook",
        "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        "default_title_prefix": "[Prax] ",
    })
    assert isinstance(p, WechatWorkWebhookProvider)


def test_build_provider_wechat_work_missing_url_raises():
    with pytest.raises(ValueError, match="missing url"):
        build_provider({"provider": "wechat_work_webhook"})


def test_build_provider_expands_env_var_in_url(monkeypatch):
    monkeypatch.setenv("FEISHU_HOOK", "https://real.url/x")
    p = build_provider({"provider": "feishu_webhook", "url": "${FEISHU_HOOK}"})
    assert isinstance(p, FeishuWebhookProvider)
    assert p._url == "https://real.url/x"


def test_build_provider_missing_url_raises():
    with pytest.raises(ValueError, match="missing url"):
        build_provider({"provider": "feishu_webhook"})


# ── FeishuWebhookProvider payload shape ──────────────────────────────────────


@pytest.mark.asyncio
async def test_feishu_provider_posts_interactive_card():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": 0})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = FeishuWebhookProvider(
            url="https://open.feishu.cn/hook/abc",
            title_prefix="[Prax] ",
            http_client=client,
        )
        await provider.send(title="今日简报", body="**hello**", level="info")

    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/hook/abc"
    import json as _json
    payload = _json.loads(req.content)
    assert payload["msg_type"] == "interactive"
    card = payload["card"]
    assert card["header"]["title"]["content"] == "[Prax] 今日简报"
    assert card["header"]["template"] == "blue"  # info → blue
    assert any(e.get("content") == "**hello**" for e in card["elements"])


@pytest.mark.asyncio
async def test_feishu_provider_maps_level_to_template():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": 0})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = FeishuWebhookProvider(url="https://x/y", http_client=client)
        await provider.send(title="t", body="b", level="error")

    import json as _json
    payload = _json.loads(captured[0].content)
    assert payload["card"]["header"]["template"] == "red"


@pytest.mark.asyncio
async def test_feishu_provider_raises_on_http_error():
    def handler(request):
        return httpx.Response(500, json={"code": 99})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = FeishuWebhookProvider(url="https://x/y", http_client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.send(title="t", body="b", level="info")


# ── WechatWorkWebhookProvider (企业微信群机器人) ─────────────────────────────


@pytest.mark.asyncio
async def test_wechat_work_provider_posts_markdown():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WechatWorkWebhookProvider(
            url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
            title_prefix="[Prax] ",
            http_client=client,
        )
        await provider.send(title="今日简报", body="hello world", level="info")

    assert len(captured) == 1
    import json as _json
    payload = _json.loads(captured[0].content)
    assert payload["msgtype"] == "markdown"
    content = payload["markdown"]["content"]
    assert "[Prax] 今日简报" in content
    assert "hello world" in content
    # info level → "info" colour tag
    assert 'color="info"' in content


@pytest.mark.asyncio
async def test_wechat_work_provider_maps_error_level_to_warning():
    captured: list[httpx.Request] = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"errcode": 0})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WechatWorkWebhookProvider(url="https://x/y", http_client=client)
        await provider.send(title="t", body="b", level="error")

    import json as _json
    content = _json.loads(captured[0].content)["markdown"]["content"]
    # WeCom only supports info|comment|warning colours; error → warning
    assert 'color="warning"' in content


@pytest.mark.asyncio
async def test_wechat_work_provider_raises_on_errcode():
    # WeCom returns 200 even on logical errors — the real signal is in body.
    def handler(request):
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook key"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WechatWorkWebhookProvider(url="https://x/y", http_client=client)
        with pytest.raises(RuntimeError, match="errcode=93000"):
            await provider.send(title="t", body="b", level="info")


@pytest.mark.asyncio
async def test_wechat_work_provider_raises_on_http_error():
    def handler(request):
        return httpx.Response(500, text="bad gateway")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WechatWorkWebhookProvider(url="https://x/y", http_client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.send(title="t", body="b", level="info")


# ── SmtpProvider ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smtp_provider_raises_when_env_missing(monkeypatch):
    monkeypatch.delenv("PRAX_TEST_SMTP_PASSWORD", raising=False)
    smtp_cls = MagicMock()
    provider = SmtpProvider(
        host="smtp.example.com",
        port=587,
        from_addr="me@x.com",
        to_addrs=["you@x.com"],
        auth_env="PRAX_TEST_SMTP_PASSWORD",
        smtp_factory=smtp_cls,
    )
    with pytest.raises(RuntimeError, match="PRAX_TEST_SMTP_PASSWORD"):
        await provider.send(title="t", body="b", level="info")
    smtp_cls.assert_not_called()


@pytest.mark.asyncio
async def test_smtp_provider_sends_when_env_present(monkeypatch):
    monkeypatch.setenv("PRAX_TEST_SMTP_PASSWORD", "secret")
    smtp_instance = MagicMock()
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)
    smtp_cls = MagicMock(return_value=smtp_instance)

    provider = SmtpProvider(
        host="smtp.example.com",
        port=587,
        from_addr="me@x.com",
        to_addrs=["you@x.com", "team@x.com"],
        auth_env="PRAX_TEST_SMTP_PASSWORD",
        smtp_factory=smtp_cls,
    )
    await provider.send(title="hello", body="world", level="info")

    smtp_cls.assert_called_once_with("smtp.example.com", 587)
    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("me@x.com", "secret")
    smtp_instance.sendmail.assert_called_once()
    from_arg, to_arg, msg_arg = smtp_instance.sendmail.call_args[0]
    assert from_arg == "me@x.com"
    assert to_arg == ["you@x.com", "team@x.com"]
    assert "Subject: hello" in msg_arg
    # Body is MIME-encoded; decode the text/plain part to check content.
    from email import message_from_string
    parsed = message_from_string(msg_arg)
    decoded_bodies = [
        part.get_payload(decode=True).decode("utf-8")
        for part in parsed.walk()
        if part.get_content_type() == "text/plain"
    ]
    assert any("world" in b for b in decoded_bodies)


# ── NotifyTool.execute ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_tool_happy_path(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "notify.yaml").write_text(
        "channels:\n  daily:\n    provider: feishu_webhook\n    url: https://x.y/z\n"
    )

    sent = []

    class DummyProvider:
        async def send(self, **kw):
            sent.append(kw)

    tool = NotifyTool(cwd=str(tmp_path), provider_factory=lambda cfg: DummyProvider())
    result = await tool.execute({"channel": "daily", "title": "t", "body": "b"})

    assert not result.is_error
    assert "daily" in result.content
    assert sent == [{"title": "t", "body": "b", "level": "info"}]


@pytest.mark.asyncio
async def test_notify_tool_unknown_channel(tmp_path):
    tool = NotifyTool(cwd=str(tmp_path))
    result = await tool.execute({"channel": "ghost", "title": "t", "body": "b"})
    assert result.is_error
    assert "ghost" in result.content


@pytest.mark.asyncio
async def test_notify_tool_provider_error_is_reported(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "notify.yaml").write_text(
        "channels:\n  daily:\n    provider: feishu_webhook\n    url: https://x.y/z\n"
    )

    class BoomProvider:
        async def send(self, **kw):
            raise RuntimeError("network down")

    tool = NotifyTool(cwd=str(tmp_path), provider_factory=lambda cfg: BoomProvider())
    result = await tool.execute({"channel": "daily", "title": "t", "body": "b"})
    assert result.is_error
    assert "network down" in result.content


@pytest.mark.asyncio
async def test_notify_tool_passes_level(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "notify.yaml").write_text(
        "channels:\n  alert:\n    provider: feishu_webhook\n    url: https://x.y/z\n"
    )

    calls = []

    class DummyProvider:
        async def send(self, **kw):
            calls.append(kw["level"])

    tool = NotifyTool(cwd=str(tmp_path), provider_factory=lambda cfg: DummyProvider())
    await tool.execute({"channel": "alert", "title": "t", "body": "b", "level": "error"})
    assert calls == ["error"]


def test_notify_tool_schema_rejects_extra_fields():
    tool = NotifyTool(cwd="/tmp")
    from prax.tools.base import ToolInputValidationError
    with pytest.raises(ToolInputValidationError):
        tool.validate_params({
            "channel": "x", "title": "t", "body": "b", "surprise": "!",
        })


def test_notify_tool_schema_requires_channel_title_body():
    tool = NotifyTool(cwd="/tmp")
    from prax.tools.base import ToolInputValidationError
    for missing in ("channel", "title", "body"):
        params = {"channel": "x", "title": "t", "body": "b"}
        params.pop(missing)
        with pytest.raises(ToolInputValidationError):
            tool.validate_params(params)


def test_notify_tool_permission_is_review():
    from prax.tools.base import PermissionLevel
    tool = NotifyTool(cwd="/tmp")
    assert tool.permission_level == PermissionLevel.REVIEW
