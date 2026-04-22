"""Regression test: LLMClient._stream_openai must not silently accept a
non-SSE response body.

Proxies (Claude Relay Service, nginx gateways, etc.) sometimes return
HTTP 200 with a plain JSON error body instead of an event-stream during
maintenance, auth failures, or rate limits. Without the content-type
guard the agent loop gets an empty LLMResponse and exits to the user
with no visible output.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from prax.core.llm_client import LLMClient, ModelConfig


def _cfg() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model="gpt-test",
        base_url="https://proxy.test/v1",
        api_key="test-key",
        api_format="openai",
        request_mode="chat_completions",
    )


@pytest.mark.asyncio
async def test_raises_runtime_error_on_json_maintenance_body_with_200():
    """Proxy returns 200 + JSON body like {"success": false, "message": "...维护中..."}
    — guard must surface the message instead of silently swallowing it."""
    maintenance_payload = b'{"success": false, "message": "\xe2\x9a\xa0\xef\xb8\x8f server under maintenance"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=maintenance_payload,
            headers={"content-type": "application/json"},
        )

    client = LLMClient()
    try:
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(RuntimeError, match="non-stream response"):
            async for _ in client._stream_openai(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                cfg=_cfg(),
                system_prompt="",
                max_tokens=10,
                temperature=0.0,
            ):
                pass
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_error_message_includes_upstream_detail():
    """Surface the upstream 'message' field verbatim so the user can
    diagnose the root cause instead of guessing."""
    payload = b'{"error": {"message": "rate limit exceeded, retry in 60s"}}'

    def handler(request):
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/json"},
        )

    client = LLMClient()
    try:
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(RuntimeError) as exc_info:
            async for _ in client._stream_openai(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                cfg=_cfg(),
                system_prompt="",
                max_tokens=10,
                temperature=0.0,
            ):
                pass
    finally:
        await client.close()

    assert "rate limit exceeded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_raises_runtime_error_on_html_gateway_error():
    """nginx/gateway errors often arrive as HTML with 200 or 502 — guard
    must refuse to proceed rather than stream-parse garbage."""
    html = b"<html><body><h1>502 Bad Gateway</h1></body></html>"

    def handler(request):
        return httpx.Response(
            200,
            content=html,
            headers={"content-type": "text/html"},
        )

    client = LLMClient()
    try:
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(RuntimeError, match="non-stream response"):
            async for _ in client._stream_openai(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                cfg=_cfg(),
                system_prompt="",
                max_tokens=10,
                temperature=0.0,
            ):
                pass
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_valid_sse_stream_still_works():
    """Regression: the guard must NOT block a legitimate event-stream."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Minimal valid SSE response with one content chunk + [DONE].
        sse = (
            'data: {"choices":[{"delta":{"content":"Hi"},"index":0,"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{},"index":0,"finish_reason":"stop"}]}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(
            200,
            content=sse.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    client = LLMClient()
    chunks: list = []
    try:
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async for c in client._stream_openai(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            cfg=_cfg(),
            system_prompt="",
            max_tokens=10,
            temperature=0.0,
        ):
            chunks.append(c)
    finally:
        await client.close()

    # Expect at least the "Hi" text chunk (string) and the final LLMResponse object.
    assert any(c == "Hi" for c in chunks), f"expected 'Hi' chunk, got {chunks}"
    from prax.core.llm_client import LLMResponse
    assert any(isinstance(c, LLMResponse) for c in chunks), "expected final LLMResponse yield"
