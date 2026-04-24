"""Unit tests for prax.core.model_catalog."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prax.core.model_catalog import (
    ModelCatalogEntry,
    _to_float,
    _to_int,
    _to_str,
    get_first_available_model,
    get_model_entry,
    iter_model_catalog,
    resolve_available_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(**kwargs) -> ModelCatalogEntry:
    defaults = dict(
        provider="openai",
        name="gpt-4o",
        api_model="gpt-4o",
        api_format="openai",
        request_mode="chat_completions",
        base_url="https://api.openai.com/v1",
        env_names=("OPENAI_API_KEY",),
        api_key_present=True,
    )
    defaults.update(kwargs)
    return ModelCatalogEntry(**defaults)


def _minimal_config(provider="prov", model_name="mymodel", api_model="mymodel-v1",
                    api_key_env="MY_KEY", base_url="https://example.com/v1",
                    extra_model_fields=None, extra_provider_fields=None) -> dict:
    model_cfg = {"name": model_name, "api_model": api_model}
    if extra_model_fields:
        model_cfg.update(extra_model_fields)
    provider_cfg: dict = {
        "api_key_env": api_key_env,
        "base_url": base_url,
        "models": [model_cfg],
    }
    if extra_provider_fields:
        provider_cfg.update(extra_provider_fields)
    return {"providers": {provider: provider_cfg}}


# ---------------------------------------------------------------------------
# 1. ModelCatalogEntry.available — True when key present and model configured
# ---------------------------------------------------------------------------

def test_available_true_when_key_and_configured():
    entry = _make_entry(api_key_present=True, api_model="gpt-4o")
    assert entry.available is True


# ---------------------------------------------------------------------------
# 2. ModelCatalogEntry.available — False when no key
# ---------------------------------------------------------------------------

def test_available_false_when_no_key():
    entry = _make_entry(api_key_present=False, api_model="gpt-4o")
    assert entry.available is False


# ---------------------------------------------------------------------------
# 3. ModelCatalogEntry.api_model_configured — False for template
# ---------------------------------------------------------------------------

def test_api_model_configured_false_for_template():
    entry = _make_entry(api_model="<your-model-here>")
    assert entry.api_model_configured is False


def test_api_model_configured_true_for_real_model():
    entry = _make_entry(api_model="gpt-4o")
    assert entry.api_model_configured is True


def test_available_false_when_template_even_with_key():
    entry = _make_entry(api_key_present=True, api_model="<placeholder>")
    assert entry.available is False


# ---------------------------------------------------------------------------
# 4. ModelCatalogEntry.matches — by name and alias
# ---------------------------------------------------------------------------

def test_matches_by_name():
    entry = _make_entry(name="gpt-4o", aliases=())
    assert entry.matches("gpt-4o") is True


def test_matches_by_alias():
    entry = _make_entry(name="gpt-4o", aliases=("gpt4o", "gpt-4"))
    assert entry.matches("gpt4o") is True
    assert entry.matches("gpt-4") is True


def test_matches_false_for_unknown():
    entry = _make_entry(name="gpt-4o", aliases=("gpt4o",))
    assert entry.matches("claude") is False


# ---------------------------------------------------------------------------
# 5. iter_model_catalog — empty config → []
# ---------------------------------------------------------------------------

def test_iter_model_catalog_empty_config():
    assert iter_model_catalog({}) == []
    assert iter_model_catalog({"providers": {}}) == []


# ---------------------------------------------------------------------------
# 6. iter_model_catalog — single provider with model
# ---------------------------------------------------------------------------

def test_iter_model_catalog_single_provider(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-test")
    cfg = _minimal_config(provider="prov", model_name="mymodel", api_key_env="MY_KEY")
    entries = iter_model_catalog(cfg)
    assert len(entries) == 1
    e = entries[0]
    assert e.provider == "prov"
    assert e.name == "mymodel"
    assert e.api_key_present is True


# ---------------------------------------------------------------------------
# 7. iter_model_catalog — env_names as list
# ---------------------------------------------------------------------------

def test_iter_model_catalog_env_names_as_list(monkeypatch):
    monkeypatch.setenv("KEY_A", "")
    monkeypatch.setenv("KEY_B", "value")
    cfg = _minimal_config(api_key_env=["KEY_A", "KEY_B"])
    entries = iter_model_catalog(cfg)
    assert len(entries) == 1
    e = entries[0]
    assert e.env_names == ("KEY_A", "KEY_B")
    assert e.api_key_present is True  # KEY_B has a value


# ---------------------------------------------------------------------------
# 8. iter_model_catalog — env_names as string
# ---------------------------------------------------------------------------

def test_iter_model_catalog_env_names_as_string(monkeypatch):
    monkeypatch.setenv("SINGLE_KEY", "abc")
    cfg = _minimal_config(api_key_env="SINGLE_KEY")
    entries = iter_model_catalog(cfg)
    assert entries[0].env_names == ("SINGLE_KEY",)
    assert entries[0].api_key_present is True


# ---------------------------------------------------------------------------
# 9. iter_model_catalog — base_url_env override
# ---------------------------------------------------------------------------

def test_iter_model_catalog_base_url_env_override(monkeypatch):
    monkeypatch.setenv("BASE_URL_OVERRIDE", "https://custom.endpoint/v1")
    cfg = _minimal_config(base_url="https://original.com/v1",
                          extra_provider_fields={"base_url_env": "BASE_URL_OVERRIDE"})
    entries = iter_model_catalog(cfg)
    assert entries[0].base_url == "https://custom.endpoint/v1"


def test_iter_model_catalog_base_url_env_missing_uses_default(monkeypatch):
    monkeypatch.delenv("NONEXISTENT_ENV_VAR", raising=False)
    cfg = _minimal_config(base_url="https://fallback.com/v1",
                          extra_provider_fields={"base_url_env": "NONEXISTENT_ENV_VAR"})
    entries = iter_model_catalog(cfg)
    assert entries[0].base_url == "https://fallback.com/v1"


# ---------------------------------------------------------------------------
# 10. iter_model_catalog — api_key_present detection
# ---------------------------------------------------------------------------

def test_iter_model_catalog_api_key_absent(monkeypatch):
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    cfg = _minimal_config(api_key_env="ABSENT_KEY")
    entries = iter_model_catalog(cfg)
    assert entries[0].api_key_present is False


def test_iter_model_catalog_no_env_names_gives_no_key():
    cfg = _minimal_config(api_key_env="")
    entries = iter_model_catalog(cfg)
    assert entries[0].env_names == ()
    assert entries[0].api_key_present is False


# ---------------------------------------------------------------------------
# 11. get_model_entry — found by name
# ---------------------------------------------------------------------------

def test_get_model_entry_found_by_name(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-x")
    cfg = _minimal_config(model_name="targetmodel")
    entry = get_model_entry("targetmodel", cfg)
    assert entry is not None
    assert entry.name == "targetmodel"


# ---------------------------------------------------------------------------
# 12. get_model_entry — found by alias
# ---------------------------------------------------------------------------

def test_get_model_entry_found_by_alias(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-x")
    cfg = _minimal_config(model_name="long-model-name",
                          extra_model_fields={"aliases": ["lmn", "l-m"]})
    entry = get_model_entry("lmn", cfg)
    assert entry is not None
    assert entry.name == "long-model-name"


# ---------------------------------------------------------------------------
# 13. get_model_entry — not found → None
# ---------------------------------------------------------------------------

def test_get_model_entry_not_found():
    cfg = _minimal_config(model_name="alpha")
    result = get_model_entry("beta", cfg)
    assert result is None


# ---------------------------------------------------------------------------
# 13b. get_model_entry — prefer available when name collides across providers
# ---------------------------------------------------------------------------

def test_get_model_entry_prefers_available_on_collision(monkeypatch):
    # Simulates: bundled "zhipu" provider defines "glm-4-flash" (no ZHIPU_API_KEY set),
    # while user-configured "my-service" also defines "glm-4-flash" with a key.
    # get_model_entry must return the available one, not the first-scanned one.
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("USER_KEY", "sk-user")
    cfg = {
        "providers": {
            "zhipu": {
                "api_key_env": "ZHIPU_API_KEY",
                "base_url": "https://open.bigmodel.cn",
                "models": [{"name": "glm-4-flash", "api_model": "glm-4-flash"}],
            },
            "my-service": {
                "api_key_env": "USER_KEY",
                "base_url": "https://my.proxy",
                "models": [{"name": "glm-4-flash", "api_model": "glm-4-flash"}],
            },
        }
    }
    entry = get_model_entry("glm-4-flash", cfg)
    assert entry is not None
    assert entry.provider == "my-service"
    assert entry.available is True


def test_get_model_entry_falls_back_to_first_when_none_available(monkeypatch):
    # When no provider has credentials, return the first match so callers can
    # surface a meaningful "missing credentials" message.
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    cfg = {
        "providers": {
            "zhipu": {
                "api_key_env": "ZHIPU_API_KEY",
                "base_url": "https://open.bigmodel.cn",
                "models": [{"name": "glm-4-flash", "api_model": "glm-4-flash"}],
            },
            "alt": {
                "api_key_env": "OTHER_KEY",
                "base_url": "https://alt",
                "models": [{"name": "glm-4-flash", "api_model": "glm-4-flash"}],
            },
        }
    }
    entry = get_model_entry("glm-4-flash", cfg)
    assert entry is not None
    assert entry.provider == "zhipu"
    assert entry.available is False


# ---------------------------------------------------------------------------
# 14. get_first_available_model — returns first available
# ---------------------------------------------------------------------------

def test_get_first_available_model_returns_first_available(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-val")
    cfg = _minimal_config(model_name="mymodel")
    result = get_first_available_model(["missing", "mymodel"], cfg)
    assert result is not None
    assert result.name == "mymodel"


# ---------------------------------------------------------------------------
# 15. get_first_available_model — none available → None
# ---------------------------------------------------------------------------

def test_get_first_available_model_none_available(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    cfg = _minimal_config(model_name="mymodel", api_key_env="MY_KEY")
    result = get_first_available_model(["mymodel"], cfg)
    assert result is None


# ---------------------------------------------------------------------------
# 16. resolve_available_model — success (mock llm_client.resolve_model)
# ---------------------------------------------------------------------------

def test_resolve_available_model_success(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-ok")
    cfg = _minimal_config(model_name="mymodel")
    mock_client = MagicMock()
    mock_client.resolve_model.return_value = MagicMock(name="mymodel")
    result = resolve_available_model("mymodel", models_config=cfg, llm_client=mock_client)
    mock_client.resolve_model.assert_called_once_with("mymodel", cfg)
    assert result is not None


# ---------------------------------------------------------------------------
# 17. resolve_available_model — model not found → ValueError
# ---------------------------------------------------------------------------

def test_resolve_available_model_not_found():
    cfg = _minimal_config(model_name="other")
    mock_client = MagicMock()
    with pytest.raises(ValueError, match="not found in configuration"):
        resolve_available_model("nonexistent", models_config=cfg, llm_client=mock_client)


# ---------------------------------------------------------------------------
# 18. resolve_available_model — template model → ValueError
# ---------------------------------------------------------------------------

def test_resolve_available_model_template(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-ok")
    cfg = _minimal_config(model_name="template-model", api_model="<your-model-here>")
    mock_client = MagicMock()
    with pytest.raises(ValueError, match="template entry"):
        resolve_available_model("template-model", models_config=cfg, llm_client=mock_client)


# ---------------------------------------------------------------------------
# 19. resolve_available_model — unavailable (no key) → ValueError
# ---------------------------------------------------------------------------

def test_resolve_available_model_unavailable(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    cfg = _minimal_config(model_name="mymodel", api_key_env="MY_KEY")
    mock_client = MagicMock()
    with pytest.raises(ValueError, match="unavailable"):
        resolve_available_model("mymodel", models_config=cfg, llm_client=mock_client)


# ---------------------------------------------------------------------------
# 20. Helper functions: _to_float, _to_str, _to_int edge cases
# ---------------------------------------------------------------------------

class TestToFloat:
    def test_none_returns_none(self):
        assert _to_float(None) is None

    def test_int_input(self):
        assert _to_float(5) == 5.0

    def test_float_input(self):
        assert _to_float(1.5) == 1.5

    def test_string_numeric(self):
        assert _to_float("3.14") == pytest.approx(3.14)

    def test_string_invalid(self):
        assert _to_float("not-a-number") is None


class TestToStr:
    def test_none_returns_none(self):
        assert _to_str(None) is None

    def test_string_passthrough(self):
        assert _to_str("hello") == "hello"

    def test_int_to_str(self):
        assert _to_str(42) == "42"

    def test_float_to_str(self):
        assert _to_str(1.5) == "1.5"


class TestToInt:
    def test_none_returns_none(self):
        assert _to_int(None) is None

    def test_int_passthrough(self):
        assert _to_int(10) == 10

    def test_string_numeric(self):
        assert _to_int("7") == 7

    def test_string_invalid(self):
        assert _to_int("abc") is None

    def test_float_string_invalid(self):
        # "1.5" cannot be parsed by int()
        assert _to_int("1.5") is None
