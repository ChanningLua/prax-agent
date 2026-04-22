"""Multi-provider LLM client with tool call support.

Internally uses Claude message format. Converts to/from OpenAI function_call
format for providers that use the OpenAI-compatible API (GLM, GPT, etc.).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx

from ..tools.base import Tool, ToolCall


@dataclass
class ModelConfig:
    provider: str        # "zhipu" | "openai" | "anthropic"
    model: str           # provider model id
    base_url: str
    api_key: str
    api_format: str      # "openai" | "anthropic"
    config_name: str | None = None
    request_mode: str = "chat_completions"
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning_effort: bool = False
    supports_thinking: bool = False
    default_reasoning_effort: str | None = None
    default_thinking_budget_tokens: int | None = None


@dataclass
class LLMResponse:
    """Unified response in Claude internal format."""
    content: list[dict[str, Any]]  # [{type: "text", text: ...}, {type: "tool_use", ...}]
    stop_reason: str | None = None
    usage: dict[str, int] | None = None

    @property
    def text(self) -> str:
        parts = [b["text"] for b in self.content if b["type"] == "text"]
        return "\n".join(parts)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=b["id"], name=b["name"], input=b["input"])
            for b in self.content
            if b["type"] == "tool_use"
        ]

    @property
    def has_tool_calls(self) -> bool:
        return any(b["type"] == "tool_use" for b in self.content)


class LLMClient:
    """Unified LLM client supporting multiple providers with tool calling."""

    def __init__(self, timeout: float = 120.0):
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self):
        await self._http.aclose()

    def resolve_model(self, model_name: str, models_config: dict) -> ModelConfig:
        """Resolve a model name to its full configuration."""
        for provider_name, provider_cfg in models_config.get("providers", {}).items():
            for m in provider_cfg.get("models", []):
                aliases = [str(alias) for alias in m.get("aliases", [])]
                if m["name"] == model_name or model_name in aliases:
                    # Support multiple env var names for API key
                    api_key_env = provider_cfg.get("api_key_env", "")
                    api_key = ""
                    if isinstance(api_key_env, list):
                        for env_name in api_key_env:
                            api_key = os.environ.get(env_name, "")
                            if api_key:
                                break
                    else:
                        api_key = os.environ.get(api_key_env, "")

                    # Support base_url override from environment
                    base_url_env = provider_cfg.get("base_url_env")
                    base_url = provider_cfg["base_url"]
                    if base_url_env:
                        base_url = os.environ.get(base_url_env, base_url)

                    return ModelConfig(
                        provider=provider_name,
                        model=str(m.get("api_model", m["name"])),
                        config_name=str(m["name"]),
                        base_url=base_url.rstrip("/"),
                        api_key=api_key,
                        api_format=provider_cfg.get("format", "openai"),
                        request_mode=str(m.get("request_mode", "chat_completions")),
                        supports_tools=bool(m.get("supports_tools", True)),
                        supports_streaming=bool(m.get("supports_streaming", True)),
                        supports_reasoning_effort=bool(m.get("supports_reasoning_effort", False)),
                        supports_thinking=bool(m.get("supports_thinking", False)),
                        default_reasoning_effort=(
                            str(m["default_reasoning_effort"])
                            if m.get("default_reasoning_effort") is not None else None
                        ),
                        default_thinking_budget_tokens=(
                            int(m["default_thinking_budget_tokens"])
                            if m.get("default_thinking_budget_tokens") is not None else None
                        ),
                    )
        raise ValueError(f"Model '{model_name}' not found in configuration")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        model_config: ModelConfig,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        thinking_enabled: bool = False,
        reasoning_effort: str | None = None,
        cache_enabled: bool = False,
    ) -> LLMResponse:
        """Send a completion request. Returns response in Claude internal format."""
        if tools and not model_config.supports_tools:
            raise RuntimeError(
                f"Model '{model_config.model}' does not support tool calling."
            )
        if model_config.api_format == "anthropic":
            return await self._complete_anthropic(
                messages, tools, model_config, system_prompt, max_tokens, temperature,
                thinking_enabled, cache_enabled=cache_enabled,
            )
        if model_config.request_mode == "responses":
            return await self._complete_openai_responses(
                messages,
                tools,
                model_config,
                system_prompt,
                temperature,
                reasoning_effort,
            )
        else:
            return await self._complete_openai(
                messages, tools, model_config, system_prompt, max_tokens, temperature, reasoning_effort
            )

    # ── OpenAI-compatible (GLM, GPT, etc.) ──────────────────────────

    async def _complete_openai(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        cfg: ModelConfig,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
    ) -> LLMResponse:
        # Convert messages from Claude format to OpenAI format
        oai_messages = self._claude_to_openai_messages(messages, system_prompt)

        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        effective_reasoning_effort = reasoning_effort or cfg.default_reasoning_effort
        if cfg.supports_reasoning_effort and effective_reasoning_effort:
            body["reasoning_effort"] = effective_reasoning_effort
        if tools:
            body["tools"] = [t.to_openai_format() for t in tools]

        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

        resp = await self._http.post(
            f"{cfg.base_url}/chat/completions",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        return self._openai_response_to_claude(data)

    async def _complete_openai_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        cfg: ModelConfig,
        system_prompt: str,
        temperature: float,
        reasoning_effort: str | None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": cfg.model,
            "input": self._claude_to_responses_input(messages),
            "store": False,
        }
        if system_prompt:
            body["instructions"] = system_prompt
        if cfg.supports_reasoning_effort:
            effort = reasoning_effort or cfg.default_reasoning_effort or "medium"
            body["reasoning"] = {"effort": effort}
        if tools:
            body["tools"] = [self._tool_to_responses_format(t) for t in tools]
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        resp = await self._http.post(
            f"{cfg.base_url}/responses",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return self._responses_to_claude(data)

    def _claude_to_openai_messages(
        self, messages: list[dict[str, Any]], system_prompt: str
    ) -> list[dict[str, Any]]:
        """Convert Claude-format messages to OpenAI format."""
        result: list[dict[str, Any]] = []

        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            # Simple text message
            if isinstance(content, str):
                result.append({"role": role, "content": content})
                continue

            # Content blocks (Claude format)
            if isinstance(content, list):
                # Check if it contains tool_use (assistant message with tool calls)
                tool_uses = [b for b in content if b.get("type") == "tool_use"]
                text_parts = [b["text"] for b in content if b.get("type") == "text"]
                tool_results = [b for b in content if b.get("type") == "tool_result"]

                if tool_uses:
                    # Assistant message with function calls
                    oai_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": "\n".join(text_parts) if text_parts else None,
                        "tool_calls": [
                            {
                                "id": tu["id"],
                                "type": "function",
                                "function": {
                                    "name": tu["name"],
                                    "arguments": json.dumps(tu["input"], ensure_ascii=False),
                                },
                            }
                            for tu in tool_uses
                        ],
                    }
                    result.append(oai_msg)
                elif tool_results:
                    # Tool result messages (one per result in OpenAI format)
                    for tr in tool_results:
                        tr_content = tr.get("content", "")
                        if isinstance(tr_content, list):
                            tr_content = "\n".join(
                                b.get("text", "") for b in tr_content if b.get("type") == "text"
                            )
                        result.append({
                            "role": "tool",
                            "tool_call_id": tr["tool_use_id"],
                            "content": tr_content,
                        })
                else:
                    # Plain text blocks
                    result.append({
                        "role": role,
                        "content": "\n".join(text_parts) if text_parts else "",
                    })

        return result

    def _openai_response_to_claude(self, data: dict) -> LLMResponse:
        """Convert OpenAI response to Claude internal format."""
        choice = data["choices"][0]
        msg = choice["message"]
        content_blocks: list[dict[str, Any]] = []

        if msg.get("content"):
            content_blocks.append({"type": "text", "text": msg["content"]})

        for tc in msg.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args = json.loads(fn["arguments"])
            except json.JSONDecodeError:
                args = {"raw": fn["arguments"]}
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": fn["name"],
                "input": args,
            })

        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        return LLMResponse(
            content=content_blocks,
            stop_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
        )

    def _claude_to_responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                mapped_role = "user" if role == "user" else "assistant"
                items.append({
                    "role": mapped_role,
                    "content": [{"type": "input_text", "text": content}],
                })
                continue
            if not isinstance(content, list):
                continue
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            text_parts = [b["text"] for b in content if b.get("type") == "text"]
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            if text_parts:
                mapped_role = "user" if role == "user" else "assistant"
                items.append({
                    "role": mapped_role,
                    "content": [{"type": "input_text", "text": "\n".join(text_parts)}],
                })
            for tu in tool_uses:
                items.append({
                    "type": "function_call",
                    "call_id": tu["id"],
                    "name": tu["name"],
                    "arguments": json.dumps(tu["input"], ensure_ascii=False),
                })
            for tr in tool_results:
                tr_content = tr.get("content", "")
                if isinstance(tr_content, list):
                    tr_content = "\n".join(
                        b.get("text", "") for b in tr_content if b.get("type") == "text"
                    )
                items.append({
                    "type": "function_call_output",
                    "call_id": tr["tool_use_id"],
                    "output": tr_content,
                })
        return items

    def _tool_to_responses_format(self, tool: Tool) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }

    def _responses_to_claude(self, data: dict) -> LLMResponse:
        content_blocks: list[dict[str, Any]] = []
        for item in data.get("output", []):
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content", []):
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text", "")
                        if text:
                            content_blocks.append({"type": "text", "text": text})
            elif item_type == "function_call":
                raw_arguments = item.get("arguments", "{}")
                try:
                    parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                except json.JSONDecodeError:
                    parsed_arguments = {"raw": raw_arguments}
                content_blocks.append({
                    "type": "tool_use",
                    "id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "input": parsed_arguments or {},
                })
        if not content_blocks and data.get("output_text"):
            content_blocks.append({"type": "text", "text": data["output_text"]})
        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        usage = data.get("usage")
        if isinstance(usage, dict):
            usage = {
                "prompt_tokens": int(usage.get("input_tokens", 0)),
                "completion_tokens": int(usage.get("output_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            }
        return LLMResponse(
            content=content_blocks,
            stop_reason=data.get("status") or data.get("stop_reason"),
            usage=usage,
        )

    # ── Anthropic (Claude) ──────────────────────────────────────────

    async def _complete_anthropic(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        cfg: ModelConfig,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        thinking_enabled: bool,
        cache_enabled: bool = False,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if cfg.supports_thinking and thinking_enabled:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": cfg.default_thinking_budget_tokens or int(max_tokens * 0.8),
            }
        if system_prompt:
            if cache_enabled:
                body["system"] = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
            else:
                body["system"] = system_prompt
        if tools:
            body["tools"] = [t.to_claude_format() for t in tools]

        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if cache_enabled:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        resp = await self._http.post(
            f"{cfg.base_url}/v1/messages",
            json=body,
            headers=headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Anthropic API error {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()

        return LLMResponse(
            content=data.get("content", []),
            stop_reason=data.get("stop_reason"),
            usage=data.get("usage"),
        )

    # ── Streaming ────────────────────────────────────────────────────

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        model_config: ModelConfig,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        thinking_enabled: bool = False,
        cache_enabled: bool = False,
    ) -> AsyncGenerator[str | LLMResponse, None]:
        """Stream completion — yields str chunks, then a final LLMResponse.

        Usage::

            async for chunk in client.stream_complete(...):
                if isinstance(chunk, str):
                    print(chunk, end="", flush=True)
                else:
                    response = chunk  # final LLMResponse with full content
        """
        if model_config.api_format == "anthropic":
            async for item in self._stream_anthropic(
                messages, tools, model_config, system_prompt,
                max_tokens, temperature, thinking_enabled, cache_enabled,
            ):
                yield item
        else:
            async for item in self._stream_openai(
                messages, tools, model_config, system_prompt, max_tokens, temperature,
            ):
                yield item

    async def _stream_anthropic(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        cfg: ModelConfig,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        thinking_enabled: bool,
        cache_enabled: bool,
    ) -> AsyncGenerator[str | LLMResponse, None]:
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if cfg.supports_thinking and thinking_enabled:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": cfg.default_thinking_budget_tokens or int(max_tokens * 0.8),
            }
        if system_prompt:
            if cache_enabled:
                body["system"] = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
            else:
                body["system"] = system_prompt
        if tools:
            body["tools"] = [t.to_claude_format() for t in tools]

        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if cache_enabled:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        text_chunks: list[str] = []
        content_blocks: list[dict[str, Any]] = []
        stop_reason: str | None = None
        usage: dict[str, int] | None = None

        # Partial tool input accumulator: index -> {"id", "name", "input_str"}
        partial_tools: dict[int, dict[str, Any]] = {}

        async with self._http.stream(
            "POST",
            f"{cfg.base_url}/v1/messages",
            json=body,
            headers=headers,
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise RuntimeError(
                    f"Anthropic API error {resp.status_code}: {text[:500]}"
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "content_block_start":
                    idx = event.get("index", 0)
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        partial_tools[idx] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input_str": "",
                        }

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    idx = event.get("index", 0)
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        text_chunks.append(chunk)
                        yield chunk
                    elif delta.get("type") == "input_json_delta":
                        if idx in partial_tools:
                            partial_tools[idx]["input_str"] += delta.get("partial_json", "")

                elif etype == "message_delta":
                    stop_reason = event.get("delta", {}).get("stop_reason")
                    if "usage" in event:
                        usage = event["usage"]

                elif etype == "message_start":
                    msg_usage = event.get("message", {}).get("usage")
                    if msg_usage:
                        usage = msg_usage

        # Assemble final content blocks
        full_text = "".join(text_chunks)
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for pt in partial_tools.values():
            try:
                parsed_input = json.loads(pt["input_str"]) if pt["input_str"] else {}
            except json.JSONDecodeError:
                parsed_input = {"raw": pt["input_str"]}
            content_blocks.append({
                "type": "tool_use",
                "id": pt["id"],
                "name": pt["name"],
                "input": parsed_input,
            })
        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        yield LLMResponse(
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
        )

    async def _stream_openai(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        cfg: ModelConfig,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[str | LLMResponse, None]:
        oai_messages = self._claude_to_openai_messages(messages, system_prompt)
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            body["tools"] = [t.to_openai_format() for t in tools]

        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

        text_chunks: list[str] = []
        # tool_calls accumulator: index -> dict
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        stop_reason: str | None = None

        async with self._http.stream(
            "POST",
            f"{cfg.base_url}/chat/completions",
            json=body,
            headers=headers,
        ) as resp:
            resp.raise_for_status()

            # Many proxies (nginx gateways, Claude-Relay-Service, etc.) answer
            # maintenance windows with HTTP 200 + a plain JSON body instead of
            # an SSE stream, e.g. `{"success": false, "message": "...维护中..."}`.
            # Without an early check the aiter_lines loop below skips every
            # line (none start with "data: "), the request returns an empty
            # LLMResponse, and the agent silently exits doing nothing. Turn
            # that into a loud error.
            ct = (resp.headers.get("content-type") or "").lower()
            if not ct.startswith("text/event-stream"):
                body_text = (await resp.aread()).decode("utf-8", errors="replace").strip()
                # Try to surface a useful one-line summary.
                summary = body_text[:400]
                try:
                    payload = json.loads(body_text)
                    if isinstance(payload, dict):
                        msg = payload.get("message") or payload.get("error") or payload.get("detail")
                        if msg:
                            summary = str(msg)[:400]
                except json.JSONDecodeError:
                    pass
                raise RuntimeError(
                    f"Upstream {cfg.base_url} returned a non-stream response "
                    f"(content-type={ct!r}). Likely maintenance, auth, or "
                    f"rate-limit error from the proxy. Body: {summary}"
                )

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choice = (event.get("choices") or [{}])[0]
                delta = choice.get("delta", {})

                if "finish_reason" in choice and choice["finish_reason"]:
                    stop_reason = choice["finish_reason"]

                if delta.get("content"):
                    chunk = delta["content"]
                    text_chunks.append(chunk)
                    yield chunk

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc.get("id"):
                        tool_calls_acc[idx]["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        tool_calls_acc[idx]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tool_calls_acc[idx]["arguments"] += fn["arguments"]

        # Assemble final content blocks
        content_blocks: list[dict[str, Any]] = []
        full_text = "".join(text_chunks)
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for tc in tool_calls_acc.values():
            try:
                parsed_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                parsed_args = {"raw": tc["arguments"]}
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": parsed_args,
            })
        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        yield LLMResponse(
            content=content_blocks,
            stop_reason=stop_reason,
            usage=None,
        )
