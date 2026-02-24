"""
Tests for model health tracking database operations.

These tests verify the upsert behavior and race condition handling
for the model_health_tracking table.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from postgrest.exceptions import APIError


class TestRecordModelCall:
    """Tests for the record_model_call function"""

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_new_record(self, mock_get_client):
        """Test recording a new model call creates a record"""
        from src.db.model_health import record_model_call

        # Mock the supabase client
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock select query returning empty (no existing record)
        mock_select = Mock()
        mock_select.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        # Mock upsert response
        mock_upsert = Mock()
        mock_upsert.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "call_count": 1,
                "success_count": 1,
                "error_count": 0,
            }
        ]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_upsert

        result = record_model_call(
            provider="openrouter",
            model="gpt-4",
            response_time_ms=150.5,
            status="success",
        )

        assert result == mock_upsert.data[0]
        # Verify upsert was called with on_conflict
        mock_client.table.return_value.upsert.assert_called_once()
        call_args = mock_client.table.return_value.upsert.call_args
        assert call_args[1]["on_conflict"] == "provider,model"

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_existing_record(self, mock_get_client):
        """Test recording a model call updates existing record"""
        from src.db.model_health import record_model_call

        # Mock the supabase client
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock select query returning existing record
        mock_select = Mock()
        mock_select.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "call_count": 10,
                "success_count": 9,
                "error_count": 1,
                "average_response_time_ms": 100.0,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        # Mock upsert response
        mock_upsert = Mock()
        mock_upsert.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "call_count": 11,
                "success_count": 10,
                "error_count": 1,
            }
        ]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_upsert

        result = record_model_call(
            provider="openrouter",
            model="gpt-4",
            response_time_ms=150.5,
            status="success",
        )

        assert result["call_count"] == 11
        assert result["success_count"] == 10

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_error_status(self, mock_get_client):
        """Test recording an error increments error count"""
        from src.db.model_health import record_model_call

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock select query returning empty
        mock_select = Mock()
        mock_select.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        # Mock upsert response
        mock_upsert = Mock()
        mock_upsert.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "call_count": 1,
                "success_count": 0,
                "error_count": 1,
                "last_error_message": "Connection timeout",
            }
        ]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_upsert

        result = record_model_call(
            provider="openrouter",
            model="gpt-4",
            response_time_ms=5000.0,
            status="error",
            error_message="Connection timeout",
        )

        assert result["error_count"] == 1
        assert result["success_count"] == 0

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_table_not_found(self, mock_get_client):
        """Test graceful handling when table doesn't exist"""
        from src.db.model_health import record_model_call

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock table not found error
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.side_effect = APIError(
            {"message": "Could not find the table", "code": "PGRST205"}
        )

        result = record_model_call(
            provider="openrouter",
            model="gpt-4",
            response_time_ms=150.5,
            status="success",
        )

        # Should return empty dict and not raise
        assert result == {}

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_duplicate_key_handled(self, mock_get_client):
        """Test duplicate key error is handled gracefully (race condition)"""
        from src.db.model_health import record_model_call

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock select query returning empty
        mock_select = Mock()
        mock_select.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        # Mock duplicate key error on upsert (simulating race condition)
        mock_client.table.return_value.upsert.return_value.execute.side_effect = APIError(
            {
                "message": "duplicate key value violates unique constraint",
                "code": "23505",
            }
        )

        result = record_model_call(
            provider="alibaba-cloud",
            model="qwen-plus",
            response_time_ms=150.5,
            status="success",
        )

        # Should return empty dict and not raise
        assert result == {}

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_with_tokens(self, mock_get_client):
        """Test recording model call with token usage"""
        from src.db.model_health import record_model_call

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_select = Mock()
        mock_select.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        mock_upsert = Mock()
        mock_upsert.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            }
        ]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_upsert

        result = record_model_call(
            provider="openrouter",
            model="gpt-4",
            response_time_ms=150.5,
            status="success",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )

        # Verify upsert was called with token data
        call_args = mock_client.table.return_value.upsert.call_args[0][0]
        assert call_args.get("input_tokens") == 100
        assert call_args.get("output_tokens") == 50
        assert call_args.get("total_tokens") == 150

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_with_gateway(self, mock_get_client):
        """Test recording model call with explicit gateway parameter"""
        from src.db.model_health import record_model_call

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_select = Mock()
        mock_select.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        mock_upsert = Mock()
        mock_upsert.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "gateway": "openrouter",
            }
        ]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_upsert

        record_model_call(
            provider="openrouter",
            model="gpt-4",
            response_time_ms=150.5,
            status="success",
            gateway="openrouter",
        )

        # Verify upsert was called with gateway
        call_args = mock_client.table.return_value.upsert.call_args[0][0]
        assert call_args.get("gateway") == "openrouter"

    @patch("src.db.model_health.get_supabase_client")
    def test_record_model_call_gateway_defaults_to_provider(self, mock_get_client):
        """Test gateway defaults to provider when not specified"""
        from src.db.model_health import record_model_call

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_select = Mock()
        mock_select.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_select
        )

        mock_upsert = Mock()
        mock_upsert.data = [
            {
                "provider": "featherless",
                "model": "meta-llama/Llama-3-70b",
                "gateway": "featherless",
            }
        ]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_upsert

        # Call without gateway parameter
        record_model_call(
            provider="featherless",
            model="meta-llama/Llama-3-70b",
            response_time_ms=200.0,
            status="success",
        )

        # Verify gateway defaults to provider value
        call_args = mock_client.table.return_value.upsert.call_args[0][0]
        assert call_args.get("gateway") == "featherless"


