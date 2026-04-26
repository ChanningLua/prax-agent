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
        "upgrade_chain": ["codex", "gpt-5.4", "claude-sonnet-4-7"],
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "format": "openai",
                "models": [
                    {
                        "name": "gpt-5.4",
                        "api_model": "gpt-5.4",
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
        "default_model": "claude-sonnet-4-7",
        "upgrade_chain": ["claude-sonnet-4-7", "claude-opus-4-7"],
        "providers": {
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
                "format": "anthropic",
                "models": [
                    {
                        "name": "claude-sonnet-4-7",
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
                        "name": "claude-opus-4-7",
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
    result["default_model"] = default_model or "gpt-5.4"
    return result


def merge_into_local_config(
    existing: dict[str, Any] | None,
    flow: str,
    *,
    overwrite: bool = False,
    set_default: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    """Build the workspace yaml content.

    Default behaviour as of 0.5.5: write an empty skeleton (`providers: {}`)
    so the user-level `~/.prax/models.yaml` and bundled defaults can flow
    through unchanged. Without this, the auto-generated workspace yaml
    silently overrode user-level `base_url` / `api_key_env` settings —
    every field looked configured but was just a copy of the bundled
    default. See https://github.com/.../issues/<id-of-init-models-bug>.

    - `full=True` reproduces the old behaviour (full template merge),
      kept for users who want the schema fully expanded as a starting
      point and for back-compat with scripts.
    - `overwrite=True` (the `--force` flag) still writes the full
      template — explicit overwrite always wins.
    """
    if overwrite:
        config = build_flow_template(flow)
        if set_default and flow != "all":
            config["default_model"] = FLOW_TEMPLATES[flow]["default_model"]
        return config

    if not full:
        # Skeleton mode: do not seed any provider fields. If existing
        # workspace yaml already had hand-written content, preserve it
        # verbatim — we don't touch what the user explicitly authored.
        if existing:
            return deepcopy(existing)
        return {"providers": {}}

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


SKELETON_HEADER = """\
# .prax/models.yaml — workspace-level model overrides for this project.
#
# YOU DON'T HAVE TO EDIT THIS FILE. prax already ships with working
# defaults for OpenAI / Anthropic / Zhipu (GLM). To start using prax:
#
#   1. Set the API key for the provider you want, e.g.:
#        export OPENAI_API_KEY=sk-...        # OpenAI / compatible relays
#        export ANTHROPIC_API_KEY=sk-ant-... # Claude
#        export ZHIPU_API_KEY=...            # GLM
#   2. Run `prax prompt "hi"` to test.
#
# This file is for OVERRIDING those defaults — only fields you write
# here will take effect; the rest inherit from ~/.prax/models.yaml
# (your cross-project user-level config) and bundled defaults.
#
# Common overrides:
#
#   providers:
#     openai:
#       base_url: https://my-relay.example.com/openai   # route through a relay
#   default_model: claude-sonnet-4-7                    # this project uses Claude
#
# Want the full schema as a starting point (all providers, every field,
# placeholders to replace)? Run:
#
#   prax /init-models --full
#
# Want to see what's currently in effect (bundled + user + workspace
# merged)? Run:
#
#   prax /providers
"""


def load_user_models_config() -> dict[str, Any] | None:
    """Read `~/.prax/models.yaml` if present. Used to detect overlap when
    the user is about to write workspace yaml that would override their
    user-level config (e.g. running /init-models repeatedly).
    """
    path = Path.home() / ".prax" / "models.yaml"
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None


def detect_user_level_overlap(flow: str) -> list[str]:
    """Return user-level provider names that the requested flow would
    cover. Empty list = no risk of unintended override.
    """
    user = load_user_models_config()
    if not user:
        return []
    user_providers = set((user.get("providers") or {}).keys())
    template_providers: set[str] = set()
    for fname in flow_names(flow):
        template_providers.update(FLOW_TEMPLATES[fname].get("providers", {}).keys())
    return sorted(user_providers & template_providers)


def render_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def load_local_models_config(cwd: str) -> dict[str, Any] | None:
    path = Path(cwd) / ".prax" / "models.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_local_models_config(cwd: str, config: dict[str, Any], *, header: str = "") -> Path:
    path = Path(cwd) / ".prax" / "models.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = render_yaml(config)
    text = (header + "\n" + body) if header else body
    path.write_text(text, encoding="utf-8")
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
