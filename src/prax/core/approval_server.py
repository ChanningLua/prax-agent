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


def serve(cwd: str, *, port: int = 7879) -> None:  # pragma: no cover - socket glue
    import time

    gate = ApprovalGate(cwd)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            status, body = route_approval(parsed.path, q, gate=gate, now=time.time())
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args: Any) -> None:  # keep it quiet
            pass

    HTTPServer(("0.0.0.0", port), _Handler).serve_forever()
