"""
Tests for chat completion requests database operations
"""

import pytest
from unittest.mock import Mock, patch
from src.db.chat_completion_requests import (
    save_chat_completion_request,
    get_model_id_by_name,
    get_chat_completion_stats,
)


class TestChatCompletionRequests:
    """Test suite for chat completion request database operations"""

    @patch("src.db.chat_completion_requests.get_supabase_client")
    def test_save_chat_completion_request_success(self, mock_get_client):
        """Test successfully saving a chat completion request"""
        # Setup mock
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock model lookup
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 123}
        ]

        # Mock insert
        mock_insert_result = Mock()
        mock_insert_result.data = [{
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "request_id": "test-request-123",
            "model_id": 123,
            "input_tokens": 100,
            "output_tokens": 200,
            "total_tokens": 300,
            "processing_time_ms": 1500,
            "status": "completed",
        }]
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_insert_result

        # Call function
        result = save_chat_completion_request(
            request_id="test-request-123",
            model_name="gpt-4",
            input_tokens=100,
            output_tokens=200,
            processing_time_ms=1500,
            status="completed",
            provider_name="openai",
        )

        # Verify
        assert result is not None
        assert result["request_id"] == "test-request-123"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 200

    @patch("src.db.chat_completion_requests.get_supabase_client")
    def test_save_chat_completion_request_model_not_found(self, mock_get_client):
        """Test handling when model is not found in database"""
        # Setup mock
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock empty model lookup results
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        # Call function
        result = save_chat_completion_request(
            request_id="test-request-123",
            model_name="unknown-model",
            input_tokens=100,
            output_tokens=200,
            processing_time_ms=1500,
            provider_name="unknown",
        )

        # Verify - should return None when model not found
        assert result is None

    @patch("src.db.chat_completion_requests.get_supabase_client")
    def test_save_chat_completion_request_with_error(self, mock_get_client):
        """Test saving a failed request with error message"""
        # Setup mock
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock model lookup
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 123}
        ]

        # Mock insert
        mock_insert_result = Mock()
        mock_insert_result.data = [{
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "request_id": "test-request-456",
            "model_id": 123,
            "input_tokens": 50,
            "output_tokens": 0,
            "processing_time_ms": 500,
            "status": "failed",
            "error_message": "Model timeout",
        }]
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_insert_result

        # Call function
        result = save_chat_completion_request(
            request_id="test-request-456",
            model_name="gpt-4",
            input_tokens=50,
            output_tokens=0,
            processing_time_ms=500,
            status="failed",
            error_message="Model timeout",
            provider_name="openai",
        )

        # Verify
        assert result is not None
        assert result["status"] == "failed"
        assert result["error_message"] == "Model timeout"

    @patch("src.db.chat_completion_requests.get_supabase_client")
    def test_get_model_id_by_name(self, mock_get_client):
        """Test retrieving model ID by name"""
        # Setup mock
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock model lookup
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 456}
        ]

        # Call function
        result = get_model_id_by_name("gpt-4", "openai")

        # Verify
        assert result is not None

    @patch("src.db.chat_completion_requests.get_supabase_client")
    def test_get_chat_completion_stats(self, mock_get_client):
        """Test retrieving chat completion statistics"""
        # Setup mock
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock stats query
        mock_result = Mock()
        mock_result.data = [
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "request_id": "req-1",
                "model_id": 123,
                "input_tokens": 100,
                "output_tokens": 200,
                "total_tokens": 300,
                "processing_time_ms": 1500,
                "status": "completed",
            },
            {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "request_id": "req-2",
                "model_id": 123,
                "input_tokens": 150,
                "output_tokens": 250,
                "total_tokens": 400,
                "processing_time_ms": 2000,
                "status": "completed",
            },
        ]

        mock_query = Mock()
        mock_query.execute.return_value = mock_result
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value = mock_query

        # Call function
        result = get_chat_completion_stats(model_id=123, limit=10)

        # Verify
        assert isinstance(result, list)
