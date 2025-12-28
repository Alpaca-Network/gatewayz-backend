"""
Comprehensive tests for Pricing Lookup service
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock

from src.services.pricing_lookup import (
    enrich_model_with_pricing,
    get_model_pricing,
    load_manual_pricing,
    refresh_pricing_cache,
)


class TestPricingLookup:
    """Test Pricing Lookup service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.pricing_lookup
        assert src.services.pricing_lookup is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import pricing_lookup
        assert hasattr(pricing_lookup, '__name__')


class TestEnrichModelWithPricing:
    """Test enrich_model_with_pricing function"""

    def test_enriches_model_with_zero_pricing(self):
        """Models with zero pricing should be enriched with manual pricing"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once_with("test-gateway", "test-model")
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}
            assert result["pricing_source"] == "manual"

    def test_skips_enrichment_for_non_zero_pricing(self):
        """Models with non-zero pricing should not be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0.001", "completion": "0.002"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}
            assert "pricing_source" not in result

    def test_handles_scientific_notation_pricing(self):
        """Models with scientific notation pricing should not be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "1e-6", "completion": "2e-6"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "1e-6", "completion": "2e-6"}

    def test_handles_float_zero_pricing(self):
        """Models with float 0.0 pricing should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": 0.0, "completion": 0.0},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_string_zero_variations(self):
        """Models with various zero string formats should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0.00", "completion": "0.000"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_no_pricing_field(self):
        """Models without pricing field should be enriched"""
        model_data = {
            "id": "test-model",
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_empty_pricing(self):
        """Models with empty pricing dict should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()

    def test_handles_none_pricing_values(self):
        """Models with None pricing values should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": None, "completion": None},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()

    def test_handles_empty_string_pricing(self):
        """Models with empty string pricing values should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "", "completion": ""},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()

    def test_handles_missing_model_id(self):
        """Models without id should be returned unchanged"""
        model_data = {
            "pricing": {"prompt": "0", "completion": "0"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result == model_data

    def test_handles_no_manual_pricing_available(self):
        """Models without manual pricing should keep original pricing"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = None

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0", "completion": "0"}
            assert "pricing_source" not in result

    def test_handles_mixed_zero_and_nonzero_pricing(self):
        """Models with some non-zero values should not be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0.001", "image": "0"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "0", "completion": "0.001", "image": "0"}

    def test_handles_invalid_pricing_values(self):
        """Models with invalid pricing values should be treated as zero"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "invalid", "completion": "not-a-number"},
        }

        with patch('src.services.pricing_lookup.get_model_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            # Invalid values should be treated as "not non-zero" (i.e., zero)
            mock_get_pricing.assert_called_once()


class TestGetModelPricing:
    """Test get_model_pricing function"""

    def test_returns_pricing_for_existing_model(self):
        """Should return pricing for model in manual pricing data"""
        mock_pricing_data = {
            "test-gateway": {
                "test-model": {"prompt": "0.001", "completion": "0.002"}
            }
        }

        with patch('src.services.pricing_lookup.load_manual_pricing') as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_returns_none_for_missing_gateway(self):
        """Should return None for unknown gateway"""
        mock_pricing_data = {
            "other-gateway": {
                "test-model": {"prompt": "0.001", "completion": "0.002"}
            }
        }

        with patch('src.services.pricing_lookup.load_manual_pricing') as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None

    def test_returns_none_for_missing_model(self):
        """Should return None for unknown model"""
        mock_pricing_data = {
            "test-gateway": {
                "other-model": {"prompt": "0.001", "completion": "0.002"}
            }
        }

        with patch('src.services.pricing_lookup.load_manual_pricing') as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None

    def test_case_insensitive_gateway_match(self):
        """Should match gateway case-insensitively"""
        mock_pricing_data = {
            "test-gateway": {
                "test-model": {"prompt": "0.001", "completion": "0.002"}
            }
        }

        with patch('src.services.pricing_lookup.load_manual_pricing') as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("TEST-GATEWAY", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_case_insensitive_model_match(self):
        """Should match model case-insensitively"""
        mock_pricing_data = {
            "test-gateway": {
                "Test-Model": {"prompt": "0.001", "completion": "0.002"}
            }
        }

        with patch('src.services.pricing_lookup.load_manual_pricing') as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_returns_none_for_empty_pricing_data(self):
        """Should return None when no pricing data loaded"""
        with patch('src.services.pricing_lookup.load_manual_pricing') as mock_load:
            mock_load.return_value = {}

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None
