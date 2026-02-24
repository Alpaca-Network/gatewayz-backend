"""
Comprehensive tests for Pricing Lookup service
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.services.pricing_lookup import (
    enrich_model_with_pricing,
    get_model_pricing,
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

        assert hasattr(pricing_lookup, "__name__")


class TestEnrichModelWithPricing:
    """Test enrich_model_with_pricing function"""

    def test_enriches_model_with_zero_pricing(self):
        """Models with zero pricing should be enriched with manual pricing"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
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

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
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

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "1e-6", "completion": "2e-6"}

    def test_handles_float_zero_pricing(self):
        """Models with float 0.0 pricing should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": 0.0, "completion": 0.0},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
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

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_no_pricing_field(self):
        """Models without pricing field should be enriched"""
        model_data = {
            "id": "test-model",
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
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

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_none_pricing_values(self):
        """Models with None pricing values should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": None, "completion": None},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_empty_string_pricing(self):
        """Models with empty string pricing values should be enriched"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "", "completion": ""},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_missing_model_id(self):
        """Models without id should be returned unchanged"""
        model_data = {
            "pricing": {"prompt": "0", "completion": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result == model_data

    def test_handles_no_manual_pricing_available(self):
        """Models without manual pricing should keep original pricing"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
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

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "0", "completion": "0.001", "image": "0"}

    def test_handles_invalid_pricing_values(self):
        """Models with invalid pricing values should be treated as zero"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "invalid", "completion": "not-a-number"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            # Invalid values should be treated as "not non-zero" (i.e., zero)
            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}
            assert result["pricing_source"] == "manual"


class TestGetModelPricing:
    """Test get_model_pricing function"""

    def test_returns_pricing_for_existing_model(self):
        """Should return pricing for model in manual pricing data"""
        mock_pricing_data = {
            "test-gateway": {"test-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_returns_none_for_missing_gateway(self):
        """Should return None for unknown gateway"""
        mock_pricing_data = {
            "other-gateway": {"test-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None

    def test_returns_none_for_missing_model(self):
        """Should return None for unknown model"""
        mock_pricing_data = {
            "test-gateway": {"other-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None

    def test_case_insensitive_gateway_match(self):
        """Should match gateway case-insensitively"""
        mock_pricing_data = {
            "test-gateway": {"test-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("TEST-GATEWAY", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_case_insensitive_model_match(self):
        """Should match model case-insensitively"""
        mock_pricing_data = {
            "test-gateway": {"Test-Model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_returns_none_for_empty_pricing_data(self):
        """Should return None when no pricing data loaded"""
        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = {}

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None


class TestGatewayProviders:
    """Test GATEWAY_PROVIDERS constant"""

    def test_gateway_providers_contains_expected_providers(self):
        """Test that GATEWAY_PROVIDERS contains all expected gateway providers

        Gateway providers route to underlying providers (OpenAI, Anthropic, etc.)
        and need cross-reference pricing from OpenRouter. Models without valid
        pricing will be filtered out to avoid appearing as "free".
        """
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        # All gateway providers that don't expose reliable pricing directly
        assert "aihubmix" in GATEWAY_PROVIDERS
        assert "akash" in GATEWAY_PROVIDERS
        assert "alibaba-cloud" in GATEWAY_PROVIDERS
        assert "anannas" in GATEWAY_PROVIDERS
        assert "clarifai" in GATEWAY_PROVIDERS
        assert "cloudflare-workers-ai" in GATEWAY_PROVIDERS
        assert "deepinfra" in GATEWAY_PROVIDERS
        assert "featherless" in GATEWAY_PROVIDERS
        assert "fireworks" in GATEWAY_PROVIDERS
        assert "groq" in GATEWAY_PROVIDERS
        assert "helicone" in GATEWAY_PROVIDERS
        assert "onerouter" in GATEWAY_PROVIDERS
        assert "together" in GATEWAY_PROVIDERS
        assert "vercel-ai-gateway" in GATEWAY_PROVIDERS

    def test_gateway_providers_is_set(self):
        """Test that GATEWAY_PROVIDERS is a set for O(1) lookup"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert isinstance(GATEWAY_PROVIDERS, set)