class TestGetModelHealth:
    """Tests for the get_model_health function"""

    @patch("src.db.model_health.get_supabase_client")
    def test_get_model_health_found(self, mock_get_client):
        """Test retrieving health data for existing model"""
        from src.db.model_health import get_model_health

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_result = Mock()
        mock_result.data = [
            {
                "provider": "openrouter",
                "model": "gpt-4",
                "call_count": 100,
                "success_count": 95,
                "error_count": 5,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_result
        )

        result = get_model_health("openrouter", "gpt-4")

        assert result is not None
        assert result["call_count"] == 100
        assert result["success_count"] == 95

    @patch("src.db.model_health.get_supabase_client")
    def test_get_model_health_not_found(self, mock_get_client):
        """Test retrieving health data for non-existent model"""
        from src.db.model_health import get_model_health

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_result = Mock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_result
        )

        result = get_model_health("openrouter", "non-existent-model")

        assert result is None


class TestGetUnhealthyModels:
    """Tests for the get_unhealthy_models function"""

    @patch("src.db.model_health.get_supabase_client")
    def test_get_unhealthy_models(self, mock_get_client):
        """Test retrieving models with high error rates"""
        from src.db.model_health import get_unhealthy_models

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_result = Mock()
        mock_result.data = [
            {
                "provider": "provider1",
                "model": "model1",
                "call_count": 100,
                "success_count": 70,
                "error_count": 30,  # 30% error rate
            },
            {
                "provider": "provider2",
                "model": "model2",
                "call_count": 100,
                "success_count": 95,
                "error_count": 5,  # 5% error rate
            },
        ]
        mock_client.table.return_value.select.return_value.gte.return_value.execute.return_value = (
            mock_result
        )

        result = get_unhealthy_models(error_threshold=0.2, min_calls=10)

        # Only model1 should be returned (30% > 20% threshold)
        assert len(result) == 1
        assert result[0]["model"] == "model1"
        assert result[0]["error_rate"] == 0.3


class TestGetModelHealthStats:
    """Tests for the get_model_health_stats function"""

    @patch("src.db.model_health.get_supabase_client")
    def test_get_model_health_stats(self, mock_get_client):
        """Test aggregating health stats across all models"""
        from src.db.model_health import get_model_health_stats

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_result = Mock()
        mock_result.data = [
            {
                "call_count": 100,
                "success_count": 90,
                "error_count": 10,
                "average_response_time_ms": 150.0,
            },
            {
                "call_count": 200,
                "success_count": 180,
                "error_count": 20,
                "average_response_time_ms": 200.0,
            },
        ]
        mock_client.table.return_value.select.return_value.execute.return_value = mock_result

        result = get_model_health_stats()

        assert result["total_models"] == 2
        assert result["total_calls"] == 300
        assert result["total_success"] == 270
        assert result["total_errors"] == 30
        assert result["success_rate"] == 0.9  # 270/300

    @patch("src.db.model_health.get_supabase_client")
    def test_get_model_health_stats_empty(self, mock_get_client):
        """Test stats with no models tracked"""
        from src.db.model_health import get_model_health_stats

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_result = Mock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.execute.return_value = mock_result

        result = get_model_health_stats()

        assert result["total_models"] == 0
        assert result["total_calls"] == 0
        assert result["success_rate"] == 0
