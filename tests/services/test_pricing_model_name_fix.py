"""
Tests for pricing.py model_name migration fix

Tests that pricing queries correctly use model_name instead of dropped model_id column
"""

from unittest.mock import Mock, patch


class TestPricingModelNameFix:
    """Test that pricing.py uses model_name instead of model_id"""

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.services.prometheus_metrics.track_database_query")
    def test_get_pricing_from_database_uses_model_name(self, mock_track, mock_get_client):
        """Test that _get_pricing_from_database queries by model_name, not model_id"""
        # Import here to avoid patching issues
        from src.services.pricing import _get_pricing_from_database

        # Setup
        model_name = "gpt-4"
        candidate_ids = {model_name, "openai/gpt-4"}

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock track_database_query as context manager
        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock(return_value=False)

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
        result = _get_pricing_from_database(model_name, candidate_ids)

        # Verify
        assert result is not None
        assert result["prompt"] == 0.00003  # per-token price
        assert result["completion"] == 0.00006  # per-token price
        assert result["found"] is True
        assert result["source"] == "database"

        # Verify query uses model_name
        select_call = mock_table.select.call_args[0][0]
        assert "model_name" in select_call
        eq_calls = mock_select.eq.call_args_list
        assert any(call[0][0] == "model_name" for call in eq_calls)

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.services.prometheus_metrics.track_database_query")
    def test_get_pricing_returns_none_if_no_match(self, mock_track, mock_get_client):
        """Test that None is returned when no candidates match"""
        from src.services.pricing import _get_pricing_from_database

        # Setup
        model_id = "nonexistent-model"
        candidate_ids = {model_id}

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock(return_value=False)

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
        result = _get_pricing_from_database(model_id, candidate_ids)

        # Verify
        assert result is None

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.services.prometheus_metrics.track_database_query")
    def test_get_pricing_skips_empty_candidates(self, mock_track, mock_get_client):
        """Test that empty candidate strings are skipped"""
        from src.services.pricing import _get_pricing_from_database

        # Setup - include empty strings and None values
        model_id = "gpt-4"
        candidate_ids = {"", "gpt-4"}  # Sets don't include None

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock(return_value=False)

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
        result = _get_pricing_from_database(model_id, candidate_ids)

        # Verify - should find valid candidate
        assert result is not None
        assert result["prompt"] == 0.00003

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.services.prometheus_metrics.track_database_query")
    def test_get_pricing_handles_missing_pricing_relationship(self, mock_track, mock_get_client):
        """Test handling when model exists but has no pricing relationship"""
        from src.services.pricing import _get_pricing_from_database

        # Setup
        model_id = "test-model"
        candidate_ids = {model_id}

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock(return_value=False)

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
        result = _get_pricing_from_database(model_id, candidate_ids)

        # Verify - should return None when no pricing (will try fallback query too)
        # The function continues to next candidate if pricing is None
        assert result is None

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.services.prometheus_metrics.track_database_query")
    def test_get_pricing_handles_list_pricing_response(self, mock_track, mock_get_client):
        """Test handling when model_pricing is returned as a list (PostgREST one-to-many)"""
        from src.services.pricing import _get_pricing_from_database

        # Setup
        model_id = "test-model"
        candidate_ids = {model_id}

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock(return_value=False)

        mock_execute = Mock()
        mock_execute.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "test-model",
            "model_pricing": [{  # List format from PostgREST
                "price_per_input_token": 0.00005,
                "price_per_output_token": 0.0001,
            }]
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
        result = _get_pricing_from_database(model_id, candidate_ids)

        # Verify - should handle list and extract first element
        assert result is not None
        assert result["prompt"] == 0.00005
        assert result["completion"] == 0.0001

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.services.prometheus_metrics.track_database_query")
    def test_fallback_query_uses_model_name_not_model_id(self, mock_track, mock_get_client):
        """Test that the fallback query (by provider_model_id) also uses model_name in select"""
        from src.services.pricing import _get_pricing_from_database

        # Setup
        model_id = "gpt-4"
        candidate_ids = {model_id}

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock(return_value=False)

        # First query by model_name returns empty
        mock_execute_empty = Mock()
        mock_execute_empty.execute.return_value = Mock(data=[])

        # Second query by provider_model_id returns pricing
        mock_execute_found = Mock()
        mock_execute_found.execute.return_value = Mock(data=[{
            "id": 1,
            "model_name": "openai/gpt-4",
            "model_pricing": {
                "price_per_input_token": 0.00003,
                "price_per_output_token": 0.00006,
            }
        }])

        call_count = [0]

        def get_limit_result(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_execute_empty
            return mock_execute_found

        mock_limit = Mock()
        mock_limit.limit = get_limit_result

        mock_is_active = Mock()
        mock_is_active.eq.return_value = mock_limit

        mock_eq = Mock()
        mock_eq.eq.return_value = mock_is_active

        mock_select = Mock()
        mock_select.eq.return_value = mock_eq

        mock_table = Mock()
        mock_table.select.return_value = mock_select

        mock_client.table.return_value = mock_table

        # Execute
        result = _get_pricing_from_database(model_id, candidate_ids)

        # Verify fallback query was called and returned pricing
        assert result is not None
        assert result["prompt"] == 0.00003

        # Verify select was called with model_name (not model_id)
        for call in mock_table.select.call_args_list:
            select_str = call[0][0]
            # Should contain model_name but not standalone model_id (model_id in model_pricing FK is ok)
            assert "model_name" in select_str
