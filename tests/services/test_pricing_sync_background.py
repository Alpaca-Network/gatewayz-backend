"""
Tests for pricing sync background service.

Verifies that pricing is correctly extracted from model metadata
after the migration that moved pricing columns to metadata.pricing_raw.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.services.pricing_sync_background import PricingSyncService


class TestExtractAndNormalizePricing:
    """Tests for _extract_and_normalize_pricing method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PricingSyncService.__new__(PricingSyncService)
        self.service.supabase = MagicMock()

    def test_extracts_pricing_from_metadata_pricing_raw(self):
        """Test that pricing is extracted from metadata.pricing_raw (new location)."""
        model = {
            "id": 123,
            "top_provider": "openrouter",
            "metadata": {
                "source_gateway": "openrouter",
                "pricing_raw": {
                    "prompt": "0.055",
                    "completion": "0.040",
                    "image": None,
                    "request": None,
                }
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is not None
        assert result["model_id"] == 123
        assert result["price_per_input_token"] is not None
        assert result["price_per_output_token"] is not None

    def test_returns_none_when_no_pricing_in_metadata(self):
        """Test that None is returned when no pricing exists in metadata."""
        model = {
            "id": 456,
            "top_provider": "openrouter",
            "metadata": {
                "source_gateway": "openrouter",
                # No pricing_raw field
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is None

    def test_returns_none_when_pricing_raw_empty(self):
        """Test that None is returned when pricing_raw has no values."""
        model = {
            "id": 789,
            "top_provider": "deepinfra",
            "metadata": {
                "source_gateway": "deepinfra",
                "pricing_raw": {
                    "prompt": None,
                    "completion": None,
                }
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is None

    def test_handles_missing_metadata_gracefully(self):
        """Test that missing metadata doesn't cause errors."""
        model = {
            "id": 111,
            "top_provider": "openrouter",
            # No metadata field
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is None

    def test_handles_none_metadata_gracefully(self):
        """Test that None metadata doesn't cause errors."""
        model = {
            "id": 222,
            "top_provider": "openrouter",
            "metadata": None
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is None

    def test_handles_none_pricing_raw_gracefully(self):
        """Test that None pricing_raw doesn't cause errors."""
        model = {
            "id": 333,
            "top_provider": "deepinfra",
            "metadata": {
                "pricing_raw": None
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is None

    def test_extracts_image_and_request_pricing(self):
        """Test that image and request pricing are extracted when present."""
        model = {
            "id": 444,
            "top_provider": "fal",
            "metadata": {
                "source_gateway": "fal",
                "pricing_raw": {
                    "prompt": "0.001",
                    "completion": "0.002",
                    "image": "0.01",
                    "request": "0.0001",
                }
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is not None
        assert result["model_id"] == 444
        assert "price_per_image_token" in result
        assert "price_per_request" in result

    def test_uses_source_gateway_from_metadata(self):
        """Test that source_gateway is read from metadata."""
        model = {
            "id": 555,
            "top_provider": "different-provider",  # Should be overridden
            "metadata": {
                "source_gateway": "openrouter",  # Should take precedence
                "pricing_raw": {
                    "prompt": "0.055",
                    "completion": "0.040",
                }
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is not None
        # The pricing format should be determined by openrouter's format

    def test_falls_back_to_top_provider_when_no_source_gateway(self):
        """Test fallback to top_provider when source_gateway not in metadata."""
        model = {
            "id": 666,
            "top_provider": "deepinfra",
            "metadata": {
                # No source_gateway
                "pricing_raw": {
                    "prompt": "0.055",
                    "completion": "0.040",
                }
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is not None

    def test_integration_with_real_model_structure(self):
        """
        Test with model structure matching what model_catalog_sync produces.

        After model sync, models have pricing in metadata.pricing_raw,
        not in top-level pricing columns (which were removed by migration).
        """
        # This is the structure produced by transform_normalized_model_to_db_schema
        model = {
            "id": 777,
            "provider_id": 1,
            "model_id": "llama-3.1-8b",
            "model_name": "Llama 3.1 8B",
            "top_provider": "openrouter",
            "is_active": True,
            "metadata": {
                "synced_at": "2026-01-20T12:00:00Z",
                "source": "openrouter",
                "source_gateway": "openrouter",
                "pricing_raw": {
                    "prompt": "0.055",
                    "completion": "0.040",
                    "image": None,
                    "request": None,
                }
            }
        }

        result = self.service._extract_and_normalize_pricing(model)

        assert result is not None
        assert result["model_id"] == 777
        assert result["price_per_input_token"] > 0
        assert result["price_per_output_token"] > 0


class TestSyncPricingForModels:
    """Tests for sync_pricing_for_models method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PricingSyncService.__new__(PricingSyncService)
        self.service.supabase = MagicMock()

    @patch('src.services.pricing_sync_background.bulk_upsert_pricing')
    def test_syncs_pricing_from_metadata(self, mock_bulk_upsert):
        """Test that pricing is synced correctly from metadata.pricing_raw."""
        # Mock the database response
        self.service.supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": 1,
                    "top_provider": "openrouter",
                    "metadata": {
                        "source_gateway": "openrouter",
                        "pricing_raw": {
                            "prompt": "0.055",
                            "completion": "0.040",
                        }
                    }
                }
            ]
        )
        mock_bulk_upsert.return_value = (1, 0)

        stats = self.service.sync_pricing_for_models([1])

        assert stats["synced"] == 1
        assert stats["failed"] == 0
        assert stats["skipped"] == 0
        mock_bulk_upsert.assert_called_once()

    @patch('src.services.pricing_sync_background.bulk_upsert_pricing')
    def test_skips_models_without_pricing(self, mock_bulk_upsert):
        """Test that models without pricing_raw are skipped."""
        self.service.supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": 2,
                    "top_provider": "openrouter",
                    "metadata": {
                        "source_gateway": "openrouter",
                        # No pricing_raw
                    }
                }
            ]
        )

        stats = self.service.sync_pricing_for_models([2])

        assert stats["synced"] == 0
        assert stats["skipped"] == 1
        mock_bulk_upsert.assert_not_called()
