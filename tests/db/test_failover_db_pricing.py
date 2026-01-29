"""
Tests for failover_db.py pricing column fixes

Ensures that get_providers_for_model correctly uses the model_pricing
table instead of the removed pricing columns.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.db.failover_db import get_providers_for_model


class TestFailoverDbPricingFix:
    """Test that failover_db uses model_pricing table correctly"""

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_uses_model_pricing_table(self, mock_get_client):
        """Test that get_providers_for_model fetches pricing from model_pricing table"""
        # Setup mock client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock response data with model_pricing relationship
        mock_response = MagicMock()
        mock_response.data = [
            {
                "id": 1,
                "model_id": "gpt-4",
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
                    "price_per_image": None,
                    "price_per_request": None,
                },
                "providers": {
                    "id": 1,
                    "slug": "openrouter",
                    "name": "OpenRouter",
                    "health_status": "healthy",
                    "average_response_time_ms": 150,
                    "is_active": True,
                    "supports_streaming": True,
                    "supports_function_calling": True,
                    "supports_vision": False,
                },
            }
        ]

        # Setup the mock chain
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq1 = MagicMock()
        mock_eq2 = MagicMock()
        mock_eq3 = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq1
        mock_eq1.eq.return_value = mock_eq2
        mock_eq2.eq.return_value = mock_eq3
        mock_eq3.execute.return_value = mock_response

        # Call the function
        providers = get_providers_for_model("gpt-4", active_only=True)

        # Assertions
        assert len(providers) == 1

        provider = providers[0]
        assert provider["model_id"] == "gpt-4"
        assert provider["provider_slug"] == "openrouter"

        # Verify pricing is correctly extracted from model_pricing table
        assert provider["pricing_prompt"] == 0.00003
        assert provider["pricing_completion"] == 0.00006
        assert provider["pricing_image"] == 0.0
        assert provider["pricing_request"] == 0.0

        # Verify the query included model_pricing in select
        select_call = mock_table.select.call_args[0][0]
        assert "model_pricing" in select_call
        assert "price_per_input_token" in select_call
        assert "price_per_output_token" in select_call

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_handles_missing_pricing_data(self, mock_get_client):
        """Test that get_providers_for_model handles models without pricing data"""
        # Setup mock client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock response with null model_pricing (model not in model_pricing table)
        mock_response = MagicMock()
        mock_response.data = [
            {
                "id": 2,
                "model_id": "custom-model",
                "provider_model_id": "custom/model",
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
                    "slug": "custom-provider",
                    "name": "Custom Provider",
                    "health_status": "healthy",
                    "average_response_time_ms": 200,
                    "is_active": True,
                    "supports_streaming": False,
                    "supports_function_calling": False,
                    "supports_vision": False,
                },
            }
        ]

        # Setup the mock chain
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq
        mock_eq.execute.return_value = mock_response

        # Call the function
        providers = get_providers_for_model("custom-model", active_only=False)

        # Assertions - should handle None pricing gracefully
        assert len(providers) == 1

        provider = providers[0]
        # Should default to 0.0 for all pricing fields
        assert provider["pricing_prompt"] == 0.0
        assert provider["pricing_completion"] == 0.0
        assert provider["pricing_image"] == 0.0
        assert provider["pricing_request"] == 0.0

    @patch("src.db.failover_db.get_supabase_client")
    def test_get_providers_sorts_by_pricing(self, mock_get_client):
        """Test that providers are sorted by pricing (among other factors)"""
        # Setup mock client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock response with multiple providers, different pricing
        mock_response = MagicMock()
        mock_response.data = [
            {
                "id": 1,
                "model_id": "llama-3-70b",
                "provider_model_id": "meta/llama-3-70b",
                "average_response_time_ms": 150,
                "health_status": "healthy",
                "success_rate": 98.0,
                "is_active": True,
                "supports_streaming": True,
                "supports_function_calling": False,
                "supports_vision": False,
                "context_length": 8192,
                "model_pricing": {
                    "price_per_input_token": 0.00005,  # More expensive
                    "price_per_output_token": 0.00010,
                    "price_per_image": None,
                    "price_per_request": None,
                },
                "providers": {
                    "id": 1,
                    "slug": "provider-a",
                    "name": "Provider A",
                    "health_status": "healthy",
                    "average_response_time_ms": 150,
                    "is_active": True,
                    "supports_streaming": True,
                    "supports_function_calling": False,
                    "supports_vision": False,
                },
            },
            {
                "id": 2,
                "model_id": "llama-3-70b",
                "provider_model_id": "llama-3-70b",
                "average_response_time_ms": 150,
                "health_status": "healthy",
                "success_rate": 98.0,
                "is_active": True,
                "supports_streaming": True,
                "supports_function_calling": False,
                "supports_vision": False,
                "context_length": 8192,
                "model_pricing": {
                    "price_per_input_token": 0.00002,  # Cheaper
                    "price_per_output_token": 0.00004,
                    "price_per_image": None,
                    "price_per_request": None,
                },
                "providers": {
                    "id": 2,
                    "slug": "provider-b",
                    "name": "Provider B",
                    "health_status": "healthy",
                    "average_response_time_ms": 150,
                    "is_active": True,
                    "supports_streaming": True,
                    "supports_function_calling": False,
                    "supports_vision": False,
                },
            },
        ]

        # Setup the mock chain
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq
        mock_eq.execute.return_value = mock_response

        # Call the function
        providers = get_providers_for_model("llama-3-70b", active_only=False)

        # Assertions - cheaper provider should come first (same health, same speed)
        assert len(providers) == 2
        assert providers[0]["provider_slug"] == "provider-b"  # Cheaper
        assert providers[1]["provider_slug"] == "provider-a"  # More expensive
