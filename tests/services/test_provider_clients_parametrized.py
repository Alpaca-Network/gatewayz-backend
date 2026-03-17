"""
Parametrized tests for OpenAI-compatible provider clients.

All simple providers follow the same pattern:
  - get_<provider>_client() -> OpenAI client
  - make_<provider>_request_openai(messages, model, **kwargs) -> response
  - make_<provider>_request_openai_stream(messages, model, **kwargs) -> stream
  - process_<provider>_response(response) -> dict

This file tests all of them with shared parametrized fixtures instead of
duplicating identical logic across 20+ files.
"""

import importlib
from unittest.mock import Mock, patch

import pytest

# ---------------------------------------------------------------------------
# Provider configuration table
# ---------------------------------------------------------------------------
# Each entry: (module_path, provider_name, config_key_attr, error_match)
#
# config_key_attr: the attribute on Config that must be None to trigger
#                  the "no key" ValueError.
# error_match:     regex to match against the ValueError message.
# ---------------------------------------------------------------------------

SIMPLE_PROVIDERS = [
    (
        "src.services.fireworks_client",
        "fireworks",
        "FIREWORKS_API_KEY",
        "Fireworks API key not configured",
    ),
    ("src.services.groq_client", "groq", "GROQ_API_KEY", "Groq API key not configured"),
    (
        "src.services.together_client",
        "together",
        "TOGETHER_API_KEY",
        "Together API key not configured",
    ),
    ("src.services.akash_client", "akash", "AKASH_API_KEY", "Akash API key not configured"),
    ("src.services.openai_client", "openai", "OPENAI_API_KEY", "OpenAI API key not configured"),
    ("src.services.zai_client", "zai", "ZAI_API_KEY", "Z.AI API key not configured"),
    (
        "src.services.helicone_client",
        "helicone",
        "HELICONE_API_KEY",
        "Helicone AI Gateway API key not configured",
    ),
    (
        "src.services.featherless_client",
        "featherless",
        "FEATHERLESS_API_KEY",
        "Featherless API key not configured",
    ),
    (
        "src.services.onerouter_client",
        "onerouter",
        "ONEROUTER_API_KEY",
        "Infron AI API key not configured",
    ),
    (
        "src.services.morpheus_client",
        "morpheus",
        "MORPHEUS_API_KEY",
        "Morpheus API key not configured",
    ),
    ("src.services.aihubmix_client", "aihubmix", "AIHUBMIX_API_KEY", "API key not configured"),
    (
        "src.services.anthropic_client",
        "anthropic",
        "ANTHROPIC_API_KEY",
        "Anthropic API key not configured",
    ),
]


