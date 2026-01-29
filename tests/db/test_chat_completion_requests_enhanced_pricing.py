"""
Tests for chat_completion_requests_enhanced.py pricing column fixes

Ensures that the backfill_request_costs function correctly uses the model_pricing
table instead of the removed pricing_prompt/pricing_completion columns.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.db.chat_completion_requests_enhanced import backfill_request_costs


class TestBackfillRequestCostsPricingFix:
    """Test that backfill_request_costs uses model_pricing table correctly"""

    @patch("src.db.chat_completion_requests_enhanced.get_supabase_client")
    @patch("src.db.chat_completion_requests_enhanced.update_request_cost")
    def test_backfill_uses_model_pricing_table(
        self, mock_update_cost, mock_get_client
    ):
        """Test that backfill fetches pricing from model_pricing table"""
        # Setup mock client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock the requests without cost data
        mock_requests_response = MagicMock()
        mock_requests_response.data = [
            {
                "request_id": "req-123",
                "model_id": 1,
                "input_tokens": 100,
                "output_tokens": 200,
            }
        ]

        # Mock the pricing data from model_pricing table
        mock_pricing_response = MagicMock()
        mock_pricing_response.data = {
            "price_per_input_token": 0.00003,  # $0.03 per 1K tokens
            "price_per_output_token": 0.00006,  # $0.06 per 1K tokens
        }

        # Setup the mock chain for requests query
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_is = MagicMock()
        mock_eq = MagicMock()
        mock_range = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.is_.return_value = mock_is
        mock_is.eq.return_value = mock_eq
        mock_eq.range.return_value = mock_range
        mock_range.execute.return_value = mock_requests_response

        # Setup mock for pricing query (second table call)
        mock_pricing_table = MagicMock()
        mock_pricing_select = MagicMock()
        mock_pricing_eq = MagicMock()
        mock_pricing_single = MagicMock()

        # Make the second .table() call return the pricing table mock
        mock_client.table.side_effect = [
            mock_table,  # First call for requests
            mock_pricing_table,  # Second call for pricing
        ]

        mock_pricing_table.select.return_value = mock_pricing_select
        mock_pricing_select.eq.return_value = mock_pricing_eq
        mock_pricing_eq.single.return_value = mock_pricing_single
        mock_pricing_single.execute.return_value = mock_pricing_response

        # Mock successful update
        mock_update_cost.return_value = True

        # Call the backfill function
        result = backfill_request_costs(limit=10, offset=0)

        # Assertions
        assert result["processed"] == 1
        assert result["updated"] == 1

        # Verify it queried the model_pricing table
        mock_pricing_table.select.assert_called_once_with(
            "price_per_input_token, price_per_output_token"
        )
        mock_pricing_select.eq.assert_called_once_with("model_id", 1)

        # Verify the cost calculation
        # input_cost = 100 * 0.00003 = 0.003
        # output_cost = 200 * 0.00006 = 0.012
        # total_cost = 0.015
        mock_update_cost.assert_called_once_with(
            request_id="req-123",
            cost_usd=0.015,
            input_cost_usd=0.003,
            output_cost_usd=0.012,
            pricing_source="backfilled",
        )

    @patch("src.db.chat_completion_requests_enhanced.get_supabase_client")
    @patch("src.db.chat_completion_requests_enhanced.update_request_cost")
    def test_backfill_skips_when_no_pricing_data(
        self, mock_update_cost, mock_get_client
    ):
        """Test that backfill skips requests when pricing data is missing"""
        # Setup mock client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock the requests without cost data
        mock_requests_response = MagicMock()
        mock_requests_response.data = [
            {
                "request_id": "req-456",
                "model_id": 2,
                "input_tokens": 50,
                "output_tokens": 100,
            }
        ]

        # Mock empty pricing data (model not found in model_pricing table)
        mock_pricing_response = MagicMock()
        mock_pricing_response.data = None

        # Setup the mock chain
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_is = MagicMock()
        mock_eq = MagicMock()
        mock_range = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.is_.return_value = mock_is
        mock_is.eq.return_value = mock_eq
        mock_eq.range.return_value = mock_range
        mock_range.execute.return_value = mock_requests_response

        # Setup pricing query mock
        mock_pricing_table = MagicMock()
        mock_pricing_select = MagicMock()
        mock_pricing_eq = MagicMock()
        mock_pricing_single = MagicMock()

        mock_client.table.side_effect = [
            mock_table,
            mock_pricing_table,
        ]

        mock_pricing_table.select.return_value = mock_pricing_select
        mock_pricing_select.eq.return_value = mock_pricing_eq
        mock_pricing_eq.single.return_value = mock_pricing_single
        mock_pricing_single.execute.return_value = mock_pricing_response

        # Call the backfill function
        result = backfill_request_costs(limit=10, offset=0)

        # Assertions - should skip the request
        assert result["processed"] == 1
        assert result["updated"] == 0

        # Should not call update_request_cost
        mock_update_cost.assert_not_called()
