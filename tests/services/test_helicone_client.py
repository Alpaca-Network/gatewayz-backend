"""
Tests for Helicone-specific logic (pricing, timeout).

Standard get/request/stream/process tests are in test_provider_clients_parametrized.py.
"""

from unittest.mock import Mock, patch

import pytest


class TestFetchModelPricing:
    """Test fetch_model_pricing_from_helicone function"""

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", None)
    def test_fetch_pricing_no_api_key(self):
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result is None

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "placeholder-key")
    def test_fetch_pricing_placeholder_key(self):
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result is None

    @patch("src.services.models._is_building_catalog")
    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test")
    def test_fetch_pricing_during_catalog_build(self, mock_is_building):
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        mock_is_building.return_value = True
        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result is None

    @patch("src.services.helicone_client.httpx.get")
    @patch("src.services.models._is_building_catalog")
    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test")
    def test_fetch_pricing_from_api(self, mock_is_building, mock_get):
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        mock_is_building.return_value = False
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"gpt-4o-mini": {"prompt": "0.15", "completion": "0.60"}}
        mock_get.return_value = mock_response

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result == {"prompt": "0.15", "completion": "0.60"}

    @patch("src.services.helicone_client.httpx.get")
    @patch("src.services.helicone_client.get_provider_pricing_for_helicone_model")
    @patch("src.services.models._is_building_catalog")
    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test")
    def test_fetch_pricing_fallback_to_provider(
        self, mock_is_building, mock_get_provider_pricing, mock_get
    ):
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        mock_is_building.return_value = False
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        mock_get_provider_pricing.return_value = {"prompt": "0.10", "completion": "0.30"}

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result == {"prompt": "0.10", "completion": "0.30"}
        mock_get_provider_pricing.assert_called_once_with("gpt-4o-mini")


class TestGetProviderPricing:
    """Test get_provider_pricing_for_helicone_model function"""

    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_during_catalog_build(self, mock_is_building):
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = True
        result = get_provider_pricing_for_helicone_model("gpt-4o-mini")
        assert result is None

    @patch("src.services.pricing.get_model_pricing")
    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_found(self, mock_is_building, mock_get_pricing):
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = False
        mock_get_pricing.return_value = {"found": True, "prompt": "0.15", "completion": "0.60"}

        result = get_provider_pricing_for_helicone_model("gpt-4o-mini")
        assert result == {"prompt": "0.15", "completion": "0.60"}

    @patch("src.services.pricing.get_model_pricing")
    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_not_found(self, mock_is_building, mock_get_pricing):
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = False
        mock_get_pricing.return_value = {"found": False}

        result = get_provider_pricing_for_helicone_model("unknown-model")
        assert result is None

    @patch("src.services.pricing.get_model_pricing")
    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_with_prefix(self, mock_is_building, mock_get_pricing):
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = False
        mock_get_pricing.side_effect = [
            {"found": False},
            {"found": True, "prompt": "0.15", "completion": "0.60"},
        ]

        result = get_provider_pricing_for_helicone_model("openai/gpt-4o-mini")
        assert result == {"prompt": "0.15", "completion": "0.60"}
        assert mock_get_pricing.call_count == 2


class TestHeliconeTimeout:
    """Test Helicone timeout configuration"""

    def test_helicone_timeout_values(self):
        from src.services.helicone_client import HELICONE_TIMEOUT

        assert HELICONE_TIMEOUT.connect == 5.0
        assert HELICONE_TIMEOUT.read == 60.0
        assert HELICONE_TIMEOUT.write == 10.0
        assert HELICONE_TIMEOUT.pool == 5.0
