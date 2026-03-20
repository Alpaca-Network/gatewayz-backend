"""Unit tests for OneRouter-specific functionality.

Standard get_client / make_request / stream / process_response tests are
in test_provider_clients_parametrized.py.
"""

from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_onerouter_api_key():
    """Mock the OneRouter API key"""
    with patch("src.services.onerouter_client.Config") as mock_config:
        mock_config.ONEROUTER_API_KEY = "test_onerouter_key_123"
        yield mock_config


class TestParseTokenLimit:
    """Test _parse_token_limit helper function"""

    def test_parse_token_limit_int(self):
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit(131072) == 131072
        assert _parse_token_limit(4096) == 4096

    def test_parse_token_limit_string(self):
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit("131072") == 131072
        assert _parse_token_limit("4096") == 4096

    def test_parse_token_limit_string_with_commas(self):
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit("131,072") == 131072
        assert _parse_token_limit("1,048,576") == 1048576

    def test_parse_token_limit_none(self):
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit(None) == 4096

    def test_parse_token_limit_invalid_string(self):
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit("") == 4096
        assert _parse_token_limit("unlimited") == 4096
        assert _parse_token_limit("N/A") == 4096
        assert _parse_token_limit("abc") == 4096

    def test_parse_token_limit_float(self):
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit(128000.0) == 128000
        assert _parse_token_limit(4096.5) == 4096


class TestParsePricing:
    """Test _parse_pricing helper function"""

    def test_parse_pricing_with_dollar_sign(self):
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("$0.10") == "0.10"
        assert _parse_pricing("$2.50") == "2.50"

    def test_parse_pricing_without_dollar_sign(self):
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("0.10") == "0.10"
        assert _parse_pricing("2.50") == "2.50"

    def test_parse_pricing_zero(self):
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("$0") == "0"
        assert _parse_pricing("0") == "0"

    def test_parse_pricing_none(self):
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing(None) == "0"

    def test_parse_pricing_with_commas(self):
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("$1,000.50") == "1000.50"
        assert _parse_pricing("1,234.56") == "1234.56"


class TestFetchModelsFromOneRouter:
    """Test fetch_models_from_onerouter function with caching"""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        from src.cache import _onerouter_models_cache

        _onerouter_models_cache["data"] = None
        _onerouter_models_cache["timestamp"] = None
        yield
        _onerouter_models_cache["data"] = None
        _onerouter_models_cache["timestamp"] = None

    def test_fetch_models_success_with_caching_and_pricing(self, mock_onerouter_api_key):
        """Test successful model fetch with pricing enrichment"""
        from datetime import UTC, datetime

        from src.cache import _onerouter_models_cache
        from src.services.onerouter_client import fetch_models_from_onerouter

        mock_v1_models_response = {
            "data": [
                {
                    "id": "gemini-2.0-flash",
                    "object": "model",
                    "created": 1234567890,
                    "owned_by": "google",
                },
                {
                    "id": "deepseek-v3-250324",
                    "object": "model",
                    "created": 1234567890,
                    "owned_by": "deepseek",
                },
                {
                    "id": "model-without-pricing",
                    "object": "model",
                    "created": 1234567890,
                    "owned_by": "test",
                },
            ]
        }
        mock_display_models_response = {
            "data": [
                {
                    "invoke_name": "gemini-2.0-flash",
                    "name": "gemini-2.0-flash",
                    "sale_input_cost": "$0",
                    "sale_output_cost": "$0",
                    "retail_input_cost": "$0.10",
                    "retail_output_cost": "$0.40",
                    "input_token_limit": "1048576",
                    "output_token_limit": "8192",
                    "input_modalities": "Text, Code, Images",
                    "output_modalities": "Text, Code",
                },
                {
                    "invoke_name": "deepseek-v3-250324",
                    "name": "deepseek-v3-250324",
                    "sale_input_cost": "$1.14",
                    "sale_output_cost": "$4.56",
                    "retail_input_cost": "$1.14",
                    "retail_output_cost": "$4.56",
                    "input_token_limit": "16,384",
                    "output_token_limit": "65,536",
                    "input_modalities": "Text",
                    "output_modalities": "Text",
                },
            ]
        }

        with patch("src.services.onerouter_client.httpx.get") as mock_get:

            def side_effect(url, **kwargs):
                mock_response = Mock()
                mock_response.raise_for_status = Mock()
                if "v1/models" in url:
                    mock_response.json.return_value = mock_v1_models_response
                else:
                    mock_response.json.return_value = mock_display_models_response
                return mock_response

            mock_get.side_effect = side_effect

            models = fetch_models_from_onerouter()

            assert len(models) == 3
            assert models[0]["id"] == "onerouter/gemini-2.0-flash"
            assert models[0]["pricing"]["prompt"] == "0.10"
            assert models[0]["architecture"]["modality"] == "text+image->text"
            assert models[1]["pricing"]["prompt"] == "1.14"
            assert _onerouter_models_cache["data"] == models
            assert _onerouter_models_cache["timestamp"] is not None

    def test_fetch_models_skip_empty_model_id(self, mock_onerouter_api_key):
        from src.services.onerouter_client import fetch_models_from_onerouter

        mock_v1 = {
            "data": [
                {"id": "", "object": "model", "created": 1234567890, "owned_by": "test"},
                {"id": "valid-model", "object": "model", "created": 1234567890, "owned_by": "test"},
            ]
        }
        with patch("src.services.onerouter_client.httpx.get") as mock_get:

            def side_effect(url, **kwargs):
                resp = Mock()
                resp.raise_for_status = Mock()
                if "v1/models" in url:
                    resp.json.return_value = mock_v1
                else:
                    resp.json.return_value = {"data": []}
                return resp

            mock_get.side_effect = side_effect
            models = fetch_models_from_onerouter()
            assert len(models) == 1
            assert models[0]["id"] == "onerouter/valid-model"

    def test_fetch_models_missing_api_key(self):
        from src.cache import _onerouter_models_cache
        from src.services.onerouter_client import fetch_models_from_onerouter

        with patch("src.services.onerouter_client.Config") as mock_config:
            mock_config.ONEROUTER_API_KEY = None
            models = fetch_models_from_onerouter()
            assert models == []
            assert _onerouter_models_cache["data"] == []
