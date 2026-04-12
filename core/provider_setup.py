"""Helpers for provider setup templates and local model config generation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


FLOW_TEMPLATES: dict[str, dict[str, Any]] = {
    "glm": {
        "default_model": "glm-5",
        "upgrade_chain": ["glm-4-flash", "glm-4", "glm-5"],
        "providers": {
            "zhipu": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key_env": "ZHIPU_API_KEY",
                "format": "openai",
                "models": [
                    {
                        "name": "glm-4-flash",
                        "aliases": ["glm-flash"],
                        "request_mode": "chat_completions",
                        "tier": "low",
                        "cost_per_1m_tokens": 0.1,
                        "supports_tools": True,
                        "supports_streaming": True,
                    },
                    {
                        "name": "glm-4",
                        "aliases": ["glm"],
                        "request_mode": "chat_completions",
                        "tier": "standard",
                        "cost_per_1m_tokens": 1.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                    },
                    {
                        "name": "glm-5",
                        "aliases": ["glm-pro"],
                        "request_mode": "chat_completions",
                        "tier": "high",
                        "cost_per_1m_tokens": 2.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                    },
                ],
            }
        },
    },
    "codex": {
        "default_model": "codex",
        "upgrade_chain": ["codex", "gpt-4.1", "claude-sonnet-4-6"],
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [
                    {
                        "name": "gpt-4.1",
                        "api_model": "gpt-4.1",
                        "aliases": ["gpt", "gpt4"],
                        "request_mode": "chat_completions",
                        "tier": "standard",
                        "cost_per_1m_tokens": 5.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                    },
                    {
                        "name": "codex",
                        "api_model": "<replace-with-codex-model>",
                        "aliases": ["codex", "responses"],
                        "request_mode": "responses",
                        "tier": "high",
                        "cost_per_1m_tokens": 0.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_reasoning_effort": True,
                        "default_reasoning_effort": "medium",
                    },
                ],
            }
        },
    },
    "claude": {
        "default_model": "claude-sonnet-4-6",
        "upgrade_chain": ["claude-sonnet-4-6", "claude-opus-4-6"],
        "providers": {
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
                "format": "anthropic",
                "models": [
                    {
                        "name": "claude-sonnet-4-6",
                        "aliases": ["sonnet"],
                        "request_mode": "chat_completions",
                        "tier": "premium",
                        "cost_per_1m_tokens": 15.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_thinking": True,
                        "default_thinking_budget_tokens": 12000,
                    },
                    {
                        "name": "claude-opus-4-6",
                        "aliases": ["opus"],
                        "request_mode": "chat_completions",
                        "tier": "ultra",
                        "cost_per_1m_tokens": 75.0,
                        "supports_tools": True,
                        "supports_streaming": True,
                        "supports_thinking": True,
                        "default_thinking_budget_tokens": 32000,
                    },
                ],
            }
        },
    },
}


def flow_names(target: str) -> list[str]:
    if target == "all":
        return ["glm", "codex", "claude"]
    if target not in FLOW_TEMPLATES:
        raise ValueError("flow must be glm|codex|claude|all")
    return [target]


def build_flow_template(flow: str) -> dict[str, Any]:
    if flow == "all":
        return merge_flow_templates(flow_names(flow))
    if flow not in FLOW_TEMPLATES:
        raise ValueError("flow must be glm|codex|claude|all")
    return deepcopy(FLOW_TEMPLATES[flow])


def merge_flow_templates(flows: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"providers": {}, "upgrade_chain": []}
    default_model = None
    for flow in flows:
        template = deepcopy(FLOW_TEMPLATES[flow])
        if default_model is None:
            default_model = template.get("default_model")
        result["providers"] = _merge_providers(result["providers"], template.get("providers", {}))
        result["upgrade_chain"] = _merge_upgrade_chain(result["upgrade_chain"], template.get("upgrade_chain", []))
    result["default_model"] = default_model or "gpt-4.1"
    return result


def merge_into_local_config(
    existing: dict[str, Any] | None,
    flow: str,
    *,
    overwrite: bool = False,
    set_default: bool = False,
) -> dict[str, Any]:
    if overwrite:
        config = build_flow_template(flow)
        if set_default and flow != "all":
            config["default_model"] = FLOW_TEMPLATES[flow]["default_model"]
        return config
    base = deepcopy(existing or {})
    template = build_flow_template(flow)
    merged = {
        "providers": _merge_providers(base.get("providers", {}), template.get("providers", {})),
        "upgrade_chain": _merge_upgrade_chain(base.get("upgrade_chain", []), template.get("upgrade_chain", [])),
        "default_model": base.get("default_model") or template.get("default_model"),
    }
    if set_default and flow != "all":
        merged["default_model"] = FLOW_TEMPLATES[flow]["default_model"]
    return merged


def render_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def load_local_models_config(cwd: str) -> dict[str, Any] | None:
    path = Path(cwd) / ".prax" / "models.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_local_models_config(cwd: str, config: dict[str, Any]) -> Path:
    path = Path(cwd) / ".prax" / "models.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_yaml(config), encoding="utf-8")
    return path


def render_env_example(flow: str) -> str:
    lines = ["# Generated by prax provider setup"]
    keys: list[str] = []
    for name in flow_names(flow):
        template = FLOW_TEMPLATES[name]
        for provider_cfg in template.get("providers", {}).values():
            raw = provider_cfg.get("api_key_env", "")
            if isinstance(raw, list):
                keys.extend(str(item) for item in raw)
            elif raw:
                keys.append(str(raw))
    unique_keys: list[str] = []
    for key in keys:
        if key not in unique_keys:
            unique_keys.append(key)
    for key in unique_keys:
        lines.append(f"{key}=<your-{key.lower()}>")
    return "\n".join(lines) + "\n"


def write_env_example(cwd: str, flow: str) -> Path:
    path = Path(cwd) / ".prax" / ".env.example"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_env_example(flow), encoding="utf-8")
    return path


def _merge_providers(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for provider_name, provider_cfg in extra.items():
        if provider_name not in merged:
            merged[provider_name] = deepcopy(provider_cfg)
            continue
        existing_provider = merged[provider_name]
        for key, value in provider_cfg.items():
            if key != "models":
                existing_provider[key] = deepcopy(value)
                continue
            existing_models = {model["name"]: deepcopy(model) for model in existing_provider.get("models", [])}
            for model in value:
                existing_models[model["name"]] = deepcopy(model)
            existing_provider["models"] = list(existing_models.values())
    return merged


def _merge_upgrade_chain(base: list[str], extra: list[str]) -> list[str]:
    result: list[str] = []
    for item in [*base, *extra]:
        if item not in result:
            result.append(item)
    return result
