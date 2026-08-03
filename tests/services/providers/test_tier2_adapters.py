"""Tests for the Tier-2 OpenAI-compatible provider adapters.

Covers DeepSeek, Moonshot (Kimi), MiniMax, and Xiaomi (MiMo) — all served by
the config-driven adapter (``src/services/providers/openai_compat.py`` /
``adapter_configs.py``), following the exact pattern used for the five
consolidated Tier-1 providers (deepinfra/together/fireworks/groq/zai) in
``tests/services/providers/test_openai_compat.py``.

None of these providers use a pooled client_factory or middleware quirks
(parity with the ``deepinfra`` config: a plain ``OpenAI(base_url=..., api_key=...)``
client built per request).
"""

from unittest.mock import Mock, patch

import pytest

from src.config import Config
from src.services.providers.base import ProviderAdapter
from src.services.providers.openai_compat import OpenAICompatAdapter

TIER2_SLUGS = ["deepseek", "moonshot", "minimax", "xiaomi"]

# slug -> (base_url, api_key_env, error_match)
EXPECTED_CONFIG = {
    "deepseek": (
        "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY",
        "DeepSeek API key not configured",
    ),
    "moonshot": (
        "https://api.moonshot.ai/v1",
        "MOONSHOT_API_KEY",
        "Moonshot AI API key not configured",
    ),
    "minimax": (
        "https://api.minimax.io/v1",
        "MINIMAX_API_KEY",
        "MiniMax API key not configured",
    ),
    "xiaomi": (
        "https://api.xiaomimimo.com/v1",
        "XIAOMI_API_KEY",
        "Xiaomi MiMo API key not configured",
    ),
}


def _mock_openai_response():
    mock_response = Mock()
    mock_response.id = "chatcmpl-test123"
    mock_response.object = "chat.completion"
    mock_response.created = 1234567890
    mock_response.model = "test-model"

    mock_choice = Mock()
    mock_choice.index = 0
    mock_choice.message = Mock()
    mock_choice.message.role = "assistant"
    mock_choice.message.content = "Test response"
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    mock_response.usage = Mock()
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 20
    mock_response.usage.total_tokens = 30
    return mock_response


MESSAGES = [{"role": "user", "content": "Hello"}]


# ---------------------------------------------------------------------------
# 1. Config table: entries exist with the right base_url / api_key_env
# ---------------------------------------------------------------------------


class TestTier2Configs:
    def test_all_tier2_slugs_present(self):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        for slug in TIER2_SLUGS:
            assert slug in ADAPTER_CONFIGS, f"{slug} missing from ADAPTER_CONFIGS"

    @pytest.mark.parametrize("slug", TIER2_SLUGS)
    def test_config_values(self, slug):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        base_url, key_env, _ = EXPECTED_CONFIG[slug]
        cfg = ADAPTER_CONFIGS[slug]
        assert cfg.base_url.rstrip("/") == base_url.rstrip("/")
        assert cfg.api_key_env == key_env
        assert cfg.slug == slug

    @pytest.mark.parametrize("slug", TIER2_SLUGS)
    def test_no_pooled_client_factory(self, slug):
        """Parity with deepinfra: plain OpenAI client built per request."""
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        assert ADAPTER_CONFIGS[slug].client_factory is None

    @pytest.mark.parametrize("slug", TIER2_SLUGS)
    def test_no_middleware_quirks(self, slug):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS
        from src.services.providers.openai_compat import Quirks

        quirks = ADAPTER_CONFIGS[slug].quirks or Quirks()
        assert quirks.circuit_breaker is None
        assert quirks.sentry is False
        assert quirks.timing is False


# ---------------------------------------------------------------------------
# 2. ADAPTERS registry: adapter resolves for every tier-2 slug
# ---------------------------------------------------------------------------


class TestTier2AdapterRegistry:
    def test_all_tier2_slugs_resolve(self):
        from src.services.providers.adapter_configs import ADAPTERS

        for slug in TIER2_SLUGS:
            assert slug in ADAPTERS, f"{slug} missing from ADAPTERS"
            assert isinstance(ADAPTERS[slug], ProviderAdapter)

    @pytest.mark.parametrize("slug", TIER2_SLUGS)
    def test_exposes_request_process_stream(self, slug):
        from src.services.providers.adapter_configs import ADAPTERS

        adapter = ADAPTERS[slug]
        assert callable(adapter.request)
        assert callable(adapter.process)
        assert callable(adapter.stream)


