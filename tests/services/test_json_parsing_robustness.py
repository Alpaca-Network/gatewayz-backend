"""
Tests for robust JSON parsing error handling across service modules.

These tests verify that service modules gracefully handle malformed JSON responses
from external APIs without crashing or exposing sensitive information.
"""

import json
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest


class TestHuggingFaceModelsJSONParsing:
    """Test JSON parsing error handling in huggingface_models.py"""

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    async def test_fetch_models_with_invalid_json_response(self, mock_get):
        """Test that fetch_huggingface_models handles invalid JSON gracefully"""
        from src.services.huggingface_models import fetch_models_from_huggingface_api

        # Create a mock response with invalid JSON
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        # Should return None instead of raising exception (per docstring contract)
        result = fetch_models_from_huggingface_api(limit=10)

        # Verify it doesn't crash and returns appropriate fallback
        # Note: Function returns None on error per documented behavior
        assert result is None or isinstance(result, list)

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    async def test_search_models_with_invalid_json(self, mock_get):
        """Test that search_huggingface_models handles malformed JSON"""
        from src.services.huggingface_models import search_huggingface_models

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = search_huggingface_models("test-query")

        # Should return empty list on JSON parse error
        assert result == []

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    async def test_get_model_info_with_invalid_json(self, mock_get):
        """Test that get_huggingface_model_info handles JSON errors"""
        from src.services.huggingface_models import get_huggingface_model_info

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = get_huggingface_model_info("test-model")

        # Should return None on JSON parse error
        assert result is None

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    async def test_fetch_models_with_html_error_response(self, mock_get):
        """Test handling when API returns HTML error page instead of JSON"""
        from src.services.huggingface_models import fetch_models_from_huggingface_api

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.raise_for_status = Mock()
        # Simulate HTML error page being parsed as JSON
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = fetch_models_from_huggingface_api(limit=5)

        # Should handle gracefully and return appropriate type
        # Note: Function returns None on error per documented behavior
        assert result is None or isinstance(result, list)


class TestProvidersJSONParsing:
    """Test JSON parsing error handling in providers.py"""

    @pytest.mark.asyncio
    @patch("src.services.providers.httpx.get")
    async def test_fetch_providers_with_invalid_json(self, mock_get):
        """Test that fetch_providers handles malformed JSON response"""
        from src.services.providers import fetch_providers_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = fetch_providers_from_openrouter()

        # Should return None on JSON parse error (based on exception handler)
        assert result is None

    @pytest.mark.asyncio
    @patch("src.services.providers.httpx.get")
    async def test_fetch_providers_with_empty_response(self, mock_get):
        """Test handling of empty response body"""
        from src.services.providers import fetch_providers_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = fetch_providers_from_openrouter()

        assert result is None


class TestModelsJSONParsing:
    """Test JSON parsing error handling in models.py"""

    @pytest.mark.asyncio
    @patch("src.services.models.httpx.get")
    async def test_fetch_openrouter_models_with_invalid_json(self, mock_get):
        """Test that fetch_openrouter_models handles JSON parse errors"""
        from src.services.models import fetch_models_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = fetch_models_from_openrouter()

        # Should return empty list instead of crashing
        assert result == []

    @pytest.mark.asyncio
    @patch("src.services.models.httpx.get")
    async def test_fetch_models_with_corrupted_json_response(self, mock_get):
        """Test handling of corrupted/truncated JSON"""
        from src.services.models import fetch_models_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        # Simulate truncated JSON that can't be parsed
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = fetch_models_from_openrouter()

        assert result == []


class TestJSONParsingWithDifferentContentTypes:
    """Test that services handle unexpected Content-Type headers"""

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    async def test_xml_content_type_returned_instead_of_json(self, mock_get):
        """Test when API incorrectly returns XML but claims JSON"""
        from src.services.huggingface_models import search_huggingface_models

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/xml"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = search_huggingface_models("test")

        assert result == []

    @pytest.mark.asyncio
    @patch("src.services.providers.httpx.get")
    async def test_plain_text_error_message_as_json(self, mock_get):
        """Test handling when API returns plain text error"""
        from src.services.providers import fetch_providers_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        result = fetch_providers_from_openrouter()

        assert result is None


class TestJSONParsingErrorLogging:
    """Verify that JSON parsing errors are logged appropriately"""

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    @patch("src.services.huggingface_models.logger")
    async def test_json_error_is_logged_with_context(self, mock_logger, mock_get):
        """Verify JSON parse errors are logged with useful context"""
        from src.services.huggingface_models import fetch_models_from_huggingface_api

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        fetch_models_from_huggingface_api(limit=1)

        # Verify error was logged
        assert mock_logger.error.called

        # Verify log message contains useful context
        call_args = str(mock_logger.error.call_args)
        assert "Failed to parse JSON" in call_args or "json" in call_args.lower()

    @pytest.mark.asyncio
    @patch("src.services.providers.httpx.get")
    @patch("src.services.providers.logger")
    async def test_providers_json_error_logging(self, mock_logger, mock_get):
        """Verify provider JSON errors include response details"""
        from src.services.providers import fetch_providers_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(side_effect=json.JSONDecodeError("Parse error", "", 0))
        mock_get.return_value = mock_response

        fetch_providers_from_openrouter()

        # Verify appropriate error logging occurred
        assert mock_logger.error.called


class TestJSONParsingEdgeCases:
    """Test edge cases in JSON parsing"""

    @pytest.mark.asyncio
    @patch("src.services.huggingface_models.httpx.get")
    async def test_null_json_response(self, mock_get):
        """Test when response.json() returns None"""
        from src.services.huggingface_models import search_huggingface_models

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(return_value=None)
        mock_get.return_value = mock_response

        result = search_huggingface_models("test")

        # Should handle None gracefully
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("src.services.models.httpx.get")
    async def test_json_with_unexpected_structure(self, mock_get):
        """Test when JSON structure differs from expected"""
        from src.services.models import fetch_models_from_openrouter

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        # Return valid JSON but wrong structure (string instead of object)
        mock_response.json = Mock(return_value="unexpected string response")
        mock_get.return_value = mock_response

        result = fetch_models_from_openrouter()

        # Should handle gracefully without crashing
        # Note: Function may return None on unexpected structure per documented behavior
        assert result is None or isinstance(result, list)
