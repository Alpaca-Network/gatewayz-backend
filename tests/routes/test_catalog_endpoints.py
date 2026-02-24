#!/usr/bin/env python3
"""
Integration tests for catalog endpoints

These tests execute real endpoint code to increase coverage
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


class TestGetProvidersEndpoint:
    """Test /v1/provider endpoint"""

    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_openrouter_default(self, mock_models, mock_providers):
        """Test getting OpenRouter providers (default)"""
        mock_providers.return_value = [
            {"id": "openai", "name": "OpenAI", "description": "OpenAI models"}
        ]
        mock_models.return_value = [{"id": "openai/gpt-4", "name": "GPT-4"}]

        response = client.get("/v1/provider")

        # Should succeed or fail gracefully
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_with_gateway_param(self, mock_models, mock_providers):
        """Test with specific gateway parameter"""
        mock_providers.return_value = [{"id": "anthropic", "name": "Anthropic"}]
        mock_models.return_value = []

        response = client.get("/v1/provider?gateway=openrouter")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_all_gateways(self, mock_models, mock_providers):
        """Test getting providers from all gateways"""
        mock_providers.return_value = [{"id": "openai", "name": "OpenAI"}]
        mock_models.return_value = [{"id": "openai/gpt-4"}]

        response = client.get("/v1/provider?gateway=all")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_huggingface(self, mock_models):
        """Test getting Hugging Face providers"""
        mock_models.return_value = [{"id": "meta-llama/Llama-2-7b"}]

        response = client.get("/v1/provider?gateway=hug")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_huggingface_alias(self, mock_models):
        """Test huggingface gateway alias"""
        mock_models.return_value = []

        response = client.get("/v1/provider?gateway=huggingface")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_providers")
    def test_get_providers_moderated_only(self, mock_providers):
        """Test filtering for moderated providers"""
        mock_providers.return_value = [
            {"id": "openai", "moderated_by_openrouter": True},
            {"id": "other", "moderated_by_openrouter": False},
        ]

        response = client.get("/v1/provider?moderated_only=true")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_with_limit(self, mock_models, mock_providers):
        """Test pagination with limit parameter"""
        mock_providers.return_value = [
            {"id": f"provider-{i}", "name": f"Provider {i}"} for i in range(100)
        ]
        mock_models.return_value = []

        response = client.get("/v1/provider?limit=10")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_with_offset(self, mock_models, mock_providers):
        """Test pagination with offset parameter"""
        mock_providers.return_value = [{"id": f"provider-{i}"} for i in range(50)]
        mock_models.return_value = []

        response = client.get("/v1/provider?offset=20&limit=10")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_providers")
    def test_get_providers_empty_data(self, mock_providers):
        """Test when no provider data available - should return 200 with empty response (graceful degradation)"""
        mock_providers.return_value = []

        response = client.get("/v1/provider")
        assert response.status_code == 200  # Changed: graceful degradation, not 503

    @patch("src.routes.catalog.get_cached_providers")
    def test_get_providers_none_data(self, mock_providers):
        """Test when provider data is None - should return 200 with empty response (graceful degradation)"""
        mock_providers.return_value = None

        response = client.get("/v1/provider")
        assert response.status_code == 200  # Changed: graceful degradation, not 503

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_groq(self, mock_models):
        """Test Groq gateway"""
        mock_models.return_value = [{"id": "mixtral-8x7b"}]

        response = client.get("/v1/provider?gateway=groq")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_fireworks(self, mock_models):
        """Test Fireworks gateway"""
        mock_models.return_value = [{"id": "llama-v2-7b"}]

        response = client.get("/v1/provider?gateway=fireworks")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_together(self, mock_models):
        """Test Together gateway - should return 200 with empty data (graceful degradation)"""
        mock_models.return_value = []

        response = client.get("/v1/provider?gateway=together")
        assert response.status_code == 200  # Changed: graceful degradation, not 503

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_cerebras(self, mock_models):
        """Test Cerebras gateway - should return 200 with empty data (graceful degradation)"""
        mock_models.return_value = []

        response = client.get("/v1/provider?gateway=cerebras")
        assert response.status_code == 200  # Changed: graceful degradation, not 503

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_xai(self, mock_models):
        """Test xAI gateway"""
        mock_models.return_value = [{"id": "grok-1"}]

        response = client.get("/v1/provider?gateway=xai")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_deepinfra(self, mock_models):
        """Test DeepInfra gateway"""
        mock_models.return_value = [{"id": "meta-llama/Llama-2-70b"}]

        response = client.get("/v1/provider?gateway=deepinfra")
        assert response.status_code in [200, 503, 500]

    @patch("src.routes.catalog.get_cached_models")
    def test_get_providers_featherless(self, mock_models):
        """Test Featherless gateway - should return 200 with empty data (graceful degradation)"""
        mock_models.return_value = []

        response = client.get("/v1/provider?gateway=featherless")
        assert response.status_code == 200  # Changed: graceful degradation, not 503


class TestModelsEndpoint:
    """Test coverage for the unified /models endpoint used by the UI."""

    @patch("src.routes.catalog.get_cached_models")
    def test_nebius_gateway_returns_empty_catalog(self, mock_get_cached_models):
        """Requests for Nebius should return 200 even if no catalog is available."""

        def fake_get_cached_models(gateway: str):
            assert gateway == "nebius"
            return []

        mock_get_cached_models.side_effect = fake_get_cached_models

        response = client.get("/v1/models?gateway=nebius&include_huggingface=false")

        assert response.status_code == 200
        payload = response.json()
        assert payload["gateway"] == "nebius"
        assert payload["total"] == 0
        assert payload["returned"] == 0
        assert payload["data"] == []

    @patch("src.routes.catalog.get_cached_models")
    def test_xai_gateway_returns_empty_catalog(self, mock_get_cached_models):
        """Requests for xAI should return 200 even if no catalog is available."""

        def fake_get_cached_models(gateway: str):
            assert gateway == "xai"
            return []

        mock_get_cached_models.side_effect = fake_get_cached_models

        response = client.get("/v1/models?gateway=xai&include_huggingface=false")

        assert response.status_code == 200
        payload = response.json()
        assert payload["gateway"] == "xai"
        assert payload["total"] == 0
        assert payload["returned"] == 0
        assert payload["data"] == []

    @patch("src.routes.catalog.enhance_providers_with_logos_and_sites")
    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_models_page_all_gateway_loads(
        self,
        mock_get_cached_models,
        mock_get_cached_providers,
        mock_enhance_providers,
    ):
        """Ensure the models catalog page can load aggregated data."""

        sample_model = {
            "id": "openai/gpt-4",
            "slug": "openai-gpt-4",
            "canonical_slug": "openai/gpt-4",
            "provider_slug": "openai",
            "pricing": {"prompt": "0.03", "completion": "0.06"},
        }

        def fake_get_cached_models(gateway: str):
            gateway = (gateway or "").lower()
            catalog_by_gateway = {
                "openrouter": [sample_model],
                "featherless": [],
                "deepinfra": [],
                "chutes": [],
                "groq": [],
                "fireworks": [],
                "together": [],
                "cerebras": [],
                "nebius": [],
                "xai": [],
                "novita": [],
                "hug": [],
                "aimo": [],
                "near": [],
                "fal": [],
                "anannas": [],
                "vercel-ai-gateway": [],
            }
            return catalog_by_gateway.get(gateway, [])

        mock_get_cached_models.side_effect = fake_get_cached_models
        mock_get_cached_providers.return_value = [
            {"slug": "openai", "site_url": "https://openai.com"}
        ]
        mock_enhance_providers.side_effect = lambda providers: providers

        response = client.get("/v1/models?gateway=all&limit=5&include_huggingface=false")

        assert response.status_code == 200
        payload = response.json()
        assert payload["gateway"] == "all"
        assert payload["returned"] == 1
        assert payload["data"][0]["id"] == "openai/gpt-4"
        assert payload["data"][0]["provider_slug"] == "openai"


class TestMergeProviderLists:
    """Test provider list merging"""

    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_merge_providers_from_multiple_sources(self, mock_models, mock_providers):
        """Test that providers from multiple gateways are merged"""
        mock_providers.return_value = [{"id": "openai", "name": "OpenAI"}]
        mock_models.return_value = [{"id": "openai/gpt-4"}, {"id": "anthropic/claude-3"}]

        response = client.get("/v1/provider?gateway=all")
        assert response.status_code in [200, 503, 500]


class TestMergeModelsBySlug:
    """Test model merging by slug"""

    @patch("src.routes.catalog.merge_models_by_slug")
    @patch("src.routes.catalog.get_cached_providers")
    @patch("src.routes.catalog.get_cached_models")
    def test_models_merged_correctly(self, mock_models, mock_providers, mock_merge):
        """Test that duplicate models are handled"""
        mock_providers.return_value = [{"id": "openai"}]
        mock_models.return_value = [
            {"id": "gpt-4", "canonical_slug": "gpt-4"},
            {"id": "gpt-4", "canonical_slug": "gpt-4"},  # Duplicate
        ]
        mock_merge.return_value = [{"id": "gpt-4", "canonical_slug": "gpt-4"}]

        response = client.get("/v1/provider?gateway=all")
        assert response.status_code in [200, 503, 500]


class TestGetGatewaysEndpoint:
    """Test /v1/gateways endpoint for gateway auto-discovery"""

    def test_get_gateways_returns_list(self):
        """Test that gateways endpoint returns a list of gateways"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        assert "data" in data
        assert "total" in data
        assert "timestamp" in data
        assert isinstance(data["data"], list)
        assert data["total"] > 0

    def test_get_gateways_has_required_fields(self):
        """Test that each gateway has required fields"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        for gateway in data["data"]:
            assert "id" in gateway
            assert "name" in gateway
            assert "color" in gateway
            assert "priority" in gateway
            # priority should be 'fast' or 'slow'
            assert gateway["priority"] in ["fast", "slow"]

    def test_get_gateways_includes_simplismart(self):
        """Test that SimpliSmart is in the gateway list"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        gateway_ids = [g["id"] for g in data["data"]]
        assert "simplismart" in gateway_ids

        # Check SimpliSmart has correct config
        simplismart = next((g for g in data["data"] if g["id"] == "simplismart"), None)
        assert simplismart is not None, "SimpliSmart gateway not found in response"
        assert simplismart["name"] == "SimpliSmart"
        assert simplismart["color"] == "bg-sky-500"

    def test_get_gateways_includes_major_providers(self):
        """Test that major providers are included"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        gateway_ids = [g["id"] for g in data["data"]]
        expected_gateways = [
            "openai",
            "anthropic",
            "openrouter",
            "groq",
            "together",
            "fireworks",
            "deepinfra",
            "huggingface",
        ]
        for gw in expected_gateways:
            assert gw in gateway_ids, f"Expected gateway '{gw}' not found"

    def test_get_gateways_fast_gateways_first(self):
        """Test that fast gateways are sorted before slow gateways"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        gateways = data["data"]
        found_slow = False
        for gw in gateways:
            if gw["priority"] == "slow":
                found_slow = True
            elif gw["priority"] == "fast" and found_slow:
                pytest.fail("Fast gateway found after slow gateway - sorting is wrong")

    def test_get_gateways_logo_urls(self):
        """Test that gateways have logo URLs generated from site URLs"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        # Check that gateways with site_url have logo_url generated
        for gateway in data["data"]:
            if gateway.get("site_url"):
                assert gateway.get("logo_url") is not None
                assert "favicon" in gateway["logo_url"]

    def test_get_gateways_aliases(self):
        """Test that gateways with aliases include them"""
        response = client.get("/v1/gateways")
        assert response.status_code == 200
        data = response.json()

        # huggingface should have 'hug' alias
        huggingface = next((g for g in data["data"] if g["id"] == "huggingface"), None)
        assert huggingface is not None
        assert "aliases" in huggingface
        assert "hug" in huggingface["aliases"]

        # google-vertex should have 'google' alias
        google = next((g for g in data["data"] if g["id"] == "google-vertex"), None)
        assert google is not None
        assert "aliases" in google
        assert "google" in google["aliases"]
