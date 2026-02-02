"""
Tests for pricing.py model_name migration fix

Tests that pricing queries correctly use model_name instead of dropped model_id column
"""

from unittest.mock import Mock, patch
from src.services.pricing import get_model_pricing_from_db


class TestPricingModelNameFix:
    """Test that pricing.py uses model_name instead of model_id"""

    @patch("src.services.pricing.get_supabase_client")
    @patch("src.services.pricing.track_database_query")
    def test_get_model_pricing_from_db_uses_model_name(self, mock_track, mock_get_client):
        """Test that get_model_pricing_from_db queries by model_name, not model_id"""
        # Setup
        model_name = "gpt-4"
        candidate_ids = [model_name, "openai/gpt-4"]

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock track_database_query as context manager
        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock()

        # Create mock response for first candidate
        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": model_name,
            "model_pricing": {
                "price_per_input_token": 0.00003,
                "price_per_output_token": 0.00006,
            }
        }])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_model_name_eq

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_model_pricing_from_db(candidate_ids)

        # Verify
        assert result is not None
        assert result["prompt"] == "30.0"  # 0.00003 * 1M
        assert result["completion"] == "60.0"  # 0.00006 * 1M

        # Verify query uses model_name
        select_call = mock_table.select.call_args[0][0]
        assert "model_name" in select_call
        eq_calls = mock_select.eq.call_args_list
        assert any(call[0][0] == "model_name" for call in eq_calls)

    @patch("src.services.pricing.get_supabase_client")
    @patch("src.services.pricing.track_database_query")
    def test_get_model_pricing_tries_multiple_candidates(self, mock_track, mock_get_client):
        """Test that function tries multiple candidate model names"""
        # Setup
        candidate_ids = ["gpt-4", "openai/gpt-4", "gpt-4-turbo"]

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock()

        # First candidate returns empty
        mock_execute_1 = Mock()
        mock_execute_1.execute.return_value = Mock(data=[])

        # Second candidate returns pricing
        mock_execute_2 = Mock()
        mock_execute_2.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "openai/gpt-4",
            "model_pricing": {
                "price_per_input_token": 0.00003,
                "price_per_output_token": 0.00006,
            }
        }])

        # Setup query chain to return different results
        call_count = [0]

        def get_execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_execute_1
            return mock_execute_2

        mock_limit = Mock()
        mock_limit.limit = get_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_model_name_eq

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_model_pricing_from_db(candidate_ids)

        # Verify found on second candidate
        assert result is not None
        assert result["prompt"] == "30.0"

    @patch("src.services.pricing.get_supabase_client")
    @patch("src.services.pricing.track_database_query")
    def test_get_model_pricing_returns_none_if_no_match(self, mock_track, mock_get_client):
        """Test that None is returned when no candidates match"""
        # Setup
        candidate_ids = ["nonexistent-model"]

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock()

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_model_name_eq

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_model_pricing_from_db(candidate_ids)

        # Verify
        assert result is None

    @patch("src.services.pricing.get_supabase_client")
    @patch("src.services.pricing.track_database_query")
    def test_get_model_pricing_skips_empty_candidates(self, mock_track, mock_get_client):
        """Test that empty candidate strings are skipped"""
        # Setup
        candidate_ids = ["", None, "gpt-4"]

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock()

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "gpt-4",
            "model_pricing": {
                "price_per_input_token": 0.00003,
                "price_per_output_token": 0.00006,
            }
        }])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_model_name_eq

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_model_pricing_from_db(candidate_ids)

        # Verify - should find valid candidate
        assert result is not None

    @patch("src.services.pricing.get_supabase_client")
    @patch("src.services.pricing.track_database_query")
    def test_get_model_pricing_handles_missing_pricing_relationship(self, mock_track, mock_get_client):
        """Test handling when model exists but has no pricing relationship"""
        # Setup
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock()

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "test-model",
            "model_pricing": None  # No pricing relationship
        }])

        mock_limit = Mock()
        mock_limit.limit.return_value = mock_execute

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_model_name_eq = Mock()
        mock_model_name_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_model_name_eq

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = get_model_pricing_from_db(["test-model"])

        # Verify - should return None when no pricing
        assert result is None
