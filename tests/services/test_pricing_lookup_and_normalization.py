"""
Consolidated Pricing Lookup and Normalization Tests

Merged from:
- test_pricing_lookup.py (enrich_model_with_pricing, gateway providers, cross-reference)
- test_pricing_normalization.py (normalize_to_per_token, pricing dict, provider formats, auto-detect)
"""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from src.services.pricing_lookup import (
    enrich_model_with_pricing,
    get_model_pricing,
)
from src.services.pricing_normalization import (
    PricingFormat,
    auto_detect_format,
    convert_between_formats,
    get_provider_format,
    normalize_price_from_provider,
    normalize_pricing_dict,
    normalize_to_per_token,
    validate_normalized_price,
)

# ===========================================================================
# Pricing Lookup Tests (from test_pricing_lookup.py)
# ===========================================================================


class TestPricingLookup:
    """Test Pricing Lookup service functionality"""

    def test_module_imports(self):
        import src.services.pricing_lookup

        assert src.services.pricing_lookup is not None

    def test_module_has_expected_attributes(self):
        from src.services import pricing_lookup

        assert hasattr(pricing_lookup, "__name__")


class TestEnrichModelWithPricing:
    """Test enrich_model_with_pricing function"""

    def test_enriches_model_with_zero_pricing(self):
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
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "1e-6", "completion": "2e-6"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "1e-6", "completion": "2e-6"}

    def test_handles_float_zero_pricing(self):
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
        model_data = {"id": "test-model"}

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_empty_pricing(self):
        model_data = {"id": "test-model", "pricing": {}}

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}

    def test_handles_none_pricing_values(self):
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
        model_data = {"pricing": {"prompt": "0", "completion": "0"}}

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result == model_data

    def test_handles_no_manual_pricing_available(self):
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
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0.001", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_not_called()
            assert result["pricing"] == {"prompt": "0", "completion": "0.001", "image": "0"}

    def test_handles_invalid_pricing_values(self):
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "invalid", "completion": "not-a-number"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_get_pricing:
            mock_get_pricing.return_value = {"prompt": "0.001", "completion": "0.002"}

            result = enrich_model_with_pricing(model_data, "test-gateway")

            mock_get_pricing.assert_called_once()
            assert result["pricing"] == {"prompt": "0.001", "completion": "0.002"}
            assert result["pricing_source"] == "manual"