# ---------------------------------------------------------------------------
# 3. request()/stream() target the right base URL + auth header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", TIER2_SLUGS)
class TestTier2RequestTargeting:
    def _adapter(self, slug):
        from src.services.providers.adapter_configs import ADAPTERS

        return ADAPTERS[slug]

    def test_missing_key_raises_value_error(self, slug, monkeypatch):
        _, key_env, error_match = EXPECTED_CONFIG[slug]
        monkeypatch.setattr(Config, key_env, None, raising=False)
        adapter = self._adapter(slug)
        with pytest.raises(ValueError, match=error_match):
            adapter.request(MESSAGES, "test-model")

    def test_request_builds_client_with_base_url_and_key(self, slug, monkeypatch):
        base_url, key_env, _ = EXPECTED_CONFIG[slug]
        monkeypatch.setattr(Config, key_env, "sk-fake-test", raising=False)
        adapter = self._adapter(slug)

        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = _mock_openai_response()
            adapter.request(MESSAGES, "test-model")

            kwargs = mock_openai.call_args[1]
            assert kwargs["base_url"].rstrip("/") == base_url.rstrip("/")
            assert kwargs["api_key"] == "sk-fake-test"

    def test_forwards_to_create(self, slug, monkeypatch):
        _, key_env, _ = EXPECTED_CONFIG[slug]
        monkeypatch.setattr(Config, key_env, "sk-fake-test", raising=False)
        adapter = self._adapter(slug)
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = _mock_openai_response()

        with patch.object(OpenAICompatAdapter, "_get_client", return_value=mock_client):
            response = adapter.request(MESSAGES, "test-model")

        assert response is not None
        mock_client.chat.completions.create.assert_called_once()
        assert mock_client.chat.completions.create.call_args[1]["messages"] == MESSAGES

    def test_request_propagates_error(self, slug, monkeypatch):
        _, key_env, _ = EXPECTED_CONFIG[slug]
        monkeypatch.setattr(Config, key_env, "sk-fake-test", raising=False)
        adapter = self._adapter(slug)
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        with patch.object(OpenAICompatAdapter, "_get_client", return_value=mock_client):
            with pytest.raises(Exception, match="API Error"):
                adapter.request(MESSAGES, "test-model")

    def test_stream_sets_flag_and_passes_through(self, slug, monkeypatch):
        _, key_env, _ = EXPECTED_CONFIG[slug]
        monkeypatch.setattr(Config, key_env, "sk-fake-test", raising=False)
        adapter = self._adapter(slug)
        mock_client = Mock()
        sentinel = Mock(name="stream")
        mock_client.chat.completions.create.return_value = sentinel

        with patch.object(OpenAICompatAdapter, "_get_client", return_value=mock_client):
            result = adapter.stream(MESSAGES, "test-model")

        assert result is sentinel
        assert mock_client.chat.completions.create.call_args[1]["stream"] is True

    def test_process_normalizes_response(self, slug):
        adapter = self._adapter(slug)
        processed = adapter.process(_mock_openai_response())
        assert processed["id"] == "chatcmpl-test123"
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"] == {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }


# ---------------------------------------------------------------------------
# 4. Config env-var attributes exist on Config
# ---------------------------------------------------------------------------


class TestTier2ConfigEnvVars:
    @pytest.mark.parametrize(
        "attr",
        ["DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "MINIMAX_API_KEY", "XIAOMI_API_KEY"],
    )
    def test_config_has_attribute(self, attr):
        assert hasattr(Config, attr), f"Config.{attr} not defined"


# ---------------------------------------------------------------------------
# 5. Registry wiring: src.handlers.provider_registry routes to the adapters
# ---------------------------------------------------------------------------


class TestTier2RegistryWiring:
    def test_routing_entries_present_when_enabled(self, monkeypatch):
        import importlib

        import src.config.config as config_mod
        import src.handlers.provider_registry as reg

        monkeypatch.setattr(config_mod.Config, "ENABLED_PROVIDERS", None)
        try:
            reg = importlib.reload(reg)
            routing = reg.PROVIDER_ROUTING
            for slug in TIER2_SLUGS:
                assert slug in routing, f"{slug} missing from PROVIDER_ROUTING"
                entry = routing[slug]
                assert set(entry.keys()) == {"request", "process", "stream"}
                for key in ("request", "process", "stream"):
                    assert callable(entry[key]), f"{slug}.{key} not callable"
        finally:
            monkeypatch.undo()
            importlib.reload(reg)


# ---------------------------------------------------------------------------
# 6. Catalog fetch functions wired into PROVIDER_FETCH_FUNCTIONS
# ---------------------------------------------------------------------------


class TestTier2CatalogFetchWiring:
    def test_fetch_functions_registered(self):
        from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS

        for slug in TIER2_SLUGS:
            assert slug in PROVIDER_FETCH_FUNCTIONS, f"{slug} missing from PROVIDER_FETCH_FUNCTIONS"
            assert callable(PROVIDER_FETCH_FUNCTIONS[slug])
