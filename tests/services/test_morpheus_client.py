"""Tests for Morpheus-specific functionality.

Standard get_client / make_request / stream / process_response tests are
in test_provider_clients_parametrized.py.
"""

from unittest.mock import Mock, patch

import pytest


class TestMorpheusBaseUrl:
    """Test Morpheus base URL"""

    def test_morpheus_base_url(self):
        from src.services.morpheus_client import MORPHEUS_BASE_URL

        assert MORPHEUS_BASE_URL == "https://api.mor.org/api/v1"


class TestFetchModelsFromMorpheus:
    """Test fetch_models_from_morpheus and related caching."""

    @patch("httpx.get")
    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_success(self, mock_config, mock_httpx_get):
        """Test fetching models from Morpheus API"""
        mock_config.MORPHEUS_API_KEY = "test-key"

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {"id": "llama-3.1-8b", "context_length": 8192},
                {"id": "mistral-7b", "context_length": 4096},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_get.return_value = mock_response

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()

        assert len(models) == 2
        assert models[0]["id"] == "morpheus/llama-3.1-8b"
        assert models[0]["provider_slug"] == "morpheus"
        assert models[1]["id"] == "morpheus/mistral-7b"

    @patch("httpx.get")
    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_updates_cache_on_success(self, mock_config, mock_httpx_get):
        """Test that cache timestamp is updated after successful fetch"""
        from src.cache import _morpheus_models_cache, clear_models_cache

        # Clear cache first
        clear_models_cache("morpheus")
        assert _morpheus_models_cache["timestamp"] is None

        mock_config.MORPHEUS_API_KEY = "test-key"
        mock_response = Mock()
        mock_response.json.return_value = {"data": [{"id": "test-model", "context_length": 4096}]}
        mock_response.raise_for_status = Mock()
        mock_httpx_get.return_value = mock_response

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()

        # Verify cache was updated
        assert len(models) == 1
        assert _morpheus_models_cache["data"] == models
        assert _morpheus_models_cache["timestamp"] is not None

    @patch("httpx.get")
    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_updates_cache_on_http_error(
        self, mock_config, mock_httpx_get
    ):
        """Test that cache timestamp is updated even when API fails (prevents repeated calls)"""
        import httpx

        from src.cache import _morpheus_models_cache, clear_models_cache

        # Clear cache first
        clear_models_cache("morpheus")
        assert _morpheus_models_cache["timestamp"] is None

        mock_config.MORPHEUS_API_KEY = "test-key"
        mock_httpx_get.side_effect = httpx.HTTPStatusError(
            "Server Error", request=Mock(), response=Mock(status_code=500)
        )

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()

        # Verify cache was updated with empty list and timestamp (prevents repeated API calls)
        assert models == []
        assert _morpheus_models_cache["data"] == []
        assert _morpheus_models_cache["timestamp"] is not None

    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_no_api_key(self, mock_config):
        """Test fetch_models returns empty list without API key"""
        mock_config.MORPHEUS_API_KEY = None

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()
        assert models == []

    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_updates_cache_when_no_api_key(self, mock_config):
        """Test that cache is updated even when API key is missing"""
        from src.cache import _morpheus_models_cache, clear_models_cache

        # Clear cache first
        clear_models_cache("morpheus")
        assert _morpheus_models_cache["timestamp"] is None

        mock_config.MORPHEUS_API_KEY = None

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()

        # Verify cache was updated (prevents repeated calls when key is missing)
        assert models == []
        assert _morpheus_models_cache["data"] == []
        assert _morpheus_models_cache["timestamp"] is not None

    @patch("httpx.get")
    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_skips_empty_ids(self, mock_config, mock_httpx_get):
        """Test that models with empty or missing IDs are skipped"""
        mock_config.MORPHEUS_API_KEY = "test-key"

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {"id": "valid-model", "context_length": 4096},
                {"id": "", "context_length": 4096},  # Empty ID
                {"context_length": 4096},  # Missing ID
                {"id": "another-valid", "context_length": 8192},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_get.return_value = mock_response

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()

        # Only valid models should be included
        assert len(models) == 2
        assert models[0]["id"] == "morpheus/valid-model"
        assert models[1]["id"] == "morpheus/another-valid"


class TestMorpheusModelTransformations:
    """Test model ID transformations for Morpheus"""

    def test_morpheus_provider_detection(self):
        """Test that morpheus/ prefix models are detected correctly"""
        from src.services.model_transformations import detect_provider_from_model_id

        provider = detect_provider_from_model_id("morpheus/llama-3.1-8b")
        assert provider == "morpheus"

    def test_morpheus_prefix_stripping(self):
        """Test that morpheus/ prefix is stripped during transformation"""
        from src.services.model_transformations import transform_model_id

        result = transform_model_id("morpheus/llama-3.1-8b", "morpheus")
        assert result == "llama-3.1-8b"

    def test_morpheus_direct_model_passthrough(self):
        """Test that direct model names pass through"""
        from src.services.model_transformations import transform_model_id

        result = transform_model_id("llama-3.1-8b", "morpheus")
        assert result == "llama-3.1-8b"


class TestMorpheusCacheIntegration:
    """Test Morpheus cache integration"""

    def test_morpheus_cache_exists(self):
        """Test that Morpheus cache is defined in cache module"""
        from src.cache import _morpheus_models_cache

        assert _morpheus_models_cache is not None
        assert "data" in _morpheus_models_cache
        assert "timestamp" in _morpheus_models_cache
        assert "ttl" in _morpheus_models_cache
        assert "stale_ttl" in _morpheus_models_cache

    def test_morpheus_cache_in_get_models_cache(self):
        """Test that Morpheus is included in get_models_cache mapping"""
        from src.cache import get_models_cache

        cache = get_models_cache("morpheus")
        assert cache is not None

    def test_morpheus_cache_clearable(self):
        """Test that Morpheus cache can be cleared"""
        from src.cache import clear_models_cache, get_models_cache

        # Clear should not raise
        clear_models_cache("morpheus")

        cache = get_models_cache("morpheus")
        assert cache["data"] is None
        assert cache["timestamp"] is None


class TestMorpheusGatewayRegistry:
    """Test Morpheus gateway registry integration"""

    def test_morpheus_in_gateway_registry(self):
        """Test that Morpheus is in the GATEWAY_REGISTRY"""
        from src.routes.catalog import GATEWAY_REGISTRY

        assert "morpheus" in GATEWAY_REGISTRY
        assert GATEWAY_REGISTRY["morpheus"]["name"] == "Morpheus"
        assert "color" in GATEWAY_REGISTRY["morpheus"]
        assert "priority" in GATEWAY_REGISTRY["morpheus"]
        assert "site_url" in GATEWAY_REGISTRY["morpheus"]


class TestMorpheusConnectionPool:
    """Test Morpheus connection pool integration"""

    @patch("src.services.connection_pool.Config")
    def test_morpheus_pooled_client_raises_without_key(self, mock_config):
        """Test that get_morpheus_pooled_client raises without API key"""
        mock_config.MORPHEUS_API_KEY = None

        from src.services.connection_pool import get_morpheus_pooled_client

        with pytest.raises(ValueError, match="Morpheus API key not configured"):
            get_morpheus_pooled_client()
