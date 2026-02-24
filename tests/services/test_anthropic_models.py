"""Tests for Anthropic model fetching and normalization.

Tests the fetch_models_from_anthropic and normalize_anthropic_model functions
which use the Anthropic Models API: https://docs.anthropic.com/en/api/models-list
"""

from unittest.mock import Mock, patch

import httpx

from src.services.models import (
    fetch_models_from_anthropic,
    normalize_anthropic_model,
)


class TestFetchModelsFromAnthropic:
    """Test Anthropic model fetching from API"""

    @patch("src.services.models.Config.ANTHROPIC_API_KEY", None)
    def test_fetch_models_no_api_key(self):
        """Test fetching models without API key returns None"""
        result = fetch_models_from_anthropic()
        assert result is None

    @patch("src.services.models.Config.ANTHROPIC_API_KEY", "test_key")
    @patch("src.services.models.httpx.get")
    def test_fetch_models_success(self, mock_get):
        """Test successful model fetching from API"""
        # Mock API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "claude-3-5-sonnet-20241022",
                    "display_name": "Claude 3.5 Sonnet",
                    "created_at": "2024-10-22T00:00:00Z",
                    "type": "model",
                },
                {
                    "id": "claude-3-opus-20240229",
                    "display_name": "Claude 3 Opus",
                    "created_at": "2024-02-29T00:00:00Z",
                    "type": "model",
                },
            ],
            "has_more": False,
            "first_id": "claude-3-5-sonnet-20241022",
            "last_id": "claude-3-opus-20240229",
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_models_from_anthropic()

        assert result is not None
        assert len(result) == 2
        assert result[0]["id"] == "anthropic/claude-3-5-sonnet-20241022"
        assert result[1]["id"] == "anthropic/claude-3-opus-20240229"

    @patch("src.services.models.Config.ANTHROPIC_API_KEY", "test_key")
    @patch("src.services.models.httpx.get")
    def test_fetch_models_pagination(self, mock_get):
        """Test model fetching with pagination"""
        # Mock paginated API responses
        page1_response = Mock()
        page1_response.status_code = 200
        page1_response.json.return_value = {
            "data": [
                {
                    "id": "claude-3-5-sonnet-20241022",
                    "display_name": "Claude 3.5 Sonnet",
                    "created_at": "2024-10-22T00:00:00Z",
                    "type": "model",
                }
            ],
            "has_more": True,
            "last_id": "claude-3-5-sonnet-20241022",
        }
        page1_response.raise_for_status = Mock()

        page2_response = Mock()
        page2_response.status_code = 200
        page2_response.json.return_value = {
            "data": [
                {
                    "id": "claude-3-opus-20240229",
                    "display_name": "Claude 3 Opus",
                    "created_at": "2024-02-29T00:00:00Z",
                    "type": "model",
                }
            ],
            "has_more": False,
            "last_id": "claude-3-opus-20240229",
        }
        page2_response.raise_for_status = Mock()

        mock_get.side_effect = [page1_response, page2_response]

        result = fetch_models_from_anthropic()

        assert result is not None
        assert len(result) == 2
        assert mock_get.call_count == 2

    @patch("src.services.models.Config.ANTHROPIC_API_KEY", "test_key")
    @patch("src.services.models.httpx.get")
    def test_fetch_models_filters_non_claude(self, mock_get):
        """Test that non-Claude models are filtered out"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "claude-3-5-sonnet-20241022",
                    "display_name": "Claude 3.5 Sonnet",
                    "created_at": "2024-10-22T00:00:00Z",
                    "type": "model",
                },
                {
                    "id": "some-other-model",
                    "display_name": "Other Model",
                    "created_at": "2024-01-01T00:00:00Z",
                    "type": "model",
                },
            ],
            "has_more": False,
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = fetch_models_from_anthropic()

        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == "anthropic/claude-3-5-sonnet-20241022"

    @patch("src.services.models.Config.ANTHROPIC_API_KEY", "test_key")
    @patch("src.services.models.httpx.get")
    def test_fetch_models_http_error(self, mock_get):
        """Test handling of HTTP errors"""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=Mock(), response=mock_response
        )

        result = fetch_models_from_anthropic()
        assert result is None


class TestNormalizeAnthropicModel:
    """Test Anthropic model normalization"""

    def test_normalize_basic_model(self):
        """Test normalizing a basic model from API response"""
        model = {
            "id": "claude-3-5-sonnet-20241022",
            "display_name": "Claude 3.5 Sonnet",
            "created_at": "2024-10-22T00:00:00Z",
            "type": "model",
        }

        result = normalize_anthropic_model(model)

        assert result is not None
        assert result["id"] == "anthropic/claude-3-5-sonnet-20241022"
        assert result["slug"] == "anthropic/claude-3-5-sonnet-20241022"
        assert result["name"] == "Claude 3.5 Sonnet"
        assert result["created"] == "2024-10-22T00:00:00Z"
        assert result["context_length"] == 200000
        assert result["provider_slug"] == "anthropic"
        assert result["source_gateway"] == "anthropic"

    def test_normalize_model_with_vision(self):
        """Test that Claude 3+ models have vision support"""
        model = {"id": "claude-3-opus-20240229", "display_name": "Claude 3 Opus", "type": "model"}

        result = normalize_anthropic_model(model)

        assert result is not None
        assert result["architecture"]["modality"] == "text+image->text"
        assert "image" in result["architecture"]["input_modalities"]

    def test_normalize_model_max_output_3_5(self):
        """Test that Claude 3.5 models have 8192 max output"""
        model = {
            "id": "claude-3-5-sonnet-20241022",
            "display_name": "Claude 3.5 Sonnet",
            "type": "model",
        }

        result = normalize_anthropic_model(model)

        assert result is not None
        assert result["architecture"]["max_output"] == 8192

    def test_normalize_model_max_output_3_0(self):
        """Test that Claude 3.0 models have 4096 max output"""
        model = {"id": "claude-3-opus-20240229", "display_name": "Claude 3 Opus", "type": "model"}

        result = normalize_anthropic_model(model)

        assert result is not None
        assert result["architecture"]["max_output"] == 4096

    def test_normalize_model_missing_id(self):
        """Test that models without ID return None"""
        model = {"display_name": "Some Model", "type": "model"}

        result = normalize_anthropic_model(model)
        assert result is None

    def test_normalize_model_fallback_name(self):
        """Test fallback to model ID when display_name is missing"""
        model = {"id": "claude-3-haiku-20240307", "type": "model"}

        result = normalize_anthropic_model(model)

        assert result is not None
        assert result["name"] == "claude-3-haiku-20240307"
