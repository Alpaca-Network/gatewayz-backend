"""Tests for the config-driven OpenAI-compatible provider adapter.

Covers the adapter itself (fake config) and behavioral parity for the five
consolidated providers (deepinfra, together, fireworks, groq, zai). The
parity assertions are migrated from
tests/services/test_provider_clients_parametrized.py, which used to exercise
the per-provider client modules that this adapter replaces:

  - get-client raises ValueError with the provider-specific message when the
    API key is not configured (old ``get_<provider>_client`` behavior),
  - request() forwards to ``client.chat.completions.create`` and propagates
    provider errors unmodified (old ``make_<provider>_request_openai``),
  - stream() passes ``stream=True`` and returns the SDK stream object
    untouched (old ``make_<provider>_request_openai_stream``),
  - process() normalizes the OpenAI-shape response to the
    ``{id, object, created, model, choices, usage}`` dict and returns ``{}``
    usage when the response has none (old ``process_<provider>_response``).
"""

from unittest.mock import Mock, patch

import pytest

from src.config import Config
from src.services.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
)
from src.services.providers.base import ProviderAdapter
from src.services.providers.openai_compat import (
    OpenAICompatAdapter,
    ProviderConfig,
    Quirks,
    make_adapter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_KEY_ATTR = "FAKEPROV_API_KEY"


def _fake_cfg(**overrides):
    defaults = {
        "slug": "fakeprov",
        "base_url": "https://api.fakeprov.test/v1",
        "api_key_env": FAKE_KEY_ATTR,
        "display_name": "FakeProv",
    }
    defaults.update(overrides)
    return ProviderConfig(**defaults)


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


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(Config, FAKE_KEY_ATTR, "sk-fake-test", raising=False)


# ---------------------------------------------------------------------------
# 1. Adapter construction and protocol conformance
# ---------------------------------------------------------------------------


class TestMakeAdapter:
    def test_returns_provider_adapter(self):
        adapter = make_adapter(_fake_cfg())
        assert isinstance(adapter, ProviderAdapter)

    def test_exposes_request_process_stream(self):
        adapter = make_adapter(_fake_cfg())
        assert callable(adapter.request)
        assert callable(adapter.process)
        assert callable(adapter.stream)


# ---------------------------------------------------------------------------
# 2. request() sets base URL / auth / model prefix
# ---------------------------------------------------------------------------


class TestRequestClientConstruction:
    def test_plain_client_gets_base_url_and_api_key(self, fake_key):
        adapter = make_adapter(_fake_cfg())
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_client = mock_openai.return_value
            mock_client.chat.completions.create.return_value = _mock_openai_response()

            adapter.request(MESSAGES, "test-model")

            mock_openai.assert_called_once()
            kwargs = mock_openai.call_args[1]
            assert kwargs["base_url"] == "https://api.fakeprov.test/v1"
            assert kwargs["api_key"] == "sk-fake-test"

    def test_extra_headers_passed_as_default_headers(self, fake_key):
        adapter = make_adapter(_fake_cfg(extra_headers={"X-Custom": "yes"}))
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = (
                _mock_openai_response()
            )
            adapter.request(MESSAGES, "test-model")
            assert mock_openai.call_args[1]["default_headers"] == {"X-Custom": "yes"}

    def test_client_factory_used_instead_of_plain_client(self, fake_key):
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = _mock_openai_response()
        factory = Mock(return_value=mock_client)

        adapter = make_adapter(_fake_cfg(client_factory=factory))
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            response = adapter.request(MESSAGES, "test-model")

        factory.assert_called_once_with()
        mock_openai.assert_not_called()
        assert response is not None
        mock_client.chat.completions.create.assert_called_once()

    def test_missing_api_key_raises_value_error(self, monkeypatch):
        monkeypatch.setattr(Config, FAKE_KEY_ATTR, None, raising=False)
        adapter = make_adapter(_fake_cfg())
        with pytest.raises(ValueError, match="FakeProv API key not configured"):
            adapter.request(MESSAGES, "test-model")

    def test_model_prefix_stripped(self, fake_key):
        adapter = make_adapter(_fake_cfg(model_prefix="fakeprov/"))
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            create = mock_openai.return_value.chat.completions.create
            create.return_value = _mock_openai_response()

            adapter.request(MESSAGES, "fakeprov/llama-3-70b")

            assert create.call_args[1]["model"] == "llama-3-70b"

    def test_model_without_prefix_passes_through(self, fake_key):
        adapter = make_adapter(_fake_cfg(model_prefix="fakeprov/"))
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            create = mock_openai.return_value.chat.completions.create
            create.return_value = _mock_openai_response()

            adapter.request(MESSAGES, "llama-3-70b")

            assert create.call_args[1]["model"] == "llama-3-70b"

    def test_forwards_messages_and_kwargs(self, fake_key):
        adapter = make_adapter(_fake_cfg())
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            create = mock_openai.return_value.chat.completions.create
            create.return_value = _mock_openai_response()

            adapter.request(MESSAGES, "test-model", max_tokens=42, temperature=0.5)

            kwargs = create.call_args[1]
            assert kwargs["messages"] == MESSAGES
            assert kwargs["max_tokens"] == 42
            assert kwargs["temperature"] == 0.5
            assert "stream" not in kwargs

    def test_request_propagates_provider_error(self, fake_key):
        adapter = make_adapter(_fake_cfg())
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = Exception(
                "API Error"
            )
            with pytest.raises(Exception, match="API Error"):
                adapter.request(MESSAGES, "test-model")


# ---------------------------------------------------------------------------
# 3. stream() passes stream=True and returns the stream untouched
# ---------------------------------------------------------------------------


class TestStream:
    def test_stream_flag_and_passthrough(self, fake_key):
        adapter = make_adapter(_fake_cfg())
        sentinel_stream = Mock(name="sse_stream")
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            create = mock_openai.return_value.chat.completions.create
            create.return_value = sentinel_stream

            result = adapter.stream(MESSAGES, "test-model")

            # SSE chunks pass through unmodified: the SDK stream object is
            # returned as-is, exactly like the old make_*_request_openai_stream.
            assert result is sentinel_stream
            assert create.call_args[1]["stream"] is True

    def test_stream_propagates_error(self, fake_key):
        adapter = make_adapter(_fake_cfg())
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = Exception(
                "Stream Error"
            )
            with pytest.raises(Exception, match="Stream Error"):
                adapter.stream(MESSAGES, "test-model")


# ---------------------------------------------------------------------------
# 4. process() normalizes the OpenAI-shape response
# ---------------------------------------------------------------------------


class TestProcess:
    def test_extracts_fields(self):
        adapter = make_adapter(_fake_cfg())
        processed = adapter.process(_mock_openai_response())

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["created"] == 1234567890
        assert processed["model"] == "test-model"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"] == {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }

    def test_no_usage_returns_empty_dict(self):
        adapter = make_adapter(_fake_cfg())
        response = _mock_openai_response()
        response.usage = None

        processed = adapter.process(response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["usage"] == {}


# ---------------------------------------------------------------------------
# 5. Quirks: circuit breaker, sentry, timing (old groq/together middleware)
# ---------------------------------------------------------------------------


class TestQuirks:
    def _cb_cfg(self):
        return _fake_cfg(
            quirks=Quirks(circuit_breaker=CircuitBreakerConfig(), sentry=True)
        )

    def test_circuit_breaker_wraps_call(self, fake_key):
        adapter = make_adapter(self._cb_cfg())
        breaker = Mock()
        breaker.call.return_value = _mock_openai_response()
        with patch(
            "src.services.providers.openai_compat.get_circuit_breaker",
            return_value=breaker,
        ) as mock_get:
            response = adapter.request(MESSAGES, "test-model")

        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == "fakeprov"
        breaker.call.assert_called_once()
        assert response is breaker.call.return_value

    def test_circuit_breaker_error_propagates_and_captures(self, fake_key):
        adapter = make_adapter(self._cb_cfg())
        cb_error = CircuitBreakerError("fakeprov", CircuitState.OPEN)
        breaker = Mock()
        breaker.call.side_effect = cb_error
        with patch(
            "src.services.providers.openai_compat.get_circuit_breaker",
            return_value=breaker,
        ):
            with patch(
                "src.utils.sentry_context.capture_provider_error"
            ) as mock_capture:
                with pytest.raises(CircuitBreakerError):
                    adapter.request(MESSAGES, "test-model")

        mock_capture.assert_called_once()
        assert mock_capture.call_args[1]["provider"] == "fakeprov"
        assert mock_capture.call_args[1]["extra_context"] == {
            "circuit_breaker_state": "open"
        }

    def test_sentry_captures_generic_error(self, fake_key):
        adapter = make_adapter(_fake_cfg(quirks=Quirks(sentry=True)))
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = Exception(
                "boom"
            )
            with patch(
                "src.utils.sentry_context.capture_provider_error"
            ) as mock_capture:
                with pytest.raises(Exception, match="boom"):
                    adapter.request(MESSAGES, "test-model")

        mock_capture.assert_called_once()
        assert mock_capture.call_args[1]["provider"] == "fakeprov"
        assert mock_capture.call_args[1]["model"] == "test-model"

    def test_no_sentry_capture_without_quirk(self, fake_key):
        adapter = make_adapter(_fake_cfg())
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = Exception(
                "boom"
            )
            with patch(
                "src.utils.sentry_context.capture_provider_error"
            ) as mock_capture:
                with pytest.raises(Exception, match="boom"):
                    adapter.request(MESSAGES, "test-model")

        mock_capture.assert_not_called()

    def test_timing_context_used_when_enabled(self, fake_key):
        adapter = make_adapter(_fake_cfg(quirks=Quirks(timing=True)))
        with patch("src.services.providers.openai_compat.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = (
                _mock_openai_response()
            )
            with patch("src.utils.provider_timing.ProviderTimingContext") as mock_ctx:
                adapter.request(MESSAGES, "test-model")

        mock_ctx.assert_called_once_with("fakeprov", "test-model", "non_stream")


# ---------------------------------------------------------------------------
# 6. ADAPTERS registry: the five consolidated providers
# ---------------------------------------------------------------------------

CONSOLIDATED = ["deepinfra", "together", "fireworks", "groq", "zai"]

EXPECTED_CONFIG = {
    # slug -> (base_url, api_key_env, error_match, has_factory)
    "deepinfra": (
        "https://api.deepinfra.com/v1/openai",
        "DEEPINFRA_API_KEY",
        "DeepInfra API key not configured",
        False,  # parity: old client built a plain OpenAI client per request
    ),
    "together": (
        "https://api.together.xyz/v1",
        "TOGETHER_API_KEY",
        "Together API key not configured",
        True,
    ),
    "fireworks": (
        "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY",
        "Fireworks API key not configured",
        True,
    ),
    "groq": (
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "Groq API key not configured",
        True,
    ),
    "zai": (
        "https://api.z.ai/api/paas/v4",
        "ZAI_API_KEY",
        "Z.AI API key not configured",
        True,
    ),
}


class TestAdapterRegistry:
    def test_contains_exactly_the_consolidated_slugs(self):
        from src.services.providers.adapter_configs import ADAPTERS

        assert set(ADAPTERS) == set(CONSOLIDATED)

    def test_all_entries_satisfy_protocol(self):
        from src.services.providers.adapter_configs import ADAPTERS

        for slug, adapter in ADAPTERS.items():
            assert isinstance(adapter, ProviderAdapter), slug

    @pytest.mark.parametrize("slug", CONSOLIDATED)
    def test_config_matches_old_client(self, slug):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        base_url, key_env, _, has_factory = EXPECTED_CONFIG[slug]
        cfg = ADAPTER_CONFIGS[slug]
        assert cfg.base_url.rstrip("/") == base_url.rstrip("/")
        assert cfg.api_key_env == key_env
        assert (cfg.client_factory is not None) is has_factory

    def test_groq_and_together_keep_circuit_breaker_and_sentry(self):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        for slug in ("groq", "together"):
            quirks = ADAPTER_CONFIGS[slug].quirks
            assert quirks is not None, slug
            assert quirks.circuit_breaker is not None, slug
            assert quirks.circuit_breaker.failure_threshold == 5
            assert quirks.circuit_breaker.success_threshold == 2
            assert quirks.circuit_breaker.timeout_seconds == 60
            assert quirks.sentry is True, slug

    def test_groq_keeps_timing(self):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        assert ADAPTER_CONFIGS["groq"].quirks.timing is True

    def test_others_have_no_middleware(self):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS

        for slug in ("deepinfra", "fireworks", "zai"):
            quirks = ADAPTER_CONFIGS[slug].quirks or Quirks()
            assert quirks.circuit_breaker is None, slug
            assert quirks.sentry is False, slug
            assert quirks.timing is False, slug


# ---------------------------------------------------------------------------
# 7. Behavioral parity for real adapters
#    (migrated from test_provider_clients_parametrized.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", CONSOLIDATED)
class TestConsolidatedProviderParity:
    def _adapter(self, slug):
        from src.services.providers.adapter_configs import ADAPTERS

        return ADAPTERS[slug]

    def test_missing_key_raises_value_error(self, slug, monkeypatch):
        _, key_env, error_match, _ = EXPECTED_CONFIG[slug]
        monkeypatch.setattr(Config, key_env, None, raising=False)
        adapter = self._adapter(slug)
        with pytest.raises(ValueError, match=error_match):
            adapter.request(MESSAGES, "test-model")

    def test_forwards_to_create(self, slug):
        adapter = self._adapter(slug)
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = _mock_openai_response()

        with patch.object(
            OpenAICompatAdapter, "_get_client", return_value=mock_client
        ):
            response = adapter.request(MESSAGES, "test-model")

        assert response is not None
        mock_client.chat.completions.create.assert_called_once()

    def test_request_propagates_error(self, slug):
        adapter = self._adapter(slug)
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        with patch.object(
            OpenAICompatAdapter, "_get_client", return_value=mock_client
        ):
            with pytest.raises(Exception, match="API Error"):
                adapter.request(MESSAGES, "test-model")

    def test_stream_flag(self, slug):
        adapter = self._adapter(slug)
        mock_client = Mock()
        sentinel = Mock(name="stream")
        mock_client.chat.completions.create.return_value = sentinel

        with patch.object(
            OpenAICompatAdapter, "_get_client", return_value=mock_client
        ):
            result = adapter.stream(MESSAGES, "test-model")

        assert result is sentinel
        assert mock_client.chat.completions.create.call_args[1]["stream"] is True

    def test_stream_propagates_error(self, slug):
        adapter = self._adapter(slug)
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")

        with patch.object(
            OpenAICompatAdapter, "_get_client", return_value=mock_client
        ):
            with pytest.raises(Exception, match="Stream Error"):
                adapter.stream(MESSAGES, "test-model")

    def test_process_extracts_fields(self, slug):
        adapter = self._adapter(slug)
        processed = adapter.process(_mock_openai_response())

        assert processed["id"] == "chatcmpl-test123"
        assert processed["model"] == "test-model"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30

    def test_process_no_usage(self, slug):
        adapter = self._adapter(slug)
        response = _mock_openai_response()
        response.usage = None
        assert adapter.process(response)["usage"] == {}


# ---------------------------------------------------------------------------
# 8. Registry integration: PROVIDER_ROUTING serves the adapter trio
# ---------------------------------------------------------------------------


# Providers whose PROVIDER_ROUTING entry has been migrated to the adapter.
MIGRATED = CONSOLIDATED


class TestProviderRoutingIntegration:
    def test_routing_entries_are_adapter_bound(self):
        from src.handlers.provider_registry import PROVIDER_ROUTING
        from src.services.providers.adapter_configs import ADAPTERS

        for slug in MIGRATED:
            if slug not in PROVIDER_ROUTING:
                continue  # provider disabled in this environment
            routing = PROVIDER_ROUTING[slug]
            adapter = ADAPTERS[slug]
            assert routing["request"] == adapter.request
            assert routing["process"] == adapter.process
            assert routing["stream"] == adapter.stream
