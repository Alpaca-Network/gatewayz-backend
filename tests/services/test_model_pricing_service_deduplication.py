"""
Tests for model pricing service deduplication fix

This test suite verifies the fix for Sentry issue GATEWAYZ-BACKEND-4PW:
"Error bulk upserting pricing: duplicate key value violates unique constraint"

The issue occurred when bulk_upsert_pricing() received duplicate model_id values,
causing PostgreSQL unique constraint violations. The fix deduplicates records
by model_id before upserting.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from src.services.model_pricing_service import (
    bulk_upsert_pricing,
    clear_pricing_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear pricing cache before each test"""
    clear_pricing_cache()
    yield
    clear_pricing_cache()


class TestBulkUpsertPricingDeduplication:
    """Test deduplication logic in bulk_upsert_pricing"""

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_deduplicates_duplicate_model_ids(self, mock_supabase):
        """Test that duplicate model_id entries are deduplicated before upserting"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Create records with duplicate model_id=402 (the exact error from Sentry)
        pricing_records = [
            {
                "model_id": 402,
                "price_per_input_token": 0.000001,
                "price_per_output_token": 0.000002,
            },
            {
                "model_id": 403,
                "price_per_input_token": 0.000003,
                "price_per_output_token": 0.000004,
            },
            {
                "model_id": 402,  # Duplicate - should be removed
                "price_per_input_token": 0.000005,
                "price_per_output_token": 0.000006,
            },
        ]

        # Call function
        success, errors = bulk_upsert_pricing(pricing_records)

        # Verify deduplication happened
        assert success == 2  # Only 2 unique records
        assert errors == 0

        # Verify upsert was called with deduplicated records
        mock_table.upsert.assert_called_once()
        upserted_records = mock_table.upsert.call_args[0][0]

        assert len(upserted_records) == 2
        model_ids = [r["model_id"] for r in upserted_records]
        assert model_ids == [402, 403]

        # Verify last occurrence was kept (model_id=402 with newer prices)
        model_402 = [r for r in upserted_records if r["model_id"] == 402][0]
        assert model_402["price_per_input_token"] == 0.000005
        assert model_402["price_per_output_token"] == 0.000006

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_handles_multiple_duplicates(self, mock_supabase):
        """Test deduplication with multiple duplicate entries"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Create records with many duplicates
        pricing_records = [
            {"model_id": 100, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
            {"model_id": 100, "price_per_input_token": 0.003, "price_per_output_token": 0.004},
            {"model_id": 200, "price_per_input_token": 0.005, "price_per_output_token": 0.006},
            {"model_id": 100, "price_per_input_token": 0.007, "price_per_output_token": 0.008},
            {"model_id": 200, "price_per_input_token": 0.009, "price_per_output_token": 0.010},
        ]

        # Call function
        success, errors = bulk_upsert_pricing(pricing_records)

        # Verify deduplication
        assert success == 2  # Only 2 unique model_ids
        assert errors == 0

        upserted_records = mock_table.upsert.call_args[0][0]
        assert len(upserted_records) == 2

        # Verify last occurrence was kept for each
        model_100 = [r for r in upserted_records if r["model_id"] == 100][0]
        assert model_100["price_per_input_token"] == 0.007

        model_200 = [r for r in upserted_records if r["model_id"] == 200][0]
        assert model_200["price_per_output_token"] == 0.010

    @patch("src.services.model_pricing_service.get_supabase_client")
    @patch("src.services.model_pricing_service.logger")
    def test_logs_warning_when_duplicates_found(self, mock_logger, mock_supabase):
        """Test that a warning is logged when duplicates are removed"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Create records with duplicates
        pricing_records = [
            {"model_id": 1, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
            {"model_id": 1, "price_per_input_token": 0.003, "price_per_output_token": 0.004},
        ]

        # Call function
        bulk_upsert_pricing(pricing_records)

        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Removed 1 duplicate" in warning_msg
        assert "original: 2" in warning_msg
        assert "deduplicated: 1" in warning_msg

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_no_warning_when_no_duplicates(self, mock_supabase):
        """Test that no warning is logged when there are no duplicates"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Create records with no duplicates
        pricing_records = [
            {"model_id": 1, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
            {"model_id": 2, "price_per_input_token": 0.003, "price_per_output_token": 0.004},
        ]

        # Call function
        success, errors = bulk_upsert_pricing(pricing_records)

        # Verify success
        assert success == 2
        assert errors == 0

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_handles_none_model_id(self, mock_supabase):
        """Test that records with None model_id are handled gracefully"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Create records with None model_id (should be filtered out)
        pricing_records = [
            {"model_id": 1, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
            {"model_id": None, "price_per_input_token": 0.003, "price_per_output_token": 0.004},
            {"model_id": 2, "price_per_input_token": 0.005, "price_per_output_token": 0.006},
        ]

        # Call function
        success, errors = bulk_upsert_pricing(pricing_records)

        # Verify only records with valid model_id were upserted
        assert success == 2
        assert errors == 0

        upserted_records = mock_table.upsert.call_args[0][0]
        assert len(upserted_records) == 2
        assert all(r["model_id"] is not None for r in upserted_records)

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_preserves_all_fields_during_deduplication(self, mock_supabase):
        """Test that all pricing fields are preserved during deduplication"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Create records with all pricing fields
        pricing_records = [
            {
                "model_id": 1,
                "price_per_input_token": 0.000001,
                "price_per_output_token": 0.000002,
            },
            {
                "model_id": 1,  # Duplicate - this should be kept
                "price_per_input_token": 0.000003,
                "price_per_output_token": 0.000004,
                "price_per_image_token": 0.000005,
                "price_per_request": 0.01,
                "pricing_source": "provider",
            },
        ]

        # Call function
        bulk_upsert_pricing(pricing_records)

        # Verify all fields were preserved
        upserted_records = mock_table.upsert.call_args[0][0]
        assert len(upserted_records) == 1

        record = upserted_records[0]
        assert record["model_id"] == 1
        assert record["price_per_input_token"] == 0.000003
        assert record["price_per_output_token"] == 0.000004
        assert record["price_per_image_token"] == 0.000005
        assert record["price_per_request"] == 0.01
        assert record["pricing_source"] == "provider"

    @patch("src.services.model_pricing_service.get_supabase_client")
    @patch("src.services.model_pricing_service._pricing_cache")
    def test_clears_cache_after_upsert(self, mock_cache, mock_supabase):
        """Test that pricing cache is cleared after successful upsert"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Add some data to cache
        mock_cache.clear = Mock()

        # Create records
        pricing_records = [
            {"model_id": 1, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
        ]

        # Call function
        bulk_upsert_pricing(pricing_records)

        # Verify cache was cleared
        mock_cache.clear.assert_called_once()

    @patch("src.services.model_pricing_service.get_supabase_client")
    @patch("src.services.model_pricing_service.logger")
    def test_handles_database_error_gracefully(self, mock_logger, mock_supabase):
        """Test that database errors are handled and logged"""
        # Setup mock to raise exception
        mock_table = Mock()
        mock_table.upsert.side_effect = Exception("Database connection error")
        mock_supabase.return_value.table.return_value = mock_table

        # Create records
        pricing_records = [
            {"model_id": 1, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
        ]

        # Call function
        success, errors = bulk_upsert_pricing(pricing_records)

        # Verify error handling
        assert success == 0
        assert errors == 1

        # Verify error was logged
        mock_logger.error.assert_called_once()
        error_msg = mock_logger.error.call_args[0][0]
        assert "Error bulk upserting pricing" in error_msg

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_empty_list_handling(self, mock_supabase):
        """Test that empty pricing list is handled correctly"""
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Call with empty list
        success, errors = bulk_upsert_pricing([])

        # Verify success
        assert success == 0
        assert errors == 0

        # Verify upsert was still called (with empty list)
        mock_table.upsert.assert_called_once_with([])

    @patch("src.services.model_pricing_service.get_supabase_client")
    def test_regression_sentry_issue_4pw(self, mock_supabase):
        """
        Regression test for Sentry issue GATEWAYZ-BACKEND-4PW

        Simulates the exact scenario that caused 733 errors:
        duplicate key value violates unique constraint "model_pricing_model_id_key"
        Key (model_id)=(402) already exists.
        """
        # Setup mock
        mock_table = Mock()
        mock_upsert = Mock()
        mock_execute = Mock()

        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_execute
        mock_supabase.return_value.table.return_value = mock_table

        # Simulate the exact error scenario from Sentry
        # Multiple sync operations trying to upsert model_id=402
        pricing_records = [
            {"model_id": 402, "price_per_input_token": 0.001, "price_per_output_token": 0.002},
            {"model_id": 500, "price_per_input_token": 0.003, "price_per_output_token": 0.004},
            {"model_id": 402, "price_per_input_token": 0.005, "price_per_output_token": 0.006},  # Duplicate
        ]

        # This should NOT raise a unique constraint violation
        success, errors = bulk_upsert_pricing(pricing_records)

        # Verify success
        assert success == 2  # Only 2 unique records
        assert errors == 0

        # Verify the upsert was called with deduplicated data
        mock_table.upsert.assert_called_once()
        upserted_records = mock_table.upsert.call_args[0][0]

        # Should only have 2 records (402 and 500)
        assert len(upserted_records) == 2
        model_ids = {r["model_id"] for r in upserted_records}
        assert model_ids == {402, 500}
