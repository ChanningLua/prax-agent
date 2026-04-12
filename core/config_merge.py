"""Config merge utilities for three-tier configuration loading."""
from __future__ import annotations

from typing import Any


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced, not merged."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_providers(global_providers: dict, local_providers: dict) -> dict:
    """Merge provider configs. Models are matched by name and merged field-by-field."""
    result = dict(global_providers)

    for provider_name, local_provider in local_providers.items():
        if provider_name not in result:
            # New provider - add directly
            result[provider_name] = local_provider
            continue

        # Merge existing provider
        global_provider = result[provider_name]
        merged_provider = dict(global_provider)

        # Merge top-level provider fields
        for key, value in local_provider.items():
            if key == "models":
                continue
            merged_provider[key] = value

        # Merge models by name
        global_models = {m.get("name"): m for m in global_provider.get("models", [])}
        local_models = {m.get("name"): m for m in local_provider.get("models", [])}

        merged_models = dict(global_models)
        for model_name, local_model in local_models.items():
            if model_name in merged_models:
                # Field-level merge
                merged_models[model_name] = {**merged_models[model_name], **local_model}
            else:
                # New model - add directly
                merged_models[model_name] = local_model

        merged_provider["models"] = list(merged_models.values())
        result[provider_name] = merged_provider

    return result


def load_merged_models_config(global_cfg: dict, local_cfg: dict) -> dict:
    """Merge local config over global config.

    - default_model, upgrade_chain: direct override
    - providers: use merge_providers for field-level merging
    """
    result = dict(global_cfg)

    # Direct overrides
    if "default_model" in local_cfg:
        result["default_model"] = local_cfg["default_model"]

    if "upgrade_chain" in local_cfg:
        result["upgrade_chain"] = local_cfg["upgrade_chain"]

    # Merge providers
    global_providers = global_cfg.get("providers", {})
    local_providers = local_cfg.get("providers", {})
    result["providers"] = merge_providers(global_providers, local_providers)

    return result
