"""Tests for Google Vertex AI multi-endpoint fallback"""

from unittest.mock import MagicMock, Mock, patch

import httpx

from src.services.google_vertex_client import _fetch_models_from_vertex_api


class TestGoogleVertexMultiEndpoint:
    """Test Google Vertex AI model fetching with multi-endpoint fallback"""

    @patch("src.services.google_vertex_client._prepare_vertex_environment")
    @patch("src.services.google_vertex_client._get_google_vertex_access_token")
    @patch("src.services.google_vertex_client.Config")
    @patch("httpx.Client")
    def test_first_endpoint_success(
        self, mock_httpx_client, mock_config, mock_get_token, mock_prepare_env
    ):
        """Test successful fetch from first endpoint (regional with project)"""
        # Setup mocks
        mock_config.GOOGLE_VERTEX_LOCATION = "us-east4"
        mock_config.GOOGLE_PROJECT_ID = "test-project"
        mock_get_token.return_value = "test-token"

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "publisherModels": [
                {"name": "publishers/google/models/gemini-2.0-flash"},
                {"name": "publishers/google/models/gemini-3-pro-preview"},
            ]
        }

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__.return_value.get.return_value = mock_response
        mock_httpx_client.return_value = mock_client_instance

        # Execute
        result = _fetch_models_from_vertex_api()

        # Verify
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "publishers/google/models/gemini-2.0-flash"

        # Verify only first endpoint was tried
        mock_client_instance.__enter__.return_value.get.assert_called_once()

    @patch("src.services.google_vertex_client._prepare_vertex_environment")
    @patch("src.services.google_vertex_client._get_google_vertex_access_token")
    @patch("src.services.google_vertex_client.Config")
    @patch("httpx.Client")
    def test_fallback_to_second_endpoint(
        self, mock_httpx_client, mock_config, mock_get_token, mock_prepare_env
    ):
        """Test fallback to second endpoint when first fails with 404"""
        # Setup mocks
        mock_config.GOOGLE_VERTEX_LOCATION = "us-east4"
        mock_config.GOOGLE_PROJECT_ID = "test-project"
        mock_get_token.return_value = "test-token"

        # Mock first call returns 404, second call succeeds
        mock_404_response = Mock()
        mock_404_response.status_code = 404
        mock_404_response.text = "Not Found"

        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {
            "publisherModels": [{"name": "publishers/google/models/gemini-2.0-flash"}]
        }

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__.return_value.get.side_effect = [
            mock_404_response,  # First endpoint fails
            mock_success_response,  # Second endpoint succeeds
        ]
        mock_httpx_client.return_value = mock_client_instance

        # Execute
        result = _fetch_models_from_vertex_api()

        # Verify
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "publishers/google/models/gemini-2.0-flash"

        # Verify both endpoints were tried
        assert mock_client_instance.__enter__.return_value.get.call_count == 2

    @patch("src.services.google_vertex_client._prepare_vertex_environment")
    @patch("src.services.google_vertex_client._get_google_vertex_access_token")
    @patch("src.services.google_vertex_client.Config")
    @patch("httpx.Client")
    def test_fallback_to_global_endpoint(
        self, mock_httpx_client, mock_config, mock_get_token, mock_prepare_env
    ):
        """Test fallback to global endpoint when regional endpoints fail"""
        # Setup mocks
        mock_config.GOOGLE_VERTEX_LOCATION = "us-east4"
        mock_config.GOOGLE_PROJECT_ID = "test-project"
        mock_get_token.return_value = "test-token"

        # Mock first two calls fail, third (global) succeeds
        mock_error_response = Mock()
        mock_error_response.status_code = 404
        mock_error_response.text = "Not Found"

        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {
            "publisherModels": [{"name": "publishers/google/models/gemini-2.0-flash"}]
        }

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__.return_value.get.side_effect = [
            mock_error_response,  # First endpoint fails
            mock_error_response,  # Second endpoint fails
            mock_success_response,  # Global endpoint succeeds
        ]
        mock_httpx_client.return_value = mock_client_instance

        # Execute
        result = _fetch_models_from_vertex_api()

        # Verify
        assert result is not None
        assert len(result) == 1

        # Verify all three endpoints were tried
        assert mock_client_instance.__enter__.return_value.get.call_count == 3

    @patch("src.services.google_vertex_client._prepare_vertex_environment")
    @patch("src.services.google_vertex_client._get_google_vertex_access_token")
    @patch("src.services.google_vertex_client.Config")
    @patch("httpx.Client")
    def test_all_endpoints_fail_returns_none(
        self, mock_httpx_client, mock_config, mock_get_token, mock_prepare_env, caplog
    ):
        """Test that None is returned when all endpoints fail"""
        # Setup mocks
        mock_config.GOOGLE_VERTEX_LOCATION = "us-east4"
        mock_config.GOOGLE_PROJECT_ID = "test-project"
        mock_get_token.return_value = "test-token"

        # Mock all calls fail
        mock_error_response = Mock()
        mock_error_response.status_code = 404
        mock_error_response.text = "Not Found"

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__.return_value.get.return_value = mock_error_response
        mock_httpx_client.return_value = mock_client_instance

        # Execute
        result = _fetch_models_from_vertex_api()

        # Verify
        assert result is None

        # Verify warning was logged about fallback
        assert any(
            "Failed to fetch models from all Vertex AI API endpoints" in record.message
            and "Falling back to static model configuration" in record.message
            for record in caplog.records
        ), "Expected fallback warning not found"

        # Verify all endpoints were tried
        assert mock_client_instance.__enter__.return_value.get.call_count == 3

    @patch("src.services.google_vertex_client._prepare_vertex_environment")
    @patch("src.services.google_vertex_client._get_google_vertex_access_token")
    @patch("src.services.google_vertex_client.Config")
    @patch("httpx.Client")
    def test_exception_handling_falls_through(
        self, mock_httpx_client, mock_config, mock_get_token, mock_prepare_env
    ):
        """Test that exceptions during fetch are handled and fallback occurs"""
        # Setup mocks
        mock_config.GOOGLE_VERTEX_LOCATION = "us-east4"
        mock_config.GOOGLE_PROJECT_ID = "test-project"
        mock_get_token.return_value = "test-token"

        # Mock all calls raise exceptions
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__.return_value.get.side_effect = [
            httpx.TimeoutException("Timeout"),
            httpx.ConnectError("Connection failed"),
            Exception("Unknown error"),
        ]
        mock_httpx_client.return_value = mock_client_instance

        # Execute
        result = _fetch_models_from_vertex_api()

        # Verify
        assert result is None

        # Verify all endpoints were attempted despite exceptions
        assert mock_client_instance.__enter__.return_value.get.call_count == 3

    @patch("src.services.google_vertex_client._prepare_vertex_environment")
    @patch("src.services.google_vertex_client._get_google_vertex_access_token")
    @patch("src.services.google_vertex_client.Config")
    @patch("httpx.Client")
    def test_correct_endpoint_urls(
        self, mock_httpx_client, mock_config, mock_get_token, mock_prepare_env
    ):
        """Test that the correct endpoint URLs are constructed"""
        # Setup mocks
        mock_config.GOOGLE_VERTEX_LOCATION = "us-central1"
        mock_config.GOOGLE_PROJECT_ID = "my-gcp-project"
        mock_get_token.return_value = "test-token"

        # Mock success on first call to capture URL
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"publisherModels": []}

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__.return_value.get.return_value = mock_response
        mock_httpx_client.return_value = mock_client_instance

        # Execute
        _fetch_models_from_vertex_api()

        # Verify the first endpoint URL was constructed correctly
        call_args = mock_client_instance.__enter__.return_value.get.call_args
        url = call_args[0][0]
        expected_url = "https://us-central1-aiplatform.googleapis.com/v1/projects/my-gcp-project/locations/us-central1/publishers/google/models"
        assert url == expected_url
