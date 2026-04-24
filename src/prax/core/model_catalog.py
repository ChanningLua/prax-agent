"""Model catalog and availability helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .llm_client import LLMClient, ModelConfig


@dataclass(frozen=True)
class ModelCatalogEntry:
    provider: str
    name: str
    api_model: str
    api_format: str
    request_mode: str
    base_url: str
    env_names: tuple[str, ...]
    api_key_present: bool
    tier: str | None = None
    cost_per_1m_tokens: float | None = None
    aliases: tuple[str, ...] = ()
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning_effort: bool = False
    supports_thinking: bool = False
    default_reasoning_effort: str | None = None
    default_thinking_budget_tokens: int | None = None

    @property
    def available(self) -> bool:
        return self.api_key_present and self.api_model_configured

    @property
    def api_model_configured(self) -> bool:
        return not (self.api_model.startswith("<") and self.api_model.endswith(">"))

    def matches(self, model_name: str) -> bool:
        return model_name == self.name or model_name in self.aliases


def iter_model_catalog(models_config: dict) -> list[ModelCatalogEntry]:
    entries: list[ModelCatalogEntry] = []
    for provider_name, provider_cfg in models_config.get("providers", {}).items():
        raw_env_names = provider_cfg.get("api_key_env", "")
        if isinstance(raw_env_names, list):
            env_names = tuple(str(name) for name in raw_env_names)
        elif raw_env_names:
            env_names = (str(raw_env_names),)
        else:
            env_names = ()

        base_url = str(provider_cfg.get("base_url", "")).rstrip("/")
        base_url_env = provider_cfg.get("base_url_env")
        if base_url_env:
            base_url = os.environ.get(str(base_url_env), base_url)

        api_key_present = any(os.environ.get(name, "") for name in env_names) if env_names else False

        for model_cfg in provider_cfg.get("models", []):
            entries.append(
                ModelCatalogEntry(
                    provider=provider_name,
                    name=str(model_cfg["name"]),
                    api_model=str(model_cfg.get("api_model", model_cfg["name"])),
                    api_format=str(provider_cfg.get("format", "openai")),
                    request_mode=str(model_cfg.get("request_mode", "chat_completions")),
                    base_url=base_url,
                    env_names=env_names,
                    api_key_present=api_key_present,
                    tier=model_cfg.get("tier"),
                    cost_per_1m_tokens=_to_float(model_cfg.get("cost_per_1m_tokens")),
                    aliases=tuple(str(alias) for alias in model_cfg.get("aliases", [])),
                    supports_tools=bool(model_cfg.get("supports_tools", True)),
                    supports_streaming=bool(model_cfg.get("supports_streaming", True)),
                    supports_reasoning_effort=bool(model_cfg.get("supports_reasoning_effort", False)),
                    supports_thinking=bool(model_cfg.get("supports_thinking", False)),
                    default_reasoning_effort=_to_str(model_cfg.get("default_reasoning_effort")),
                    default_thinking_budget_tokens=_to_int(model_cfg.get("default_thinking_budget_tokens")),
                )
            )
    return entries


def get_model_entry(model_name: str, models_config: dict) -> ModelCatalogEntry | None:
    for entry in iter_model_catalog(models_config):
        if entry.matches(model_name):
            return entry
    return None


def get_first_available_model(candidates: list[str], models_config: dict) -> ModelCatalogEntry | None:
    for candidate in candidates:
        entry = get_model_entry(candidate, models_config)
        if entry is not None and entry.available:
            return entry
    return None


def resolve_available_model(
    model_name: str,
    *,
    models_config: dict,
    llm_client: LLMClient,
) -> ModelConfig:
    entry = get_model_entry(model_name, models_config)
    if entry is None:
        raise ValueError(f"Model '{model_name}' not found in configuration")
    if not entry.available:
        env_text = ", ".join(entry.env_names) if entry.env_names else "(no env configured)"
        if not entry.api_model_configured:
            raise ValueError(
                f"Model '{model_name}' is a template entry. Replace api_model in models.yaml before use."
            )
        raise ValueError(
            f"Model '{model_name}' is configured but unavailable; missing credentials in {env_text}"
        )
    return llm_client.resolve_model(entry.name, models_config)


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _to_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return None
