"""
Unit tests for the Moonshot (Kimi) and MiniMax direct provider clients.

Both providers are OpenAI-compatible and mirror the together_client.py /
deepinfra_client.py wiring pattern: pooled OpenAI client + circuit breaker
protected make_<provider>_request_openai[_stream]().

These tests mock the underlying OpenAI client and connection-pool factory —
no real API keys or network calls are used.
"""

import importlib
from unittest.mock import Mock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. make_<provider>_request_openai posts to the right base_url / model / auth
# ---------------------------------------------------------------------------


class TestMoonshotRequest:
    def test_get_client_raises_without_api_key(self):
        from src.services.providers import moonshot_client

        with patch.object(moonshot_client.Config, "MOONSHOT_API_KEY", None):
            with pytest.raises(ValueError, match="Moonshot API key not configured"):
                moonshot_client.get_moonshot_client()

    def test_pooled_client_uses_correct_base_url_and_key(self):
        """get_moonshot_pooled_client must call get_pooled_client with Moonshot's
        base_url and the configured API key (i.e. the auth header source)."""
        from src.services import connection_pool

        with patch.object(connection_pool.Config, "MOONSHOT_API_KEY", "test-moonshot-key"):
            with patch.object(connection_pool, "get_pooled_client") as mock_get_pooled:
                mock_get_pooled.return_value = Mock()
                connection_pool.get_moonshot_pooled_client()

                mock_get_pooled.assert_called_once_with(
                    provider="moonshot",
                    base_url="https://api.moonshot.ai/v1",
                    api_key="test-moonshot-key",
                )

    def test_make_request_forwards_model_and_messages(self):
        from src.services.providers import moonshot_client

        mock_client = Mock()
        mock_response = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(
            moonshot_client, "get_moonshot_client", return_value=mock_client
        ):
            messages = [{"role": "user", "content": "hi"}]
            response = moonshot_client.make_moonshot_request_openai(
                messages, "kimi-k2-0711-preview", temperature=0.5
            )

            assert response is mock_response
            mock_client.chat.completions.create.assert_called_once_with(
                model="kimi-k2-0711-preview", messages=messages, temperature=0.5
            )

    def test_make_request_stream_sets_stream_true(self):
        from src.services.providers import moonshot_client

        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream

        with patch.object(
            moonshot_client, "get_moonshot_client", return_value=mock_client
        ):
            messages = [{"role": "user", "content": "hi"}]
            result = moonshot_client.make_moonshot_request_openai_stream(
                messages, "moonshot-v1-8k"
            )

            assert result is mock_stream
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["stream"] is True
            assert call_kwargs["model"] == "moonshot-v1-8k"


class TestMinimaxRequest:
    def test_get_client_raises_without_api_key(self):
        from src.services.providers import minimax_client

        with patch.object(minimax_client.Config, "MINIMAX_API_KEY", None):
            with pytest.raises(ValueError, match="MiniMax API key not configured"):
                minimax_client.get_minimax_client()

    def test_pooled_client_uses_correct_base_url_and_key(self):
        from src.services import connection_pool

        with patch.object(connection_pool.Config, "MINIMAX_API_KEY", "test-minimax-key"):
            with patch.object(connection_pool, "get_pooled_client") as mock_get_pooled:
                mock_get_pooled.return_value = Mock()
                connection_pool.get_minimax_pooled_client()

                mock_get_pooled.assert_called_once_with(
                    provider="minimax",
                    base_url="https://api.minimaxi.com/v1",
                    api_key="test-minimax-key",
                )

    def test_make_request_forwards_model_and_messages(self):
        from src.services.providers import minimax_client

        mock_client = Mock()
        mock_response = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(
            minimax_client, "get_minimax_client", return_value=mock_client
        ):
            messages = [{"role": "user", "content": "hi"}]
            response = minimax_client.make_minimax_request_openai(
                messages, "MiniMax-Text-01", max_tokens=100
            )

            assert response is mock_response
            mock_client.chat.completions.create.assert_called_once_with(
                model="MiniMax-Text-01", messages=messages, max_tokens=100
            )

    def test_make_request_stream_sets_stream_true(self):
        from src.services.providers import minimax_client

        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream

        with patch.object(
            minimax_client, "get_minimax_client", return_value=mock_client
        ):
            messages = [{"role": "user", "content": "hi"}]
            result = minimax_client.make_minimax_request_openai_stream(
                messages, "MiniMax-M1"
            )

            assert result is mock_stream
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["stream"] is True
            assert call_kwargs["model"] == "MiniMax-M1"


# ---------------------------------------------------------------------------
# 2. process_<provider>_response normalizes the SDK response
# ---------------------------------------------------------------------------


def _make_mock_response():
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


class TestProcessResponses:
    def test_process_moonshot_response(self):
        from src.services.providers.moonshot_client import process_moonshot_response

        processed = process_moonshot_response(_make_mock_response())
        assert processed["id"] == "chatcmpl-test123"
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30

    def test_process_minimax_response(self):
        from src.services.providers.minimax_client import process_minimax_response

        processed = process_minimax_response(_make_mock_response())
        assert processed["id"] == "chatcmpl-test123"
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30


