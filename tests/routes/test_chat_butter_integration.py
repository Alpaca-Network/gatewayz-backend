"""
Tests for Butter.dev integration in chat completions route.

These tests verify that the Butter.dev caching proxy is correctly integrated
into the chat completions flow.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set test environment before imports
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("APP_ENV", "testing")


class TestButterProviderConfig:
    """Test BUTTER_PROVIDER_CONFIG mapping."""

    def test_openrouter_config_exists(self):
        """Test that OpenRouter config is defined."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG

        assert "openrouter" in BUTTER_PROVIDER_CONFIG
        assert BUTTER_PROVIDER_CONFIG["openrouter"]["api_key_attr"] == "OPENROUTER_API_KEY"
        assert "openrouter.ai" in BUTTER_PROVIDER_CONFIG["openrouter"]["base_url"]

    def test_together_config_exists(self):
        """Test that Together config is defined."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG

        assert "together" in BUTTER_PROVIDER_CONFIG
        assert BUTTER_PROVIDER_CONFIG["together"]["api_key_attr"] == "TOGETHER_API_KEY"
        assert "together.xyz" in BUTTER_PROVIDER_CONFIG["together"]["base_url"]

    def test_fireworks_config_exists(self):
        """Test that Fireworks config is defined."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG

        assert "fireworks" in BUTTER_PROVIDER_CONFIG
        assert BUTTER_PROVIDER_CONFIG["fireworks"]["api_key_attr"] == "FIREWORKS_API_KEY"
        assert "fireworks.ai" in BUTTER_PROVIDER_CONFIG["fireworks"]["base_url"]

    def test_groq_config_exists(self):
        """Test that Groq config is defined."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG

        assert "groq" in BUTTER_PROVIDER_CONFIG
        assert BUTTER_PROVIDER_CONFIG["groq"]["api_key_attr"] == "GROQ_API_KEY"
        assert "groq.com" in BUTTER_PROVIDER_CONFIG["groq"]["base_url"]

    def test_onerouter_config_exists(self):
        """Test that OneRouter/Infron config is defined - this is the default provider."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG

        assert "onerouter" in BUTTER_PROVIDER_CONFIG
        assert BUTTER_PROVIDER_CONFIG["onerouter"]["api_key_attr"] == "ONEROUTER_API_KEY"
        assert "infron.ai" in BUTTER_PROVIDER_CONFIG["onerouter"]["base_url"]

    def test_all_compatible_providers_have_config(self):
        """Test that all providers in BUTTER_COMPATIBLE_PROVIDERS have a config entry."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG
        from src.services.butter_client import BUTTER_COMPATIBLE_PROVIDERS

        # These providers are in compatible list but may not have Butter proxy config
        # because they use special authentication or non-OpenAI API formats
        providers_without_config = {
            "cloudflare-workers-ai",  # Uses account-specific URL
            "alpaca-network",  # Uses special auth
            "vercel-ai-gateway",  # Uses Vercel-specific auth
            "perplexity",  # Not commonly used
        }

        for provider in BUTTER_COMPATIBLE_PROVIDERS:
            if provider not in providers_without_config:
                assert provider in BUTTER_PROVIDER_CONFIG, (
                    f"Provider '{provider}' is in BUTTER_COMPATIBLE_PROVIDERS "
                    f"but missing from BUTTER_PROVIDER_CONFIG"
                )

    def test_all_configs_have_required_fields(self):
        """Test that all provider configs have required fields."""
        from src.routes.chat import BUTTER_PROVIDER_CONFIG

        for provider, config in BUTTER_PROVIDER_CONFIG.items():
            assert "api_key_attr" in config, f"{provider} missing api_key_attr"
            assert "base_url" in config, f"{provider} missing base_url"
            assert config["api_key_attr"].endswith(
                "_API_KEY"
            ), f"{provider} api_key_attr should end with _API_KEY"
            assert config["base_url"].startswith("https://"), f"{provider} base_url should be HTTPS"


class TestMakeButterProxiedStream:
    """Test the make_butter_proxied_stream function."""

    @pytest.mark.asyncio
    async def test_raises_for_unknown_provider(self):
        """Test that unknown provider raises ValueError."""
        from src.routes.chat import make_butter_proxied_stream

        with pytest.raises(ValueError, match="not configured"):
            await make_butter_proxied_stream(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
                provider="unknown-provider",
            )

    @pytest.mark.asyncio
    @patch("src.routes.chat.Config")
    async def test_raises_for_missing_api_key(self, mock_config):
        """Test that missing API key raises ValueError."""
        from src.routes.chat import make_butter_proxied_stream

        # Set OPENROUTER_API_KEY to None
        mock_config.OPENROUTER_API_KEY = None

        with pytest.raises(ValueError, match="API key not configured"):
            await make_butter_proxied_stream(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
                provider="openrouter",
            )

    @pytest.mark.asyncio
    @patch("src.routes.chat.get_butter_pooled_async_client")
    @patch("src.routes.chat.Config")
    async def test_creates_butter_client_correctly(self, mock_config, mock_get_client):
        """Test that Butter client is created with correct parameters."""
        from src.routes.chat import make_butter_proxied_stream

        # Setup mocks
        mock_config.OPENROUTER_API_KEY = "test-api-key"

        mock_client = MagicMock()
        mock_stream = AsyncMock()
        mock_client.chat.completions.create = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "test"}]

        await make_butter_proxied_stream(
            messages=messages,
            model="gpt-4o",
            provider="openrouter",
            temperature=0.7,
            max_tokens=100,
        )

        # Verify get_butter_pooled_async_client was called correctly
        mock_get_client.assert_called_once_with(
            target_provider="openrouter",
            target_api_key="test-api-key",
            target_base_url="https://openrouter.ai/api/v1",
        )

        # Verify chat.completions.create was called
        mock_stream.assert_called_once()
        call_args = mock_stream.call_args
        assert call_args.kwargs["model"] == "gpt-4o"
        assert call_args.kwargs["messages"] == messages
        assert call_args.kwargs["stream"] is True
        assert call_args.kwargs["temperature"] == 0.7
        assert call_args.kwargs["max_tokens"] == 100


class TestButterIntegrationInChatCompletions:
    """Test Butter.dev integration in the chat completions flow."""

    def test_should_use_butter_cache_is_imported(self):
        """Test that should_use_butter_cache is available in chat module."""
        from src.routes.chat import should_use_butter_cache

        # Verify it's the correct function
        assert callable(should_use_butter_cache)

    def test_butter_cache_timer_is_imported(self):
        """Test that ButterCacheTimer is available in chat module."""
        from src.routes.chat import ButterCacheTimer

        # Verify it's the correct class
        assert callable(ButterCacheTimer)

    def test_get_butter_pooled_async_client_is_imported(self):
        """Test that get_butter_pooled_async_client is available in chat module."""
        from src.routes.chat import get_butter_pooled_async_client

        # Verify it's the correct function
        assert callable(get_butter_pooled_async_client)


class TestButterCacheHeaderLogic:
    """Test the X-Butter-Cache header logic."""

    @patch("src.services.butter_client.Config")
    def test_butter_reason_codes(self, mock_config):
        """Test all possible reason codes from should_use_butter_cache."""
        from src.services.butter_client import should_use_butter_cache

        # Test system_disabled
        mock_config.BUTTER_DEV_ENABLED = False
        use_cache, reason = should_use_butter_cache(
            {"id": 1, "preferences": {"enable_butter_cache": True}}, "openrouter"
        )
        assert reason == "system_disabled"

        # Test anonymous_user
        mock_config.BUTTER_DEV_ENABLED = True
        use_cache, reason = should_use_butter_cache(None, "openrouter")
        assert reason == "anonymous_user"

        # Test user_preference_disabled
        use_cache, reason = should_use_butter_cache(
            {"id": 1, "preferences": {"enable_butter_cache": False}}, "openrouter"
        )
        assert reason == "user_preference_disabled"

        # Test provider_incompatible
        use_cache, reason = should_use_butter_cache(
            {"id": 1, "preferences": {"enable_butter_cache": True}}, "anthropic"
        )
        assert "provider_incompatible" in reason

        # Test enabled
        use_cache, reason = should_use_butter_cache(
            {"id": 1, "preferences": {"enable_butter_cache": True}}, "openrouter"
        )
        assert reason == "enabled"
        assert use_cache is True
