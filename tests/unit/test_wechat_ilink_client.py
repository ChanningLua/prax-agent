"""Unit tests for prax.integrations.wechat_ilink.client.

All HTTP calls are mocked via ``httpx.MockTransport``. No real iLink
traffic, no QR scanning — those exercise the same code paths but require
a logged-in user, and live as the manual `prax wechat login` smoke.
"""

from __future__ import annotations

import json

import httpx
import pytest

from prax.integrations.wechat_ilink import client as wx_client
from prax.integrations.wechat_ilink.client import (
    EP_SEND_MESSAGE,
    _build_message,
    _headers,
    send_text,
)
from prax.integrations.wechat_ilink.store import AccountRecord


def _account(**overrides) -> AccountRecord:
    base = {
        "account_id": "ilink_test",
        "token": "test-bot-token",
        "base_url": "https://ilinkai.weixin.qq.com",
        "user_id": "u_self",
        "saved_at": "2026-04-25T00:00:00Z",
    }
    base.update(overrides)
    return AccountRecord(**base)


# ── header signing ──────────────────────────────────────────────────────────


def test_headers_set_required_ilink_fields():
    body = '{"hello":"world"}'
    h = _headers("bot-tok", body)
    assert h["AuthorizationType"] == "ilink_bot_token"
    assert h["Authorization"] == "Bearer bot-tok"
    assert h["Content-Type"] == "application/json"
    assert h["Content-Length"] == str(len(body.encode("utf-8")))
    assert h["iLink-App-Id"] == "bot"
    # X-WECHAT-UIN is a base64-encoded random number; just sanity check shape
    assert h["X-WECHAT-UIN"]


def test_headers_omit_authorization_when_token_missing():
    h = _headers(None, "{}")
    assert "Authorization" not in h
    assert h["AuthorizationType"] == "ilink_bot_token"


# ── _build_message ──────────────────────────────────────────────────────────


def test_build_message_shape_matches_ilink_contract():
    msg = _build_message(to_user_id="u_target", text="hello", client_id="cid-1")
    assert msg["to_user_id"] == "u_target"
    assert msg["client_id"] == "cid-1"
    assert msg["from_user_id"] == ""
    assert msg["message_type"] == 2  # MSG_TYPE_BOT
    assert msg["message_state"] == 2  # MSG_STATE_FINISH
    assert msg["item_list"] == [{"type": 1, "text_item": {"text": "hello"}}]


# ── send_text ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_text_posts_to_sendmessage_with_token():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ret": 0})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await send_text(
            _account(),
            to_user_id="u_recipient",
            text="ping",
            http_client=client,
        )

    assert len(captured) == 1
    req = captured[0]
    assert req.url.path.endswith(f"/{EP_SEND_MESSAGE}")
    assert req.headers["Authorization"] == "Bearer test-bot-token"
    payload = json.loads(req.content.decode("utf-8"))
    assert payload["msg"]["to_user_id"] == "u_recipient"
    assert payload["msg"]["item_list"][0]["text_item"]["text"] == "ping"
    # base_info is appended to every payload at _api_post layer
    assert "base_info" in payload


@pytest.mark.asyncio
async def test_send_text_raises_on_http_error():
    def handler(request):
        return httpx.Response(500, text="server boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await send_text(
                _account(),
                to_user_id="u_recipient",
                text="x",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_send_text_raises_when_ret_nonzero():
    """iLink returns 200 with ret/errcode in body when the call is rejected."""
    def handler(request):
        return httpx.Response(200, json={"ret": -14, "errmsg": "session expired"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="ret=-14"):
            await send_text(
                _account(),
                to_user_id="u_recipient",
                text="x",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_send_text_translates_ret_minus_2_to_friendly_hint():
    """ret=-2 means iLink has no chat context with the recipient — the raw
    error 'ret=-2 errmsg=unknown' is unactionable. Surface a Chinese hint
    that tells the user to send the bot a message in WeChat first."""
    def handler(request):
        return httpx.Response(200, json={"ret": -2})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="ret=-2") as exc_info:
            await send_text(
                _account(),
                to_user_id="u_no_context",
                text="x",
                http_client=client,
            )
        msg = str(exc_info.value)
        # The actionable bits the user must see.
        assert "会话上下文" in msg
        assert "微信" in msg
        assert "先发一句话" in msg


@pytest.mark.asyncio
async def test_send_text_raises_when_errcode_nonzero():
    def handler(request):
        return httpx.Response(200, json={"errcode": -99, "errmsg": "bad"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="errcode=-99"):
            await send_text(
                _account(),
                to_user_id="u",
                text="x",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_send_text_rejects_empty_text():
    with pytest.raises(ValueError, match="must not be empty"):
        await send_text(_account(), to_user_id="u", text="   ")


@pytest.mark.asyncio
async def test_send_text_rejects_empty_recipient():
    with pytest.raises(ValueError, match="to_user_id is required"):
        await send_text(_account(), to_user_id="", text="hi")
