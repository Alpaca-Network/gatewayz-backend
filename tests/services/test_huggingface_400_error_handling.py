"""Tests for HuggingFace 400 error handling improvements"""

from unittest.mock import Mock, patch

import httpx
import pytest

from src.services.huggingface_client import (
    make_huggingface_request_openai,
    make_huggingface_request_openai_stream,
)


class TestHuggingFace400ErrorHandling:
    """Test improved error handling for HuggingFace 400 Bad Request errors"""

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_400_error_with_detailed_logging(self, mock_get_client, caplog):
        """Test that 400 errors are logged with detailed information including payload"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Model not available on HF Inference Router"

        # Create HTTP status error
        error = httpx.HTTPStatusError("400 Bad Request", request=Mock(), response=mock_response)

        mock_client.post.side_effect = error
        mock_client.close = Mock()
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        model = "Qwen/Qwen2.5-72B-Instruct"

        # Test should raise the error
        with pytest.raises(httpx.HTTPStatusError):
            make_huggingface_request_openai(messages, model)

        # Verify detailed logging occurred
        assert any(
            "400 Bad Request for model" in record.message
            and "This model may not be available on HF Inference Router" in record.message
            for record in caplog.records
        ), "Expected detailed 400 error logging not found"

        # Verify payload is logged
        assert any(
            "Request payload:" in record.message for record in caplog.records
        ), "Expected payload logging not found"

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_400_error_streaming_with_detailed_logging(self, mock_get_client, caplog):
        """Test that streaming 400 errors are logged with detailed information"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Model requires Pro subscription"

        # Create HTTP status error
        error = httpx.HTTPStatusError("400 Bad Request", request=Mock(), response=mock_response)

        # Mock the stream context manager to raise error
        mock_stream_context = Mock()
        mock_stream_context.__enter__.side_effect = error
        mock_stream_context.__exit__ = Mock(return_value=False)

        mock_client.stream.return_value = mock_stream_context
        mock_client.close = Mock()
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        model = "Qwen/Qwen2.5-72B-Instruct"

        # Test should raise the error
        with pytest.raises(httpx.HTTPStatusError):
            # Consume the generator
            list(make_huggingface_request_openai_stream(messages, model))

        # Verify detailed logging occurred for streaming
        assert any(
            "400 Bad Request (streaming) for model" in record.message
            and "This model may not be available on HF Inference Router" in record.message
            for record in caplog.records
        ), "Expected detailed 400 streaming error logging not found"

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_non_400_error_standard_logging(self, mock_get_client, caplog):
        """Test that non-400 errors use standard error logging"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.text = "Service temporarily unavailable"

        # Create HTTP status error
        error = httpx.HTTPStatusError(
            "503 Service Unavailable", request=Mock(), response=mock_response
        )

        mock_client.post.side_effect = error
        mock_client.close = Mock()
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        model = "meta-llama/Llama-2-7b"

        # Test should raise the error
        with pytest.raises(httpx.HTTPStatusError):
            make_huggingface_request_openai(messages, model)

        # Verify standard logging (no special 400 handling)
        assert any(
            "Hugging Face request failed" in record.message
            and "400 Bad Request for model" not in record.message
            for record in caplog.records
        ), "Expected standard error logging not found"

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_400_error_without_text_attribute(self, mock_get_client, caplog):
        """Test 400 error handling when response has no text attribute"""
        # Mock the client and response without text attribute
        mock_client = Mock()
        mock_response = Mock(spec=["status_code"])
        mock_response.status_code = 400
        # Don't set text attribute to test fallback

        # Create HTTP status error
        error = httpx.HTTPStatusError("400 Bad Request", request=Mock(), response=mock_response)

        mock_client.post.side_effect = error
        mock_client.close = Mock()
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        model = "Qwen/Qwen2.5-72B-Instruct"

        # Test should not crash even without text attribute
        with pytest.raises(httpx.HTTPStatusError):
            make_huggingface_request_openai(messages, model)

        # Verify logging still occurred despite missing text
        assert any(
            "400 Bad Request for model" in record.message for record in caplog.records
        ), "Expected error logging even without response text"