# ---------------------------------------------------------------------------
# 3. fetch_models_from_moonshot / fetch_models_from_minimax
# ---------------------------------------------------------------------------


class TestFetchModelsFromMoonshot:
    def test_no_api_key_returns_none(self):
        from src.services.providers import moonshot_client

        with patch.object(moonshot_client.Config, "MOONSHOT_API_KEY", None):
            assert moonshot_client.fetch_models_from_moonshot() is None

    def test_calls_models_endpoint(self):
        """fetch_models_from_moonshot must GET https://api.moonshot.ai/v1/models
        with a bearer auth header derived from MOONSHOT_API_KEY."""
        from src.services.providers import moonshot_client

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {"id": "moonshot-v1-8k", "context_length": 8192},
                {"id": "kimi-k2-0711-preview", "context_length": 131072},
            ]
        }
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()

        with (
            patch.object(moonshot_client.Config, "MOONSHOT_API_KEY", "test-key"),
            patch.object(moonshot_client.httpx, "get", return_value=mock_response) as mock_get,
            patch.object(moonshot_client, "cache_gateway_catalog"),
        ):
            models = moonshot_client.fetch_models_from_moonshot()

            mock_get.assert_called_once()
            call_args, call_kwargs = mock_get.call_args
            url = call_args[0] if call_args else call_kwargs.get("url")
            assert url == "https://api.moonshot.ai/v1/models"
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-key"

            assert models is not None
            ids = {m["id"] for m in models}
            assert {"moonshot-v1-8k", "kimi-k2-0711-preview"} <= ids


class TestFetchModelsFromMinimax:
    def test_no_api_key_returns_none(self):
        from src.services.providers import minimax_client

        with patch.object(minimax_client.Config, "MINIMAX_API_KEY", None):
            assert minimax_client.fetch_models_from_minimax() is None

    def test_returns_static_known_models_without_http_call(self):
        """MiniMax has no reliable /models endpoint, so this must return the
        static MINIMAX_KNOWN_MODELS list rather than making an HTTP request."""
        from src.services.providers import minimax_client

        with (
            patch.object(minimax_client.Config, "MINIMAX_API_KEY", "test-key"),
            patch.object(minimax_client, "cache_gateway_catalog"),
        ):
            models = minimax_client.fetch_models_from_minimax()

            assert models is not None
            ids = {m["id"] for m in models}
            assert ids == {"MiniMax-M1", "MiniMax-Text-01"}


# ---------------------------------------------------------------------------
# 4. Registration: both slugs are wired into PROVIDER_FETCH_FUNCTIONS and
#    PROVIDER_ENV_VAR_MAP so the sync pipeline and env-var mapping pick
#    them up.
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_fetch_functions_registered(self):
        from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS
        from src.services.providers.minimax_client import fetch_models_from_minimax
        from src.services.providers.moonshot_client import fetch_models_from_moonshot

        assert PROVIDER_FETCH_FUNCTIONS["moonshot"] is fetch_models_from_moonshot
        assert PROVIDER_FETCH_FUNCTIONS["minimax"] is fetch_models_from_minimax

    def test_env_var_map_registered(self):
        from src.services.provider_model_sync_service import PROVIDER_ENV_VAR_MAP

        assert PROVIDER_ENV_VAR_MAP["moonshot"] == "MOONSHOT_API_KEY"
        assert PROVIDER_ENV_VAR_MAP["minimax"] == "MINIMAX_API_KEY"

    def test_dispatch_registry_wires_both_providers(self):
        """The chat dispatch map (src/handlers/provider_registry.py) must
        expose request/process/stream callables for both slugs when enabled."""
        import os

        prev = os.environ.get("ENABLED_PROVIDERS")
        os.environ["ENABLED_PROVIDERS"] = "openai,anthropic,together,moonshot,minimax"
        try:
            import src.config.config as config_module

            importlib.reload(config_module)

            import src.handlers.provider_registry as provider_registry

            importlib.reload(provider_registry)

            assert "moonshot" in provider_registry.PROVIDER_ROUTING
            assert "minimax" in provider_registry.PROVIDER_ROUTING
            assert callable(provider_registry.PROVIDER_ROUTING["moonshot"]["request"])
            assert callable(provider_registry.PROVIDER_ROUTING["minimax"]["request"])
        finally:
            if prev is None:
                os.environ.pop("ENABLED_PROVIDERS", None)
            else:
                os.environ["ENABLED_PROVIDERS"] = prev
            importlib.reload(config_module)
            importlib.reload(provider_registry)

    def test_config_has_api_key_attrs(self):
        from src.config.config import Config

        assert hasattr(Config, "MOONSHOT_API_KEY")
        assert hasattr(Config, "MINIMAX_API_KEY")
