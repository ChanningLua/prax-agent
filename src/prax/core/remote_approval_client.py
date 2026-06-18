"""Remote approval client — the agent talks OUTBOUND to a public approval relay.

The agent (behind NAT, e.g. on a laptop) parks an approval on a public relay
server (``POST /request``) and polls it (``GET /status/<id>``); the human taps
the relay's public link from their phone on ANY network. Mirrors
:func:`prax.core.remote_approval.check_or_request` but over HTTP, so the approve
link is publicly reachable while the agent never needs to be.

The HTTP call is injectable (``http=``) so the client logic is unit-testable
without sockets.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Callable

Notifier = Callable[[str, str], None]


def _default_http(method: str, url: str, *, body=None, timeout: int = 10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    # bypass any env HTTP_PROXY — the relay is reached directly
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


def remote_status(server_url, admin_token, approval_id, *, http=_default_http):
    server = server_url.rstrip("/")
    _, body = http("GET", f"{server}/status/{approval_id}?k={admin_token}")
    return json.loads(body).get("status")


def remote_request(
    server_url, admin_token, approval_id, instruction, *, deadline_at=None, http=_default_http
):
    server = server_url.rstrip("/")
    _, body = http(
        "POST",
        f"{server}/request",
        body={
            "id": approval_id,
            "instruction": instruction,
            "k": admin_token,
            "deadline_at": deadline_at,
        },
    )
    token = json.loads(body)["token"]
    approve = f"{server}/a/{approval_id}?t={token}&d=approve"
    reject = f"{server}/a/{approval_id}?t={token}&d=reject"
    return token, approve, reject


def remote_check_or_request(
    server_url,
    admin_token,
    approval_id,
    instruction,
    *,
    notify: Notifier | None = None,
    deadline_at=None,
    http=_default_http,
) -> str:
    """Non-blocking park-and-continue over HTTP (relay version).

    First call: parks on the relay + notifies with tap links, returns
    ``"pending"``. Later calls: poll the relay and return its status. The agent
    proceeds only on ``approved`` and keeps working on ``pending``.
    """
    status = remote_status(server_url, admin_token, approval_id, http=http)
    if status is None:  # not on the relay yet → park it
        _, approve, reject = remote_request(
            server_url, admin_token, approval_id, instruction, deadline_at=deadline_at, http=http
        )
        if notify is not None:
            notify(
                f"[审批] {approval_id}",
                f"需批准：{instruction}\n✅ 批准: {approve}\n❌ 拒绝: {reject}",
            )
        return "pending"
    return status
