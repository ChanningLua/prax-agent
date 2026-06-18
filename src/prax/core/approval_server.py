"""Minimal remote-approval HTTP endpoint — the inbound half of "notify + approve".

Flow: the agent parks a risky step (``ApprovalGate.request`` → token), pushes a
notification carrying approve/reject links, and KEEPS RUNNING. When you tap a
link from your phone it hits this endpoint, which resolves the gate; the agent
picks the decision up on its next cycle. The endpoint never blocks the loop.

Zero extra deps — stdlib ``http.server``. The routing logic (:func:`route_approval`)
is pure and unit-tested; :func:`serve` is the thin socket glue.

Link shape: ``GET /a/<id>?t=<token>&d=approve|reject``
"""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .approval_gate import APPROVED, REJECTED, ApprovalGate


def route_approval(
    path: str,
    query: dict[str, str],
    *,
    gate: ApprovalGate,
    now: float,
) -> "tuple[int, str]":
    """Resolve an approval from a tapped link. Returns (http_status, html/text)."""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) != 2 or parts[0] != "a":
        return 404, "not found"
    approval_id = parts[1]

    decision = query.get("d", "")
    if decision not in ("approve", "reject"):
        return 400, "missing or invalid 'd' (approve|reject)"
    if gate.get(approval_id) is None:
        return 404, f"unknown approval {approval_id}"
    if not gate.check_token(approval_id, query.get("t", "")):
        return 403, "bad token"

    result = APPROVED if decision == "approve" else REJECTED
    gate.resolve(approval_id, result, resolved_at=now)
    label = "已批准" if decision == "approve" else "已拒绝"
    return 200, f"<!doctype html><meta charset=utf-8><h3>{label}：{approval_id}</h3>"


def _admin_ok(query: dict, body: Any, admin_token: str) -> bool:
    """Agent endpoints require the shared admin token (in ?k= or body 'k')."""
    if not admin_token:  # none configured → open (local/dev)
        return True
    provided = ""
    if query and query.get("k"):
        provided = query["k"]
    elif isinstance(body, dict) and body.get("k"):
        provided = str(body["k"])
    return secrets.compare_digest(str(provided), admin_token)


def dispatch(
    method: str,
    path: str,
    query: dict,
    body: Any,
    *,
    gate: ApprovalGate,
    now: float,
    admin_token: str = "",
) -> "tuple[int, str]":
    """HTTP router for the approval relay.

    - ``GET /a/<id>?t=&d=``     phone tap (per-approval token)         → resolve
    - ``POST /request``        agent parks {id,instruction} (admin)   → {token}
    - ``GET /status/<id>``     agent polls (admin)                    → {status}
    """
    if method == "GET" and (path == "/a" or path.startswith("/a/")):
        return route_approval(path, query, gate=gate, now=now)

    if not _admin_ok(query, body, admin_token):
        return 401, "unauthorized"

    if method == "POST" and path == "/request":
        data = body if isinstance(body, dict) else {}
        approval_id = str(data.get("id", "")).strip()
        instruction = str(data.get("instruction", "")).strip()
        if not approval_id or not instruction:
            return 400, "missing id or instruction"
        req = gate.request(
            approval_id, instruction, created_at=now, deadline_at=data.get("deadline_at")
        )
        return 200, json.dumps({"id": approval_id, "token": req.token})

    if method == "GET" and path.startswith("/status/"):
        approval_id = path[len("/status/") :]
        return 200, json.dumps({"id": approval_id, "status": gate.status(approval_id, now=now)})

    return 404, "not found"


def serve(cwd: str, *, port: int = 7879, admin_token: str = "") -> None:  # pragma: no cover - socket glue
    import time

    gate = ApprovalGate(cwd)

    class _Handler(BaseHTTPRequestHandler):
        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            body: Any = None
            if method == "POST":
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except Exception:
                    body = {}
            status, content = dispatch(
                method, parsed.path, q, body, gate=gate, now=time.time(), admin_token=admin_token
            )
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def log_message(self, *args: Any) -> None:  # keep it quiet
            pass

    HTTPServer(("0.0.0.0", port), _Handler).serve_forever()