def _provider_id(param):
    """Use provider name as test ID."""
    return param[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(module_path):
    return importlib.import_module(module_path)


def _get_func(module, provider, suffix):
    """Resolve a provider function by naming convention.

    OpenAI client uses make_openai_request / make_openai_request_stream
    (no _openai suffix). All others use make_<name>_request_openai.
    """
    if provider == "openai":
        name_map = {
            "get_client": "get_openai_client",
            "make_request": "make_openai_request",
            "make_stream": "make_openai_request_stream",
            "process_response": "process_openai_response",
        }
    elif provider == "anthropic":
        name_map = {
            "get_client": "get_anthropic_client",
            "make_request": "make_anthropic_request",
            "make_stream": "make_anthropic_request_stream",
            "process_response": "process_anthropic_response",
        }
    else:
        name_map = {
            "get_client": f"get_{provider}_client",
            "make_request": f"make_{provider}_request_openai",
            "make_stream": f"make_{provider}_request_openai_stream",
            "process_response": f"process_{provider}_response",
        }
    return getattr(module, name_map[suffix])


# ---------------------------------------------------------------------------
# 1. get_client raises ValueError when API key is None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path, provider, config_key, error_match",
    SIMPLE_PROVIDERS,
    ids=[p[1] for p in SIMPLE_PROVIDERS],
)
class TestGetClientMissingKey:
    """get_<provider>_client() must raise ValueError when the API key is not set."""

    def test_raises_value_error(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        get_client = _get_func(mod, provider, "get_client")

        with patch(f"{module_path}.Config") as mock_config:
            setattr(mock_config, config_key, None)
            # aihubmix also checks APP_CODE -- set it so we isolate the key check
            if provider == "aihubmix":
                mock_config.AIHUBMIX_APP_CODE = "TEST"
            with pytest.raises(ValueError, match=error_match):
                get_client()


# ---------------------------------------------------------------------------
# 2. make_request forwards to client.chat.completions.create
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path, provider, config_key, error_match",
    SIMPLE_PROVIDERS,
    ids=[p[1] for p in SIMPLE_PROVIDERS],
)
class TestMakeRequest:
    """make_<provider>_request_openai() forwards to chat.completions.create."""

    def test_forwards_to_create(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        make_request = _get_func(mod, provider, "make_request")
        get_client_path = f"{module_path}.{_get_func(mod, provider, 'get_client').__name__}"

        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "test-model"
        mock_client.chat.completions.create.return_value = mock_response

        with patch(get_client_path, return_value=mock_client):
            messages = [{"role": "user", "content": "Hello"}]
            response = make_request(messages, "test-model")

            assert response is not None
            mock_client.chat.completions.create.assert_called_once()

    def test_propagates_error(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        make_request = _get_func(mod, provider, "make_request")
        get_client_path = f"{module_path}.{_get_func(mod, provider, 'get_client').__name__}"

        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        with patch(get_client_path, return_value=mock_client):
            with pytest.raises(Exception, match="API Error"):
                make_request([{"role": "user", "content": "Hello"}], "test-model")


# ---------------------------------------------------------------------------
# 3. make_request_stream sets stream=True
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path, provider, config_key, error_match",
    SIMPLE_PROVIDERS,
    ids=[p[1] for p in SIMPLE_PROVIDERS],
)
class TestMakeRequestStream:
    """make_<provider>_request_openai_stream() passes stream=True."""

    def test_stream_flag(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        make_stream = _get_func(mod, provider, "make_stream")
        get_client_path = f"{module_path}.{_get_func(mod, provider, 'get_client').__name__}"

        mock_client = Mock()
        mock_stream_obj = Mock()
        mock_client.chat.completions.create.return_value = mock_stream_obj

        with patch(get_client_path, return_value=mock_client):
            messages = [{"role": "user", "content": "Hello"}]
            result = make_stream(messages, "test-model")

            assert result is mock_stream_obj
            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs[1].get("stream") is True or (
                len(call_kwargs[0]) > 0 and True in call_kwargs[0]
            )

    def test_stream_propagates_error(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        make_stream = _get_func(mod, provider, "make_stream")
        get_client_path = f"{module_path}.{_get_func(mod, provider, 'get_client').__name__}"

        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")

        with patch(get_client_path, return_value=mock_client):
            with pytest.raises(Exception, match="Stream Error"):
                make_stream([{"role": "user", "content": "Hello"}], "test-model")


# ---------------------------------------------------------------------------
# 4. process_response normalizes the OpenAI SDK response to a dict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path, provider, config_key, error_match",
    SIMPLE_PROVIDERS,
    ids=[p[1] for p in SIMPLE_PROVIDERS],
)
class TestProcessResponse:
    """process_<provider>_response() extracts id, model, choices, usage."""

    def _make_mock_response(self):
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

    def test_extracts_fields(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        process_response = _get_func(mod, provider, "process_response")
        mock_resp = self._make_mock_response()

        processed = process_response(mock_resp)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["model"] == "test-model"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30

    def test_no_usage(self, module_path, provider, config_key, error_match):
        mod = _load_module(module_path)
        process_response = _get_func(mod, provider, "process_response")

        mock_resp = self._make_mock_response()
        mock_resp.usage = None

        processed = process_response(mock_resp)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["usage"] == {}


# ---------------------------------------------------------------------------
# 5. Module import smoke tests (covers stubs and all providers)
# ---------------------------------------------------------------------------

ALL_PROVIDER_MODULES = [
    "src.services.fireworks_client",
    "src.services.groq_client",
    "src.services.together_client",
    "src.services.akash_client",
    "src.services.openai_client",
    "src.services.zai_client",
    "src.services.helicone_client",
    "src.services.featherless_client",
    "src.services.onerouter_client",
    "src.services.morpheus_client",
    "src.services.aihubmix_client",
    "src.services.anthropic_client",
    "src.services.vercel_ai_gateway_client",
    "src.services.near_client",
    "src.services.modelz_client",
    "src.services.deepinfra_client",
    "src.services.chutes_client",
    "src.services.aimo_client",
    "src.services.nebius_client",
    "src.services.alpaca_network_client",
    "src.services.ai_sdk_client",
    "src.services.clarifai_client",
    "src.services.anannas_client",
]


@pytest.mark.parametrize(
    "module_path", ALL_PROVIDER_MODULES, ids=[m.split(".")[-1] for m in ALL_PROVIDER_MODULES]
)
def test_provider_module_imports(module_path):
    """Every provider module must import without errors."""
    mod = importlib.import_module(module_path)
    assert mod is not None


# ---------------------------------------------------------------------------
# 6. Provider-specific edge-case tests
# ---------------------------------------------------------------------------


class TestSanitizeMessagesForFeatherless:
    """Test _sanitize_messages_for_featherless function."""

    def test_removes_null_tool_calls_from_dict(self):
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there", "tool_calls": None},
            {"role": "user", "content": "How are you?"},
        ]
        sanitized = _sanitize_messages_for_featherless(messages)
        assert len(sanitized) == 3
        assert "tool_calls" not in sanitized[1]

    def test_preserves_valid_tool_calls(self):
        from src.services.featherless_client import _sanitize_messages_for_featherless

        tool_calls = [{"id": "call_123", "function": {"name": "test", "arguments": "{}"}}]
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": None, "tool_calls": tool_calls},
        ]
        sanitized = _sanitize_messages_for_featherless(messages)
        assert "tool_calls" in sanitized[1]
        assert sanitized[1]["tool_calls"] == tool_calls

    def test_preserves_messages_without_tool_calls(self):
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        sanitized = _sanitize_messages_for_featherless(messages)
        assert sanitized == messages

    def test_does_not_mutate_original_messages(self):
        from src.services.featherless_client import _sanitize_messages_for_featherless

        original = {"role": "assistant", "content": "Hi", "tool_calls": None}
        messages = [original]
        _sanitize_messages_for_featherless(messages)
        assert "tool_calls" in original
        assert original["tool_calls"] is None

    def test_removes_invalid_tool_calls_type(self):
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [{"role": "assistant", "content": "Hi", "tool_calls": "invalid_string"}]
        sanitized = _sanitize_messages_for_featherless(messages)
        assert "tool_calls" not in sanitized[0]

    def test_handles_empty_list(self):
        from src.services.featherless_client import _sanitize_messages_for_featherless

        assert _sanitize_messages_for_featherless([]) == []