class TestGetModelPricingLookup:
    """Test get_model_pricing function from pricing_lookup module"""

    def test_returns_pricing_for_existing_model(self):
        mock_pricing_data = {
            "test-gateway": {"test-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_returns_none_for_missing_gateway(self):
        mock_pricing_data = {
            "other-gateway": {"test-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None

    def test_returns_none_for_missing_model(self):
        mock_pricing_data = {
            "test-gateway": {"other-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None

    def test_case_insensitive_gateway_match(self):
        mock_pricing_data = {
            "test-gateway": {"test-model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("TEST-GATEWAY", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_case_insensitive_model_match(self):
        mock_pricing_data = {
            "test-gateway": {"Test-Model": {"prompt": "0.001", "completion": "0.002"}}
        }

        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = mock_pricing_data

            result = get_model_pricing("test-gateway", "test-model")

            assert result == {"prompt": "0.001", "completion": "0.002"}

    def test_returns_none_for_empty_pricing_data(self):
        with patch("src.services.pricing_lookup.load_manual_pricing") as mock_load:
            mock_load.return_value = {}

            result = get_model_pricing("test-gateway", "test-model")

            assert result is None


class TestGatewayProviders:
    """Test GATEWAY_PROVIDERS constant"""

    def test_gateway_providers_contains_expected_providers(self):
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        expected = [
            "aihubmix",
            "akash",
            "alibaba-cloud",
            "anannas",
            "clarifai",
            "cloudflare-workers-ai",
            "deepinfra",
            "featherless",
            "fireworks",
            "groq",
            "helicone",
            "onerouter",
            "together",
            "vercel-ai-gateway",
        ]
        for provider in expected:
            assert provider in GATEWAY_PROVIDERS

    def test_gateway_providers_is_set(self):
        from src.services.pricing_lookup import GATEWAY_PROVIDERS

        assert isinstance(GATEWAY_PROVIDERS, set)


class TestCrossReferencePricing:
    """Test cross-reference pricing functionality"""

    def test_cross_reference_pricing_returns_none_when_catalog_building(self):
        from src.services.pricing_lookup import _get_cross_reference_pricing

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=True):
            result = _get_cross_reference_pricing("gpt-4o")
            assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_returns_none_when_no_openrouter_models(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=None):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_finds_matching_model(self):
        pytest.importorskip("fastapi")
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
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is not None
                assert result["prompt"] == "0.000005"
                assert result["completion"] == "0.000015"

    @pytest.mark.integration
    def test_cross_reference_pricing_handles_provider_prefix(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "anthropic/claude-3-opus",
                "pricing": {"prompt": "0.00001", "completion": "0.00003"},
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("anthropic/claude-3-opus")
                assert result is not None
                assert result["prompt"] == "0.00001"

    @pytest.mark.integration
    def test_cross_reference_pricing_returns_none_for_unknown_model(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}}
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("unknown-model-xyz")
                assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_handles_versioned_model_ids(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "anthropic/claude-3-opus-20240229",
                "pricing": {"prompt": "0.00001", "completion": "0.00003"},
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("claude-3-opus")
                assert result is not None
                assert result["prompt"] == "0.00001"

    @pytest.mark.integration
    def test_cross_reference_pricing_does_not_match_different_model_variants(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}}
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("gpt-4o-mini")
                assert result is None

    @pytest.mark.integration
    def test_cross_reference_pricing_matches_correct_model_with_variants(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}},
            {
                "id": "openai/gpt-4o-mini",
                "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
            },
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result_4o = _get_cross_reference_pricing("gpt-4o")
                assert result_4o is not None
                assert result_4o["prompt"] == "0.000005"

                result_mini = _get_cross_reference_pricing("gpt-4o-mini")
                assert result_mini is not None
                assert result_mini["prompt"] == "0.0000001"


class TestEnrichModelWithPricingGatewayProviders:
    """Test enrich_model_with_pricing for gateway providers"""

    def test_gateway_provider_uses_cross_reference_pricing(self):
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
                result = enrich_model_with_pricing(model_data, "aihubmix")
                assert result is not None
                assert result["pricing"] == mock_cross_ref
                assert result["pricing_source"] == "cross-reference"

    def test_gateway_provider_returns_none_when_no_pricing_and_not_building(self):
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing", return_value=None
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "helicone")
                    assert result is None

    def test_gateway_provider_keeps_model_during_catalog_build(self):
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing", return_value=None
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=True):
                    result = enrich_model_with_pricing(model_data, "helicone")
                    assert result is not None
                    assert result["pricing"]["prompt"] == "0"

    def test_gateway_provider_prefers_manual_pricing(self):
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
                mock_cross.assert_not_called()

    def test_non_gateway_provider_returns_model_without_pricing(self):
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            result = enrich_model_with_pricing(model_data, "openrouter")
            assert result is not None
            assert result == model_data
            assert "pricing_source" not in result

    def test_gateway_provider_with_existing_non_zero_pricing(self):
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0.001", "completion": "0.002"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing") as mock_manual:
            with patch("src.services.pricing_lookup._get_cross_reference_pricing") as mock_cross:
                result = enrich_model_with_pricing(model_data, "alibaba-cloud")
                assert result is not None
                assert result["pricing"]["prompt"] == "0.001"
                mock_manual.assert_not_called()
                mock_cross.assert_not_called()

    def test_gateway_provider_filters_on_exception(self):
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch(
            "src.services.pricing_lookup.get_model_pricing", side_effect=Exception("Test error")
        ):
            result = enrich_model_with_pricing(model_data, "aihubmix")
            assert result is None

    def test_non_gateway_provider_returns_model_on_exception(self):
        model_data = {
            "id": "test-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch(
            "src.services.pricing_lookup.get_model_pricing", side_effect=Exception("Test error")
        ):
            result = enrich_model_with_pricing(model_data, "openrouter")
            assert result is not None
            assert result["id"] == "test-model"


class TestGatewayProviderZeroPricingFiltering:
    """Test that gateway providers filter out models with zero cross-reference pricing"""

    def test_gateway_provider_filters_zero_cross_reference_pricing(self):
        model_data = {
            "id": "some-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }
        zero_cross_ref = {"prompt": "0", "completion": "0", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=zero_cross_ref,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "aihubmix")
                    assert result is None

    def test_gateway_provider_accepts_nonzero_cross_reference_pricing(self):
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }
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
        model_data = {
            "id": "some-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }
        zero_variants = {"prompt": "0.0", "completion": "0.00", "request": "0", "image": "0"}

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing",
                return_value=zero_variants,
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, "anannas")
                    assert result is None

    def test_gateway_provider_accepts_partial_nonzero_pricing(self):
        model_data = {
            "id": "gpt-4o",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }
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

    @pytest.mark.parametrize(
        "provider",
        [
            "deepinfra",
            "featherless",
            "groq",
            "fireworks",
            "together",
        ],
    )
    def test_provider_filters_models_without_pricing(self, provider):
        model_data = {
            "id": "unknown-model",
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=None):
            with patch(
                "src.services.pricing_lookup._get_cross_reference_pricing", return_value=None
            ):
                with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
                    result = enrich_model_with_pricing(model_data, provider)
                    assert result is None

    @pytest.mark.parametrize(
        "provider,model_id,manual_pricing",
        [
            (
                "deepinfra",
                "meta-llama/Meta-Llama-3.1-8B-Instruct",
                {"prompt": "0.055", "completion": "0.055", "request": "0", "image": "0"},
            ),
            (
                "featherless",
                "meta-llama/Meta-Llama-3.1-8B-Instruct",
                {"prompt": "0.05", "completion": "0.05", "request": "0", "image": "0"},
            ),
            (
                "groq",
                "groq/llama-3.3-70b-versatile",
                {"prompt": "0.59", "completion": "0.79", "request": "0", "image": "0"},
            ),
            (
                "fireworks",
                "accounts/fireworks/models/deepseek-v3",
                {"prompt": "0.56", "completion": "1.68", "request": "0", "image": "0"},
            ),
        ],
    )
    def test_provider_accepts_models_with_manual_pricing(self, provider, model_id, manual_pricing):
        model_data = {
            "id": model_id,
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }

        with patch("src.services.pricing_lookup.get_model_pricing", return_value=manual_pricing):
            result = enrich_model_with_pricing(model_data, provider)
            assert result is not None
            assert result["pricing"] == manual_pricing
            assert result["pricing_source"] == "manual"


class TestCrossReferencePricingNullHandling:
    """Test null/None value handling in cross-reference pricing"""

    @pytest.mark.integration
    def test_cross_reference_handles_none_pricing_values(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": None,
                    "completion": "0.000015",
                    "request": None,
                    "image": None,
                },
            }
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is not None
                assert result["prompt"] == "0"
                assert result["completion"] == "0.000015"
                assert result["request"] == "0"
                assert result["image"] == "0"

    @pytest.mark.integration
    def test_cross_reference_handles_empty_string_pricing(self):
        pytest.importorskip("fastapi")
        from src.services import models
        from src.services.pricing_lookup import _get_cross_reference_pricing

        mock_openrouter_models = [
            {"id": "openai/gpt-4o", "pricing": {"prompt": "", "completion": "0.000015"}}
        ]

        with patch("src.services.pricing_lookup._is_building_catalog", return_value=False):
            with patch.object(models, "get_cached_models", return_value=mock_openrouter_models):
                result = _get_cross_reference_pricing("gpt-4o")
                assert result is not None
                assert result["prompt"] == "0"
                assert result["completion"] == "0.000015"


class TestIsFreeField:
    """Test is_free field is set correctly for models"""

    @pytest.mark.parametrize(
        "provider,model_id,pricing",
        [
            ("groq", "groq/llama-3.3-70b-versatile", {"prompt": "0.59", "completion": "0.79"}),
            (
                "deepinfra",
                "meta-llama/Meta-Llama-3.1-8B-Instruct",
                {"prompt": "0.06", "completion": "0.06"},
            ),
            (
                "featherless",
                "meta-llama/Llama-3.1-8B-Instruct",
                {"prompt": "0.10", "completion": "0.10"},
            ),
            (
                "together",
                "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                {"prompt": "0.88", "completion": "0.88"},
            ),
            ("aihubmix", "gpt-4o", {"prompt": "2.50", "completion": "10.00"}),
            ("helicone", "claude-3-opus", {"prompt": "15.00", "completion": "75.00"}),
        ],
    )
    def test_non_openrouter_gateway_sets_is_free_false(self, provider, model_id, pricing):
        model_data = {"id": model_id, "pricing": pricing}
        result = enrich_model_with_pricing(model_data, provider)
        assert result is not None
        assert result["is_free"] is False

    def test_fireworks_sets_is_free_false(self):
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
        model_data = {
            "id": "openai/gpt-4o",
            "pricing": {"prompt": "2.50", "completion": "10.00"},
        }
        result = enrich_model_with_pricing(model_data, "openrouter")
        assert result is not None
        assert "is_free" not in result


# ===========================================================================
# Pricing Normalization Tests (from test_pricing_normalization.py)
# ===========================================================================


class TestNormalizeToPerToken:
    """Test normalize_to_per_token function"""

    def test_normalize_per_1m_to_per_token(self):
        result = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000055")

    def test_normalize_per_1k_to_per_token(self):
        result = normalize_to_per_token(0.055, PricingFormat.PER_1K_TOKENS)
        assert result == Decimal("0.000055")

    def test_normalize_already_per_token(self):
        result = normalize_to_per_token(0.000000055, PricingFormat.PER_TOKEN)
        assert result == Decimal("0.000000055")

    def test_normalize_negative_price(self):
        result = normalize_to_per_token(-1, PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_zero_price(self):
        result = normalize_to_per_token(0, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0")

    def test_normalize_none_price(self):
        result = normalize_to_per_token(None, PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_empty_string(self):
        result = normalize_to_per_token("", PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_string_price(self):
        result = normalize_to_per_token("0.055", PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000055")

    def test_normalize_decimal_price(self):
        result = normalize_to_per_token(Decimal("0.055"), PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000055")

    def test_normalize_large_price(self):
        result = normalize_to_per_token(30, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.00003")

    def test_normalize_very_small_price(self):
        result = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000055")

    def test_normalize_invalid_string(self):
        result = normalize_to_per_token("invalid", PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_scientific_notation(self):
        result = normalize_to_per_token("5.5e-2", PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000055")

    def test_normalize_very_large_number(self):
        result = normalize_to_per_token(1000000, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("1")

    def test_normalize_very_small_number(self):
        result = normalize_to_per_token(0.000001, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000000001")


class TestNormalizePricingDict:
    """Test normalize_pricing_dict function"""

    def test_normalize_full_pricing_dict(self):
        pricing = {"prompt": "0.055", "completion": "0.040", "image": "0.001", "request": "0"}
        result = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        assert float(result["prompt"]) == pytest.approx(0.000000055, rel=1e-9)
        assert float(result["completion"]) == pytest.approx(0.000000040, rel=1e-9)
        assert float(result["image"]) == pytest.approx(0.000000001, rel=1e-9)
        assert result["request"] == "0"

    def test_normalize_partial_pricing_dict(self):
        pricing = {"prompt": "0.055"}
        result = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        assert float(result["prompt"]) == pytest.approx(0.000000055, rel=1e-9)
        assert result["completion"] == "0"
        assert result["image"] == "0"
        assert result["request"] == "0"

    def test_normalize_empty_pricing_dict(self):
        result = normalize_pricing_dict({}, PricingFormat.PER_1M_TOKENS)
        assert result["prompt"] == "0"
        assert result["completion"] == "0"
        assert result["image"] == "0"
        assert result["request"] == "0"

    def test_normalize_none_pricing_dict(self):
        result = normalize_pricing_dict(None, PricingFormat.PER_1M_TOKENS)
        assert result["prompt"] == "0"
        assert result["completion"] == "0"


class TestProviderFormats:
    """Test provider format mappings"""

    def test_get_openrouter_format(self):
        assert get_provider_format("openrouter") == PricingFormat.PER_1M_TOKENS

    def test_get_deepinfra_format(self):
        assert get_provider_format("deepinfra") == PricingFormat.PER_1M_TOKENS

    def test_get_aihubmix_format(self):
        assert get_provider_format("aihubmix") == PricingFormat.PER_1K_TOKENS

    def test_get_unknown_provider_format(self):
        assert get_provider_format("unknown-provider") == PricingFormat.PER_1M_TOKENS

    def test_provider_format_case_insensitive(self):
        assert get_provider_format("OpenRouter") == PricingFormat.PER_1M_TOKENS
        assert get_provider_format("DEEPINFRA") == PricingFormat.PER_1M_TOKENS


class TestAutoDetectFormat:
    """Test auto-detection of pricing format"""

    def test_detect_per_token(self):
        assert auto_detect_format(0.000000055) == PricingFormat.PER_TOKEN

    def test_detect_per_1k(self):
        assert auto_detect_format(0.000055) == PricingFormat.PER_1K_TOKENS

    def test_detect_per_1m(self):
        assert auto_detect_format(0.055) == PricingFormat.PER_1M_TOKENS
        assert auto_detect_format(30) == PricingFormat.PER_1M_TOKENS

    def test_detect_boundary_values(self):
        assert auto_detect_format(0.0000009) == PricingFormat.PER_TOKEN
        assert auto_detect_format(0.000001) == PricingFormat.PER_1K_TOKENS
        assert auto_detect_format(0.0009) == PricingFormat.PER_1K_TOKENS
        assert auto_detect_format(0.001) == PricingFormat.PER_1M_TOKENS


class TestConvertBetweenFormats:
    """Test conversion between different formats"""

    def test_convert_1m_to_token(self):
        result = convert_between_formats(
            0.055, PricingFormat.PER_1M_TOKENS, PricingFormat.PER_TOKEN
        )
        assert result == Decimal("0.000000055")

    def test_convert_1k_to_token(self):
        result = convert_between_formats(
            0.055, PricingFormat.PER_1K_TOKENS, PricingFormat.PER_TOKEN
        )
        assert result == Decimal("0.000055")

    def test_convert_token_to_1m(self):
        result = convert_between_formats(
            0.000000055, PricingFormat.PER_TOKEN, PricingFormat.PER_1M_TOKENS
        )
        assert result == Decimal("0.055")

    def test_convert_1k_to_1m(self):
        result = convert_between_formats(
            0.055, PricingFormat.PER_1K_TOKENS, PricingFormat.PER_1M_TOKENS
        )
        assert result == Decimal("55")


class TestValidateNormalizedPrice:
    """Test price validation"""

    def test_validate_correct_per_token(self):
        assert validate_normalized_price(0.000000055) is True
        assert validate_normalized_price(0.00003) is True
        assert validate_normalized_price(0.0009) is True

    def test_validate_incorrect_per_token(self):
        assert validate_normalized_price(0.055) is False
        assert validate_normalized_price(30) is False


class TestNormalizePriceFromProvider:
    """Test convenience function for provider-specific normalization"""

    def test_normalize_from_deepinfra(self):
        result = normalize_price_from_provider(0.055, "deepinfra")
        assert result == Decimal("0.000000055")

    def test_normalize_from_aihubmix(self):
        result = normalize_price_from_provider(0.055, "aihubmix")
        assert result == Decimal("0.000055")

    def test_normalize_from_openrouter(self):
        result = normalize_price_from_provider(30, "openrouter")
        assert result == Decimal("0.00003")


class TestCostCalculations:
    """Test that cost calculations are accurate"""

    def test_llama_3_1_8b_cost(self):
        price_per_token = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        cost = float(1000 * price_per_token)
        assert cost == pytest.approx(0.000055, rel=1e-6)

    def test_gpt4_cost(self):
        price_per_token = normalize_to_per_token(30, PricingFormat.PER_1M_TOKENS)
        cost = float(1000 * price_per_token)
        assert cost == pytest.approx(0.030, rel=1e-6)

    def test_mixed_input_output_cost(self):
        input_price = normalize_to_per_token(5, PricingFormat.PER_1M_TOKENS)
        output_price = normalize_to_per_token(15, PricingFormat.PER_1M_TOKENS)
        total_cost = float((500 * input_price) + (100 * output_price))
        expected_cost = (500 * 0.000005) + (100 * 0.000015)
        assert total_cost == pytest.approx(expected_cost, rel=1e-6)

    def test_large_request_cost(self):
        price_per_token = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        cost = float(100000 * price_per_token)
        assert cost == pytest.approx(0.0055, rel=1e-6)


class TestRealWorldScenarios:
    """Test real-world pricing scenarios"""

    def test_typical_openrouter_model(self):
        pricing = {"prompt": "5", "completion": "15"}
        normalized = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        input_cost = 2000 * float(normalized["prompt"])
        output_cost = 500 * float(normalized["completion"])
        total = input_cost + output_cost

        assert total == pytest.approx(0.0175, rel=1e-6)

    def test_typical_aihubmix_model(self):
        pricing = {"prompt": "0.35", "completion": "0.40"}
        normalized = normalize_pricing_dict(pricing, PricingFormat.PER_1K_TOKENS)

        input_cost = 1000 * float(normalized["prompt"])
        output_cost = 500 * float(normalized["completion"])
        total = input_cost + output_cost

        assert total == pytest.approx(0.55, rel=1e-6)


pytestmark = pytest.mark.critical
