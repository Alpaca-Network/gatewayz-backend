"""
Tests for pricing_lookup.py model_name migration fix

Tests that pricing lookup correctly uses model_name instead of dropped model_id column
"""

from unittest.mock import Mock, patch
from src.services.pricing_lookup import (
    _get_pricing_from_database,
    enrich_model_with_pricing,
)


class TestPricingLookupModelNameFix:
    """Test that pricing lookup uses model_name instead of model_id"""

    @patch("src.services.pricing_lookup.get_supabase_client")
    def test_get_pricing_from_database_uses_model_name(self, mock_get_client):
        """Test that _get_pricing_from_database queries by model_name, not model_id"""
        # Setup
        model_name = "test-model"
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Create mock chain for query builder
        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": model_name,
            "model_pricing": {
                "price_per_input_token": 0.000001,
                "price_per_output_token": 0.000002,
            }
        }])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.select.return_value = mock_model_name_eq

        mock_client.table.return_value = mock_select

        # Execute
        result = _get_pricing_from_database(model_name)

        # Verify
        assert result is not None
        assert result["prompt"] == "1.0"  # 0.000001 * 1M
        assert result["completion"] == "2.0"  # 0.000002 * 1M

        # Verify query chain - should use model_name, not model_id
        mock_client.table.assert_called_once_with("models")
        select_call = mock_select.select.call_args[0][0]
        assert "model_name" in select_call
        assert "model_id" not in select_call or "model_pricing" in select_call  # model_id only in join

        # Verify eq was called with model_name
        eq_calls = mock_model_name_eq.eq.call_args_list
        assert any(call[0][0] == "model_name" and call[0][1] == model_name for call in eq_calls)

    @patch("src.services.pricing_lookup.get_supabase_client")
    def test_get_pricing_from_database_handles_missing_model(self, mock_get_client):
        """Test handling when model not found by model_name"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.select.return_value = mock_model_name_eq

        mock_client.table.return_value = mock_select

        # Execute
        result = _get_pricing_from_database("nonexistent-model")

        # Verify
        assert result is None

    @patch("src.services.pricing_lookup.get_supabase_client")
    def test_get_pricing_from_database_handles_missing_pricing_data(self, mock_get_client):
        """Test handling when model exists but has no pricing data"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "test-model",
            "model_pricing": None  # No pricing data
        }])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.select.return_value = mock_model_name_eq

        mock_client.table.return_value = mock_select

        # Execute
        result = _get_pricing_from_database("test-model")

        # Verify
        assert result is None

    @patch("src.services.pricing_lookup._get_pricing_from_database")
    def test_enrich_model_with_pricing_uses_database_first(self, mock_db_pricing):
        """Test that enrich_model_with_pricing tries database first"""
        # Setup
        mock_db_pricing.return_value = {
            "prompt": "1.0",
            "completion": "2.0",
            "request": "0",
            "image": "0"
        }

        model_data = {
            "id": "test-model",
            "name": "Test Model"
        }

        # Execute
        result = enrich_model_with_pricing(model_data, "test-gateway")

        # Verify
        assert result is not None
        assert result["pricing"]["prompt"] == "1.0"
        assert result["pricing"]["completion"] == "2.0"
        assert result["pricing_source"] == "database"
        mock_db_pricing.assert_called_once_with("test-model")

    @patch("src.services.pricing_lookup.get_supabase_client")
    def test_database_error_logged_correctly(self, mock_get_client):
        """Test that database errors are logged with correct model name"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client
        mock_client.table.side_effect = Exception("Database connection error")

        # Execute
        result = _get_pricing_from_database("test-model")

        # Verify
        assert result is None  # Should return None on error

    @patch("src.services.pricing_lookup.get_supabase_client")
    def test_get_pricing_converts_per_token_to_per_1m(self, mock_get_client):
        """Test that pricing is correctly converted from per-token to per-1M format"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Database stores per-token: 0.0000009 = $0.90 per 1M
        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "test-model",
            "model_pricing": {
                "price_per_input_token": 0.0000009,
                "price_per_output_token": 0.0000018,
            }
        }])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.select.return_value = mock_model_name_eq

        mock_client.table.return_value = mock_select

        # Execute
        result = _get_pricing_from_database("test-model")

        # Verify conversion: per-token * 1M = per-1M
        assert result is not None
        assert result["prompt"] == "0.9"  # 0.0000009 * 1,000,000
        assert result["completion"] == "1.8"  # 0.0000018 * 1,000,000
        assert result["request"] == "0"
        assert result["image"] == "0"
