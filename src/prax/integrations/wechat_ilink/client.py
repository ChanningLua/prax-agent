"""Push-only iLink Bot client.

Adapted from hermes-agent's ``gateway/platforms/weixin.py``
(MIT, 2025 Nous Research). Reproduces the QR-login flow and the
``ilink/bot/sendmessage`` text-send path with three deliberate trims:

1. Uses ``httpx`` instead of ``aiohttp`` (Prax already depends on httpx).
2. Drops the long-poll ``getupdates`` loop, media encryption, typing
   tickets, and context-token cache — none are needed for push-only.
3. Falls back gracefully when the optional ``qrcode`` package isn't
   installed (just prints the scan URL instead of an ASCII QR).

The endpoint constants and request shape come straight from Hermes — they
are reverse-engineered from the official iLink API and are stable enough
that Hermes's docs treat them as a contract.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .store import AccountRecord, save_account

logger = logging.getLogger(__name__)


# ── iLink protocol constants (verbatim from Hermes) ──────────────────────────

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0  # = 131584

QR_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
SESSION_EXPIRED_ERRCODE = -14

ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"


# ── Header / signing ─────────────────────────────────────────────────────────


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _base_info() -> dict[str, Any]:
    return {"channel_version": CHANNEL_VERSION}


def _headers(token: str | None, body: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ── Low-level API helpers ────────────────────────────────────────────────────


async def _api_get(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    endpoint: str,
    timeout_ms: int = QR_TIMEOUT_MS,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint}"
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    timeout = timeout_ms / 1000
    resp = await client.get(url, headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"iLink GET {endpoint} HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


async def _api_post(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str | None,
    timeout_ms: int = API_TIMEOUT_MS,
) -> dict[str, Any]:
    body = json.dumps({**payload, "base_info": _base_info()}, ensure_ascii=False)
    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = timeout_ms / 1000
    resp = await client.post(
        url,
        content=body.encode("utf-8"),
        headers=_headers(token, body),
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"iLink POST {endpoint} HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


# ── QR login ─────────────────────────────────────────────────────────────────


@dataclass
class QrLoginResult:
    account_id: str
    token: str
    base_url: str
    user_id: str


def _render_qrcode_to_terminal(scan_data: str) -> bool:
    """Try the optional ``qrcode`` package; return True iff it rendered."""
    try:
        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        qr = qrcode.QRCode()
        qr.add_data(scan_data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        return True
    except Exception as exc:
        logger.debug("wechat_ilink: ASCII QR render failed: %s", exc)
        return False


async def qr_login(
    *,
    bot_type: str = "3",
    timeout_seconds: int = 480,
    prax_home: Path | None = None,
    on_status: Any = None,
) -> QrLoginResult | None:
    """Run the interactive iLink QR login flow.

    Prints the scan URL (and an ASCII QR if ``qrcode`` is installed),
    polls until the user confirms in WeChat, then writes credentials to
    ``~/.prax/wechat/<account_id>.json``. Returns ``None`` on timeout or
    error.

    ``on_status`` is an optional callable ``fn(status: str, info: dict) ->
    None`` that fires for each poll tick — useful for a future GUI to
    show "waiting / scanned / confirmed" without re-implementing the
    underlying flow.
    """
    async with httpx.AsyncClient(trust_env=True) as client:
        try:
            qr_resp = await _api_get(
                client,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
            )
        except Exception as exc:
            logger.error("wechat_ilink: failed to fetch QR code: %s", exc)
            return None

        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            logger.error("wechat_ilink: QR response missing qrcode token")
            return None

        scan_data = qrcode_url or qrcode_value
        print("\n请使用微信扫描以下二维码（如果终端没渲染，复制链接到浏览器打开后用微信扫描）：")
        if qrcode_url:
            print(qrcode_url)
        if not _render_qrcode_to_terminal(scan_data):
            print("（提示：`pip install qrcode` 后可在终端直接看 ASCII 二维码）")

        deadline = time.time() + timeout_seconds
        current_base_url = ILINK_BASE_URL
        refresh_count = 0

        while time.time() < deadline:
            try:
                status_resp = await _api_get(
                    client,
                    base_url=current_base_url,
                    endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError):
                await asyncio.sleep(1)
                continue
            except Exception as exc:
                logger.warning("wechat_ilink: QR poll error: %s", exc)
                await asyncio.sleep(1)
                continue

            status = str(status_resp.get("status") or "wait")
            if on_status is not None:
                try:
                    on_status(status, status_resp)
                except Exception:
                    pass

            if status == "wait":
                print(".", end="", flush=True)
            elif status == "scaned":
                print("\n已扫码，请在微信里确认...")
            elif status == "scaned_but_redirect":
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
            elif status == "expired":
                refresh_count += 1
                if refresh_count > 3:
                    print("\n二维码多次过期，请重新执行 prax wechat login。")
                    return None
                print(f"\n二维码已过期，正在刷新... ({refresh_count}/3)")
                try:
                    qr_resp = await _api_get(
                        client,
                        base_url=ILINK_BASE_URL,
                        endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                    )
                    qrcode_value = str(qr_resp.get("qrcode") or "")
                    qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
                    scan_data = qrcode_url or qrcode_value
                    if qrcode_url:
                        print(qrcode_url)
                    _render_qrcode_to_terminal(scan_data)
                except Exception as exc:
                    logger.error("wechat_ilink: QR refresh failed: %s", exc)
                    return None
            elif status == "confirmed":
                account_id = str(status_resp.get("ilink_bot_id") or "")
                token = str(status_resp.get("bot_token") or "")
                base_url = str(status_resp.get("baseurl") or ILINK_BASE_URL)
                user_id = str(status_resp.get("ilink_user_id") or "")
                if not account_id or not token:
                    logger.error(
                        "wechat_ilink: QR confirmed but credential payload was incomplete"
                    )
                    return None
                save_account(
                    account_id=account_id,
                    token=token,
                    base_url=base_url,
                    user_id=user_id,
                    prax_home=prax_home,
                )
                print(f"\n微信连接成功！account_id={account_id}")
                if user_id:
                    print(f"你的 user_id: {user_id}  ← 在 notify.yaml 里写 'to: self' 默认就推到这里。")
                return QrLoginResult(
                    account_id=account_id,
                    token=token,
                    base_url=base_url,
                    user_id=user_id,
                )
            await asyncio.sleep(1)

        print("\n微信登录超时。")
        return None


# ── Outbound text send ───────────────────────────────────────────────────────


def _build_message(*, to_user_id: str, text: str, client_id: str) -> dict[str, Any]:
    """Construct the iLink sendmessage payload for a single text chunk."""
    return {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }


async def send_text(
    account: AccountRecord,
    *,
    to_user_id: str,
    text: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send a single text message.

    Raises ``RuntimeError`` if iLink rejects the call (HTTP error or
    non-zero ``ret``/``errcode`` in the response body).
    """
    if not text or not text.strip():
        raise ValueError("send_text: text must not be empty")
    if not to_user_id:
        raise ValueError("send_text: to_user_id is required")

    import uuid as _uuid

    client_id = f"prax-wechat-{_uuid.uuid4().hex}"
    msg = _build_message(to_user_id=to_user_id, text=text, client_id=client_id)
    payload = {"msg": msg}

    own = http_client or httpx.AsyncClient()
    try:
        resp = await _api_post(
            own,
            base_url=account.base_url,
            endpoint=EP_SEND_MESSAGE,
            payload=payload,
            token=account.token,
        )
    finally:
        if http_client is None:
            await own.aclose()

    ret = resp.get("ret")
    errcode = resp.get("errcode")
    bad_ret = ret is not None and ret != 0
    bad_errcode = errcode is not None and errcode != 0
    if bad_ret or bad_errcode:
        errmsg = resp.get("errmsg") or resp.get("msg") or "unknown"
        raise RuntimeError(
            f"iLink sendmessage error: ret={ret} errcode={errcode} errmsg={errmsg!r}"
        )
    return resp
