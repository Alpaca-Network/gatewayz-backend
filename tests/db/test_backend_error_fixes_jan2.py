"""
Tests for backend error fixes - January 2, 2026

This test module verifies fixes for unsafe database access patterns and provider response handling
identified in the 24-hour backend error check.

Fixes tested:
1. src/db/api_keys.py - Unsafe .data[0] access patterns (5 locations)
2. src/db/chat_completion_requests.py - Unsafe .data[0] access patterns (4 locations)
3. src/services/ai_sdk_client.py - Unsafe provider response.choices[0] access
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from src.utils.db_safety import DatabaseResultError
from src.utils.provider_safety import ProviderError


class TestAPIKeysDataAccessSafety:
    """Test safe database access patterns in src/db/api_keys.py"""

    @patch('src.db.api_keys.get_supabase_client')
    def test_create_api_key_handles_empty_result(self, mock_get_client):
        """Test that create_api_key handles empty database result safely"""
        from src.db.api_keys import create_api_key

        # Mock empty result from database
        mock_client = Mock()
        mock_result = Mock()
        mock_result.data = []  # Empty list - should raise DatabaseResultError
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_result
        mock_get_client.return_value = mock_client

        # Should raise ValueError (wrapped DatabaseResultError)
        with pytest.raises(ValueError, match="Failed to create API key"):
            create_api_key(user_id=123, key_name="test")

    @patch('src.db.api_keys.get_supabase_client')
    def test_get_api_key_usage_stats_handles_empty_result(self, mock_get_client):
        """Test that get_api_key_usage_stats handles missing API key safely"""
        from src.db.api_keys import get_api_key_usage_stats

        # Mock empty result from database
        mock_client = Mock()
        mock_result = Mock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_result
        mock_get_client.return_value = mock_client

        # Should return default stats dict, not crash with IndexError
        result = get_api_key_usage_stats("sk_test_missing")

        assert result["api_key"] == "sk_test_missing"
        assert result["key_name"] == "Unknown"
        assert result["is_active"] is False
        assert result["requests_used"] == 0

    @patch('src.db.api_keys.get_supabase_client')
    def test_update_api_key_handles_empty_key_lookup(self, mock_get_client):
        """Test that update_api_key handles missing API key safely"""
        from src.db.api_keys import update_api_key

        # Mock empty result for key lookup
        mock_client = Mock()
        mock_result = Mock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result
        mock_get_client.return_value = mock_client

        # Should raise ValueError (wrapped DatabaseResultError)
        with pytest.raises(ValueError, match="API key not found or not owned by user"):
            update_api_key(
                user_id=123,
                api_key="sk_test_missing",
                updates={"key_name": "new_name"}
            )

    @patch('src.db.api_keys.get_supabase_client')
    def test_update_api_key_handles_empty_update_result(self, mock_get_client):
        """Test that update_api_key handles failed update safely"""
        from src.db.api_keys import update_api_key

        # Mock successful key lookup but failed update
        mock_client = Mock()

        # Key lookup succeeds
        lookup_result = Mock()
        lookup_result.data = [{"id": 456, "user_id": 123}]

        # Update fails (empty result)
        update_result = Mock()
        update_result.data = []

        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = lookup_result
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_result
        mock_get_client.return_value = mock_client

        # Should raise ValueError (wrapped DatabaseResultError)
        with pytest.raises(ValueError, match="Failed to update API key"):
            update_api_key(
                user_id=123,
                api_key="sk_test_valid",
                updates={"key_name": "new_name"}
            )

    @patch('src.db.api_keys.get_supabase_client')
    def test_delete_api_key_handles_empty_lookup(self, mock_get_client):
        """Test that delete_api_key handles missing API key safely"""
        from src.db.api_keys import delete_api_key

        # Mock empty result for key lookup
        mock_client = Mock()
        mock_result = Mock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_result
        mock_get_client.return_value = mock_client

        # Should return False (not crash)
        result = delete_api_key("sk_test_missing")
        assert result is False


class TestChatCompletionRequestsDataAccessSafety:
    """Test safe database access patterns in src/db/chat_completion_requests.py"""

    @patch('src.db.chat_completion_requests.get_supabase_client')
    def test_get_model_id_by_name_handles_empty_provider_result(self, mock_get_client):
        """Test that get_model_id_by_name handles missing provider safely"""
        from src.db.chat_completion_requests import get_model_id_by_name

        # Mock empty provider lookup
        mock_client = Mock()
        provider_result = Mock()
        provider_result.data = []
        mock_client.table.return_value.select.return_value.or_.return_value.execute.return_value = provider_result
        mock_get_client.return_value = mock_client

        # Should not crash, should fall back to searching without provider filter
        result = get_model_id_by_name("gpt-4", provider_name="nonexistent")
        assert result is None  # No model found

    @patch('src.db.chat_completion_requests.get_supabase_client')
    def test_get_model_id_by_name_handles_empty_model_result(self, mock_get_client):
        """Test that get_model_id_by_name handles missing model safely"""
        from src.db.chat_completion_requests import get_model_id_by_name

        # Mock empty model lookup
        mock_client = Mock()
        model_result = Mock()
        model_result.data = []

        # Both provider search and fallback search return empty
        mock_client.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = model_result
        mock_client.table.return_value.select.return_value.or_.return_value.limit.return_value.execute.return_value = model_result
        mock_get_client.return_value = mock_client

        # Should return None (not crash)
        result = get_model_id_by_name("nonexistent_model")
        assert result is None

    @patch('src.db.chat_completion_requests.get_supabase_client')
    def test_save_chat_completion_request_handles_empty_insert_result(self, mock_get_client):
        """Test that save_chat_completion_request handles failed insert safely"""
        from src.db.chat_completion_requests import save_chat_completion_request

        # Mock empty insert result
        mock_client = Mock()
        mock_result = Mock()
        mock_result.data = []  # Insert failed
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_result
        mock_get_client.return_value = mock_client

        # Should return None (not crash)
        result = save_chat_completion_request(
            request_id="req_123",
            model_name="gpt-4",
            input_tokens=10,
            output_tokens=20,
            processing_time_ms=100
        )
        assert result is None

    @patch('src.db.chat_completion_requests.get_supabase_client')
    def test_get_model_performance_metrics_handles_empty_model_data(self, mock_get_client):
        """Test that get_model_performance_metrics handles missing model info safely"""
        from src.db.chat_completion_requests import get_model_performance_metrics

        # Mock empty model lookup
        mock_client = Mock()

        # Requests return data
        requests_result = Mock()
        requests_result.data = [
            {"input_tokens": 10, "output_tokens": 20, "processing_time_ms": 100}
        ]

        # Model lookup returns empty (model not found)
        model_result = Mock()
        model_result.data = []

        def mock_table_select(*args, **kwargs):
            mock_select = Mock()
            if "chat_completion_requests" in str(args):
                mock_select.eq.return_value.order.return_value.limit.return_value.execute.return_value = requests_result
            else:  # models table
                mock_select.eq.return_value.execute.return_value = model_result
            return mock_select

        mock_client.table.side_effect = lambda table_name: Mock(select=mock_table_select)
        mock_get_client.return_value = mock_client

        # Should use "Unknown" for model name (not crash)
        result = get_model_performance_metrics(model_id=999, hours=24)
        assert result["model_name"] == "Unknown"
        assert result["provider"] == "unknown"


class TestAISDKClientProviderSafety:
    """Test safe provider response handling in src/services/ai_sdk_client.py"""

    def test_process_ai_sdk_response_handles_empty_choices(self):
        """Test that _process_ai_sdk_response handles empty choices safely"""
        from src.services.ai_sdk_client import _process_ai_sdk_response

        # Mock response with empty choices
        mock_response = Mock()
        mock_response.choices = []  # Empty choices
        mock_response.usage = Mock(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        # Should raise ProviderError (not IndexError)
        with pytest.raises(ProviderError, match="AI SDK returned empty choices"):
            _process_ai_sdk_response(mock_response)

    def test_process_ai_sdk_response_handles_missing_choices_attribute(self):
        """Test that _process_ai_sdk_response handles missing choices attribute safely"""
        from src.services.ai_sdk_client import _process_ai_sdk_response

        # Mock response without choices attribute
        mock_response = Mock(spec=[])  # spec=[] means no attributes
        delattr(mock_response, 'choices')  # Ensure no choices attribute

        # Should raise ProviderError (not AttributeError)
        with pytest.raises(ProviderError):
            _process_ai_sdk_response(mock_response)

    def test_process_ai_sdk_response_handles_missing_message_attributes(self):
        """Test that _process_ai_sdk_response handles missing message attributes safely"""
        from src.services.ai_sdk_client import _process_ai_sdk_response

        # Mock response with choice but missing message attributes
        mock_choice = Mock(spec=['message', 'finish_reason'])
        mock_choice.message = Mock(spec=[])  # Message without role/content
        mock_choice.finish_reason = "stop"

        mock_response = Mock()
        mock_response.choices = [mock_choice]
        mock_response.usage = Mock(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        # Should use defaults (not crash with AttributeError)
        result = _process_ai_sdk_response(mock_response)

        assert result["choices"][0]["message"]["role"] == "assistant"  # Default role
        assert result["choices"][0]["message"]["content"] == ""  # Default content
        assert result["choices"][0]["finish_reason"] == "stop"

    def test_process_ai_sdk_response_handles_valid_response(self):
        """Test that _process_ai_sdk_response processes valid response correctly"""
        from src.services.ai_sdk_client import _process_ai_sdk_response

        # Mock valid response
        mock_message = Mock()
        mock_message.role = "assistant"
        mock_message.content = "Hello, world!"

        mock_choice = Mock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = Mock()
        mock_response.choices = [mock_choice]
        mock_response.usage = Mock(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        # Should process successfully
        result = _process_ai_sdk_response(mock_response)

        assert result["choices"][0]["message"]["role"] == "assistant"
        assert result["choices"][0]["message"]["content"] == "Hello, world!"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 20
        assert result["usage"]["total_tokens"] == 30


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple fixes"""

    @patch('src.db.api_keys.get_supabase_client')
    @patch('src.db.chat_completion_requests.get_supabase_client')
    def test_concurrent_empty_result_handling(self, mock_chat_client, mock_api_client):
        """Test that multiple concurrent empty results don't cause crashes"""
        from src.db.api_keys import get_api_key_usage_stats
        from src.db.chat_completion_requests import get_model_id_by_name

        # Mock empty results for both
        empty_result = Mock()
        empty_result.data = []

        mock_api_client.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value = empty_result
        mock_chat_client.return_value.table.return_value.select.return_value.or_.return_value.limit.return_value.execute.return_value = empty_result

        # Both should handle gracefully
        api_stats = get_api_key_usage_stats("sk_test_missing")
        model_id = get_model_id_by_name("nonexistent_model")

        assert api_stats["key_name"] == "Unknown"
        assert model_id is None

    def test_error_propagation_chain(self):
        """Test that errors propagate correctly through the chain"""
        from src.utils.db_safety import DatabaseResultError, safe_get_first

        # Create a mock empty result
        mock_result = Mock()
        mock_result.data = []

        # Should raise DatabaseResultError
        with pytest.raises(DatabaseResultError, match="Test error"):
            safe_get_first(mock_result, error_message="Test error")


# Pytest configuration
pytestmark = pytest.mark.unit