class TestCrossReferencePricing:
    """Test cross-reference pricing functionality"""

    def test_cross_reference_pricing_returns_none_when_catalog_building(self):
        """Test that cross-reference returns None when catalog is building"""
        from src.services.pricing_lookup import _get_cross_reference_pricing

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=True):
            result = _get_cross_reference_pricing("gpt-4o")
            assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_returns_none_when_no_openrouter_models(self):
        """Test that cross-reference returns None when OpenRouter cache is empty"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=None):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_finds_matching_model(self):
        """Test that cross-reference finds pricing for matching model"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": "0.000005",
                    "completion": "0.000015",
                    "request": "0",
                    "image": "0",
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is not None
                assert result["prompt"] == "0.000005"
                assert result["completion"] == "0.000015"

    @pytest.mark.integration
    def test_cross_reference_pricing_handles_provider_prefix(self):
        """Test that cross-reference works with provider/model format"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "anthropic/claude-3-opus",
                "pricing": {
                    "prompt": "0.00001",
                    "completion": "0.00003",
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                result = _get_cross_reference_pricing("anthropic/claude-3-opus")
                assert result is not None
                assert result["prompt"] == "0.00001"

    @pytest.mark.integration
    def test_cross_reference_pricing_returns_none_for_unknown_model(self):
        """Test that cross-reference returns None for models not in OpenRouter"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {"prompt": "0.000005", "completion": "0.000015"},
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                result = _get_cross_reference_pricing("unknown-model-xyz")
                assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_handles_versioned_model_ids(self):
        """Test that cross-reference matches versioned OpenRouter model IDs"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        # OpenRouter uses date-versioned IDs like "anthropic/claude-3-opus-20240229"
        mock_openrouter_models = [
            {
                "id": "anthropic/claude-3-opus-20240229",
                "pricing": {
                    "prompt": "0.00001",
                    "completion": "0.00003",
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                # Should match "claude-3-opus" to "claude-3-opus-20240229"
                result = _get_cross_reference_pricing("claude-3-opus")
                assert result is not None
                assert result["prompt"] == "0.00001"

    @pytest.mark.integration
    def test_cross_reference_pricing_does_not_match_different_model_variants(self):
        """Test that cross-reference does NOT match different model variants incorrectly"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        # gpt-4o-mini should NOT match gpt-4o (different model entirely)
        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": "0.000005",
                    "completion": "0.000015",
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                # gpt-4o-mini should NOT match openai/gpt-4o
                result = _get_cross_reference_pricing("gpt-4o-mini")
                assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_matches_correct_model_with_variants(self):
        """Test that cross-reference matches the correct model when variants exist"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        # Both gpt-4o and gpt-4o-mini should match their correct models
        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": "0.000005",
                    "completion": "0.000015",
                },
            },
            {
                "id": "openai/gpt-4o-mini",
                "pricing": {
                    "prompt": "0.0000001",
                    "completion": "0.0000004",
                },
            },
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                # gpt-4o should get gpt-4o pricing
                result_4o = _get_cross_reference_pricing("gpt-4o")
                assert result_4o is not None
                assert result_4o["prompt"] == "0.000005"

                # gpt-4o-mini should get gpt-4o-mini pricing
                result_mini = _get_cross_reference_pricing("gpt-4o-mini")
                assert result_mini is not None
                assert result_mini["prompt"] == "0.0000001"


class TestEnrichModelWithPricingGatewayProviders:
    """Test enrich_model_with_pricing for gateway providers"""

    def test_gateway_provider_uses_cross_reference_pricing(self):
        """Gateway provider should use cross-reference pricing when no manual pricing"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        mock_cross_ref = {
            "prompt": "0.000005",
            "completion": "0.000015",
            "request": "0",
            "image": "0",
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=mock_cross_ref,
            ):
                # Test with aihubmix as a gateway provider
                result = enrich_model_with_pricing(model_data, "aihubmix")
                assert result is not None
                assert result["pricing"] == mock_cross_ref
                assert result["pricing_source"] == "cross-reference"

    def test_gateway_provider_returns_none_when_no_pricing_and_not_building(self):
        """Gateway provider should return None when no pricing found and not building catalog"""
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "helicone")
                    assert result is None

    def test_gateway_provider_keeps_model_during_catalog_build(self):
        """Gateway provider should keep model with zero pricing during catalog build"""
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=True):
                    result = enrich_model_with_pricing(model_data, "helicone")
                    # During catalog build, should return model with zero pricing
                    assert result is not None
                    assert result["pricing"]["prompt"] == "0"

    def test_gateway_provider_prefers_manual_pricing(self):
        """Gateway provider should prefer manual pricing over cross-reference"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        manual_pricing = {"prompt": "0.00001", "completion": "0.00002"}
        cross_ref_pricing = {
            "prompt": "0.000005",
            "completion": "0.000015",
            "request": "0",
            "image": "0",
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=manual_pricing):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=cross_ref_pricing,
            ) as mock_cross:
                result = enrich_model_with_pricing(model_data, "anannas")
                assert result is not None
                assert result["pricing"] == manual_pricing
                assert result["pricing_source"] == "manual"
                # Cross-reference should not be called when manual pricing exists
                mock_cross.assert_not_called()

    def test_non_gateway_provider_returns_model_without_pricing(self):
        """Non-gateway provider should return model unchanged when no pricing found"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            # Use openrouter as non-gateway provider (primary source, not a gateway)
            result = enrich_model_with_pricing(model_data, "openrouter")
            assert result is not None
            assert result == model_data
            assert "pricing_source" not in result

    def test_gateway_provider_with_existing_non_zero_pricing(self):
        """Gateway provider with non-zero pricing should not be enriched"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0.001", "completion": "0.002"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_manual:
            with patch("src.services.pricing_lookup._get_cross_reference_pricing") as mock_cross:
                # Test with alibaba-cloud as a gateway provider
                result = enrich_model_with_pricing(model_data, "alibaba-cloud")
                assert result is not None
                assert result["pricing"]["prompt"] == "0.001"
                mock_manual.assert_not_called()
                mock_cross.assert_not_called()

    def test_gateway_provider_filters_on_exception(self):
        """Gateway provider should return None on exception to prevent appearing free"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch(
            "src.services.pricing_lookup.get_model_pricing", side_effect=Exception("Test error")
        ):
            # Test with aihubmix as a gateway provider
            result = enrich_model_with_pricing(model_data, "aihubmix")
            # Gateway provider should be filtered out on error
            assert result is None

    def test_non_gateway_provider_returns_model_on_exception(self):
        """Non-gateway provider should return model data on exception"""
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch(
            "src.services.pricing_lookup.get_model_pricing", side_effect=Exception("Test error")
        ):
            # Use openrouter as non-gateway provider (primary source, not a gateway)
            result = enrich_model_with_pricing(model_data, "openrouter")
            # Non-gateway provider should return model data even on error
            assert result is not None
            assert result["id"] == "test-model"


