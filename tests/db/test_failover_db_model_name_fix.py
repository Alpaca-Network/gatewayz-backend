"""
Tests for failover_db.py model_name migration fix

Tests that failover queries correctly use model_name instead of dropped model_id column
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from src.db.failover_db import (
    get_providers_for_model,
    get_provider_model_id,
)


class TestFailoverDbModelNameFix:
    """Test that failover_db uses model_name instead of model_id"""

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_for_model_uses_model_name(self, mock_get_client):
        """Test that get_providers_for_model queries by model_name, not model_id"""
        # Setup
        model_name = "gpt-4"
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Create mock response
        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": model_name,
            "provider_model_id": "openai/gpt-4",
            "average_response_time_ms": 150,
            "health_status": "healthy",
            "success_rate": 98.5,
            "is_active": True,
            "supports_streaming": True,
            "supports_function_calling": True,
            "supports_vision": False,
            "context_length": 8192,
            "model_pricing": {
                "price_per_input_token": 0.00003,
                "price_per_output_token": 0.00006,
                "price_per_image_token": 0,
                "price_per_request": 0,
            },
            "providers": {
                "id": 1,
                "slug": "openrouter",
                "name": "OpenRouter",
                "health_status": "healthy",
                "average_response_time_ms": 100,
                "is_active": True,
                "supports_streaming": True,
                "supports_function_calling": True,
                "supports_vision": False,
            }
        }])

        # Build query chain
        mock_eq_active = Mock()
        mock_eq_active.execute = mock_execute.execute

        mock_eq_model = Mock()
        mock_eq_model.eq.return_value = mock_eq_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_eq_model

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_providers_for_model(model_name)

        # Verify
        assert len(result) == 1
        assert result[0]["model_id"] == model_name
        assert result[0]["provider_slug"] == "openrouter"
        assert result[0]["pricing_prompt"] == 0.00003
        assert result[0]["pricing_completion"] == 0.00006

        # Verify query uses model_name
        eq_calls = mock_select.eq.call_args_list
        assert any(call[0][0] == "model_name" and call[0][1] == model_name for call in eq_calls)

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_extracts_pricing_from_model_pricing_table(self, mock_get_client):
        """Test that pricing is extracted from model_pricing relationship, not direct columns"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "test-model",
            "provider_model_id": "test/model",
            "average_response_time_ms": 200,
            "health_status": "healthy",
            "success_rate": 95.0,
            "is_active": True,
            "supports_streaming": False,
            "supports_function_calling": False,
            "supports_vision": False,
            "context_length": 4096,
            "model_pricing": {  # Pricing from model_pricing table
                "price_per_input_token": 0.000001,
                "price_per_output_token": 0.000002,
                "price_per_image_token": 0.000003,
                "price_per_request": 0.01,
            },
            "providers": {
                "id": 2,
                "slug": "test-provider",
                "name": "Test Provider",
                "health_status": "healthy",
                "average_response_time_ms": 150,
                "is_active": True,
                "supports_streaming": False,
                "supports_function_calling": False,
                "supports_vision": False,
            }
        }])

        mock_eq_active = Mock()
        mock_eq_active.execute = mock_execute.execute

        mock_eq_model = Mock()
        mock_eq_model.eq.return_value = mock_eq_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_eq_model

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_providers_for_model("test-model")

        # Verify pricing extracted from model_pricing table
        assert len(result) == 1
        assert result[0]["pricing_prompt"] == 0.000001
        assert result[0]["pricing_completion"] == 0.000002
        assert result[0]["pricing_image"] == 0.000003
        assert result[0]["pricing_request"] == 0.01

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_handles_missing_pricing_data(self, mock_get_client):
        """Test that missing pricing data is handled gracefully"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "test-model",
            "provider_model_id": "test/model",
            "average_response_time_ms": 200,
            "health_status": "healthy",
            "success_rate": 95.0,
            "is_active": True,
            "supports_streaming": False,
            "supports_function_calling": False,
            "supports_vision": False,
            "context_length": 4096,
            "model_pricing": None,  # No pricing data
            "providers": {
                "id": 2,
                "slug": "test-provider",
                "name": "Test Provider",
                "health_status": "healthy",
                "average_response_time_ms": 150,
                "is_active": True,
                "supports_streaming": False,
                "supports_function_calling": False,
                "supports_vision": False,
            }
        }])

        mock_eq_active = Mock()
        mock_eq_active.execute = mock_execute.execute

        mock_eq_model = Mock()
        mock_eq_model.eq.return_value = mock_eq_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_eq_model

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_providers_for_model("test-model")

        # Verify defaults to 0
        assert len(result) == 1
        assert result[0]["pricing_prompt"] == 0.0
        assert result[0]["pricing_completion"] == 0.0
        assert result[0]["pricing_image"] == 0.0
        assert result[0]["pricing_request"] == 0.0

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_provider_model_id_uses_model_name(self, mock_get_client):
        """Test that get_provider_model_id queries by model_name, not model_id"""
        # Setup
        canonical_model_id = "gpt-4"
        provider_slug = "openrouter"
        expected_provider_model_id = "openai/gpt-4"

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data={"provider_model_id": expected_provider_model_id})

        mock_single = Mock()
        mock_single.single.return_value = mock_execute

        mock_eq_provider = Mock()
        mock_eq_provider.eq.return_value = mock_single

        mock_eq_model = Mock()
        mock_eq_model.eq.return_value = mock_eq_provider

        mock_select = Mock()
        mock_select.eq.return_value = mock_eq_model

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_provider_model_id(canonical_model_id, provider_slug)

        # Verify
        assert result == expected_provider_model_id

        # Verify query uses model_name
        eq_calls = mock_select.eq.call_args_list
        assert any(call[0][0] == "model_name" and call[0][1] == canonical_model_id for call in eq_calls)

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_returns_empty_list_for_nonexistent_model(self, mock_get_client):
        """Test that empty list is returned when model not found"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[])

        mock_eq_active = Mock()
        mock_eq_active.execute = mock_execute.execute

        mock_eq_model = Mock()
        mock_eq_model.eq.return_value = mock_eq_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_eq_model

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_providers_for_model("nonexistent-model")

        # Verify
        assert result == []