class TestGatewayProviderZeroPricingFiltering:
    """Test that gateway providers filter out models with zero cross-reference pricing"""

    def test_gateway_provider_filters_zero_cross_reference_pricing(self):
        """Gateway provider should filter out models with zero cross-reference pricing"""
        model_data = {
            "id": "some-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        # Cross-reference returns zero pricing (model is free on OpenRouter)
        zero_cross_ref = {"prompt": "0", "completion": "0", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=zero_cross_ref,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    # Test with aihubmix as a gateway provider
                    result = enrich_model_with_pricing(model_data, "aihubmix")
                    # Should be filtered out because cross-reference pricing is zero
                    assert result is None

    def test_gateway_provider_accepts_nonzero_cross_reference_pricing(self):
        """Gateway provider should accept models with non-zero cross-reference pricing"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        # Cross-reference returns non-zero pricing
        nonzero_cross_ref = {
            "prompt": "0.000005",
            "completion": "0.000015",
            "request": "0",
            "image": "0",
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=nonzero_cross_ref,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "helicone")
                    assert result is not None
                    assert result["pricing"] == nonzero_cross_ref
                    assert result["pricing_source"] == "cross-reference"

    def test_gateway_provider_filters_zero_string_variants(self):
        """Gateway provider should filter zero pricing in various formats"""
        model_data = {
            "id": "some-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        # Various zero formats
        zero_variants = {"prompt": "0.0", "completion": "0.00", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=zero_variants,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "anannas")
                    # Should be filtered out because all pricing values are zero
                    assert result is None

    def test_gateway_provider_accepts_partial_nonzero_pricing(self):
        """Gateway provider should accept models with at least one non-zero prompt/completion"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        # Only completion is non-zero
        partial_pricing = {"prompt": "0", "completion": "0.000015", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=partial_pricing,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "vercel-ai-gateway")
                    assert result is not None
                    assert result["pricing"]["completion"] == "0.000015"

    def test_clarifai_in_gateway_providers(self):
        """Test that clarifai is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "clarifai" in GATEWAY_PROVIDERS

    def test_onerouter_in_gateway_providers(self):
        """Test that onerouter is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "onerouter" in GATEWAY_PROVIDERS

    def test_deepinfra_in_gateway_providers(self):
        """Test that deepinfra is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "deepinfra" in GATEWAY_PROVIDERS

    def test_featherless_in_gateway_providers(self):
        """Test that featherless is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "featherless" in GATEWAY_PROVIDERS

    def test_deepinfra_filters_models_without_pricing(self):
        """Test that deepinfra filters out models without valid pricing"""
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "deepinfra")
                    # Should be filtered out because no pricing found
                    assert result is None

    def test_featherless_filters_models_without_pricing(self):
        """Test that featherless filters out models without valid pricing"""
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "featherless")
                    # Should be filtered out because no pricing found
                    assert result is None

    def test_deepinfra_accepts_models_with_manual_pricing(self):
        """Test that deepinfra accepts models with manual pricing"""
        model_data = {
            "id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        manual_pricing = {"prompt": "0.055", "completion": "0.055", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=manual_pricing):
            result = enrich_model_with_pricing(model_data, "deepinfra")
            assert result is not None
            assert result["pricing"] == manual_pricing
            assert result["pricing_source"] == "manual"

    def test_featherless_accepts_models_with_manual_pricing(self):
        """Test that featherless accepts models with manual pricing"""
        model_data = {
            "id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        manual_pricing = {"prompt": "0.05", "completion": "0.05", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=manual_pricing):
            result = enrich_model_with_pricing(model_data, "featherless")
            assert result is not None
            assert result["pricing"] == manual_pricing
            assert result["pricing_source"] == "manual"

    def test_groq_in_gateway_providers(self):
        """Test that groq is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "groq" in GATEWAY_PROVIDERS

    def test_fireworks_in_gateway_providers(self):
        """Test that fireworks is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "fireworks" in GATEWAY_PROVIDERS

    def test_together_in_gateway_providers(self):
        """Test that together is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "together" in GATEWAY_PROVIDERS

    def test_akash_in_gateway_providers(self):
        """Test that akash is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "akash" in GATEWAY_PROVIDERS

    def test_cloudflare_workers_ai_in_gateway_providers(self):
        """Test that cloudflare-workers-ai is now in GATEWAY_PROVIDERS"""
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert "cloudflare-workers-ai" in GATEWAY_PROVIDERS

    def test_groq_filters_models_without_pricing(self):
        """Test that groq filters out models without valid pricing"""
        model_data = {
            "id": "groq/unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "groq")
                    assert result is None

    def test_fireworks_filters_models_without_pricing(self):
        """Test that fireworks filters out models without valid pricing"""
        model_data = {
            "id": "accounts/fireworks/models/unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "fireworks")
                    assert result is None

    def test_together_filters_models_without_pricing(self):
        """Test that together filters out models without valid pricing"""
        model_data = {
            "id": "unknown/unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=None,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "together")
                    assert result is None

    def test_groq_accepts_models_with_manual_pricing(self):
        """Test that groq accepts models with manual pricing"""
        model_data = {
            "id": "groq/llama-3.3-70b-versatile",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        manual_pricing = {"prompt": "0.59", "completion": "0.79", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=manual_pricing):
            result = enrich_model_with_pricing(model_data, "groq")
            assert result is not None
            assert result["pricing"] == manual_pricing
            assert result["pricing_source"] == "manual"

    def test_fireworks_accepts_models_with_manual_pricing(self):
        """Test that fireworks accepts models with manual pricing"""
        model_data = {
            "id": "accounts/fireworks/models/deepseek-v3",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        manual_pricing = {"prompt": "0.56", "completion": "1.68", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=manual_pricing):
            result = enrich_model_with_pricing(model_data, "fireworks")
            assert result is not None
            assert result["pricing"] == manual_pricing
            assert result["pricing_source"] == "manual"


class TestCrossReferencePricingNullHandling:
    """Test null/None value handling in cross-reference pricing"""

    @pytest.mark.integration
    def test_cross_reference_handles_none_pricing_values(self):
        """Cross-reference should handle None pricing values correctly"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        # OpenRouter model with None values in pricing
        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": None,  # Explicitly None
                    "completion": "0.000015",
                    "request": None,
                    "image": None,
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is not None
                # None values should be converted to "0"
                assert result["prompt"] == "0"
                assert result["completion"] == "0.000015"
                assert result["request"] == "0"
                assert result["image"] == "0"

    @pytest.mark.integration
    def test_cross_reference_handles_empty_string_pricing(self):
        """Cross-reference should handle empty string pricing values correctly"""
        pytest.importorskip("fastapi")  # Skip if fastapi not available
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": "",  # Empty string
                    "completion": "0.000015",
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(
                models,
                "get_cached_models",
                return_value=mock_openrouter_models,
            ):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is not None
                # Empty string should be converted to "0"
                assert result["prompt"] == "0"
                assert result["completion"] == "0.000015"


class TestIsFreeField:
    """Test is_free field is set correctly for models"""

    def test_non_openrouter_gateway_sets_is_free_false(self):
        """Non-OpenRouter gateways should have is_free set to False"""
        model_data = {
            "id": "groq/llama-3.3-70b-versatile",
            "pricing": {"prompt": "0.59", "completion": "0.79"},
        }

        result = enrich_model_with_pricing(model_data, "groq")

        assert result is not None
        assert result["is_free"] is False

    def test_deepinfra_sets_is_free_false(self):
        """DeepInfra models should have is_free set to False"""
        model_data = {
            "id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
            "pricing": {"prompt": "0.06", "completion": "0.06"},
        }

        result = enrich_model_with_pricing(model_data, "deepinfra")

        assert result is not None
        assert result["is_free"] is False

    def test_featherless_sets_is_free_false(self):
        """Featherless models should have is_free set to False"""
        model_data = {
            "id": "meta-llama/Llama-3.1-8B-Instruct",
            "pricing": {"prompt": "0.10", "completion": "0.10"},
        }

        result = enrich_model_with_pricing(model_data, "featherless")

        assert result is not None
        assert result["is_free"] is False

    def test_together_sets_is_free_false(self):
        """Together models should have is_free set to False"""
        model_data = {
            "id": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "pricing": {"prompt": "0.88", "completion": "0.88"},
        }

        result = enrich_model_with_pricing(model_data, "together")

        assert result is not None
        assert result["is_free"] is False

    def test_fireworks_sets_is_free_false(self):
        """Fireworks models should have is_free set to False"""
        model_data = {
            "id": "accounts/fireworks/models/deepseek-v3",
            "pricing": {"prompt": "0.56", "completion": "1.68"},
        }

        with patch(
            "src.services.pricing_lookup.get_model_pricing",
            return_value={"prompt": "0.56", "completion": "1.68"},
        ):
            result = enrich_model_with_pricing(model_data, "fireworks")

        assert result is not None
        assert result["is_free"] is False

    def test_openrouter_does_not_set_is_free(self):
        """OpenRouter models should not have is_free set by enrich_model_with_pricing
        (it's set by fetch_models_from_openrouter based on :free suffix)"""
        model_data = {
            "id": "openai/gpt-4o",
            "pricing": {"prompt": "2.50", "completion": "10.00"},
        }

        result = enrich_model_with_pricing(model_data, "openrouter")

        assert result is not None
        # OpenRouter models don't get is_free set by enrich_model_with_pricing
        # because is_free is set by fetch_models_from_openrouter based on :free suffix
        assert "is_free" not in result

    def test_aihubmix_sets_is_free_false(self):
        """AiHubMix gateway models should have is_free set to False"""
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "2.50", "completion": "10.00"},
        }

        result = enrich_model_with_pricing(model_data, "aihubmix")

        assert result is not None
        assert result["is_free"] is False

    def test_helicone_sets_is_free_false(self):
        """Helicone gateway models should have is_free set to False"""
        model_data = {
            "id": "claude-3-opus",
            "pricing": {"prompt": "15.00", "completion": "75.00"},
        }

        result = enrich_model_with_pricing(model_data, "helicone")

        assert result is not None
        assert result["is_free"] is False
