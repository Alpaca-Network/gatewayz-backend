"""
Tests for pricing normalization utilities

These tests verify that pricing is correctly converted from various
provider formats to per-token format.
"""

from decimal import Decimal

import pytest

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


class TestNormalizeToPerToken:
    """Test normalize_to_per_token function"""

    def test_normalize_per_1m_to_per_token(self):
        """Test normalization from per-1M format"""
        result = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.000000055")
        assert result == expected

    def test_normalize_per_1k_to_per_token(self):
        """Test normalization from per-1K format"""
        result = normalize_to_per_token(0.055, PricingFormat.PER_1K_TOKENS)
        expected = Decimal("0.000055")
        assert result == expected

    def test_normalize_already_per_token(self):
        """Test normalization when already per-token"""
        result = normalize_to_per_token(0.000000055, PricingFormat.PER_TOKEN)
        expected = Decimal("0.000000055")
        assert result == expected

    def test_normalize_negative_price(self):
        """Test that negative prices (dynamic) return None"""
        result = normalize_to_per_token(-1, PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_zero_price(self):
        """Test that zero prices are handled"""
        result = normalize_to_per_token(0, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0")

    def test_normalize_none_price(self):
        """Test that None returns None"""
        result = normalize_to_per_token(None, PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_empty_string(self):
        """Test that empty string returns None"""
        result = normalize_to_per_token("", PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_string_price(self):
        """Test normalization with string input"""
        result = normalize_to_per_token("0.055", PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.000000055")
        assert result == expected

    def test_normalize_decimal_price(self):
        """Test normalization with Decimal input"""
        result = normalize_to_per_token(Decimal("0.055"), PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.000000055")
        assert result == expected

    def test_normalize_large_price(self):
        """Test normalization with large price (expensive model)"""
        # GPT-4 at $30 per 1M tokens
        result = normalize_to_per_token(30, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.00003")
        assert result == expected

    def test_normalize_very_small_price(self):
        """Test normalization with very small price (cheap model)"""
        # Llama-3.1-8B at $0.055 per 1M tokens
        result = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.000000055")
        assert result == expected


class TestNormalizePricingDict:
    """Test normalize_pricing_dict function"""

    def test_normalize_full_pricing_dict(self):
        """Test normalizing full pricing dictionary"""
        pricing = {
            "prompt": "0.055",
            "completion": "0.040",
            "image": "0.001",
            "request": "0",
        }
        result = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        assert float(result["prompt"]) == pytest.approx(0.000000055, rel=1e-9)
        assert float(result["completion"]) == pytest.approx(0.000000040, rel=1e-9)
        assert float(result["image"]) == pytest.approx(0.000000001, rel=1e-9)
        assert result["request"] == "0"

    def test_normalize_partial_pricing_dict(self):
        """Test normalizing dict with missing fields"""
        pricing = {"prompt": "0.055"}
        result = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        assert float(result["prompt"]) == pytest.approx(0.000000055, rel=1e-9)
        assert result["completion"] == "0"
        assert result["image"] == "0"
        assert result["request"] == "0"

    def test_normalize_empty_pricing_dict(self):
        """Test normalizing empty dict"""
        result = normalize_pricing_dict({}, PricingFormat.PER_1M_TOKENS)

        assert result["prompt"] == "0"
        assert result["completion"] == "0"
        assert result["image"] == "0"
        assert result["request"] == "0"

    def test_normalize_none_pricing_dict(self):
        """Test normalizing None (should handle gracefully)"""
        result = normalize_pricing_dict(None, PricingFormat.PER_1M_TOKENS)

        assert result["prompt"] == "0"
        assert result["completion"] == "0"


class TestProviderFormats:
    """Test provider format mappings"""

    def test_get_openrouter_format(self):
        """OpenRouter uses per-1M format"""
        assert get_provider_format("openrouter") == PricingFormat.PER_1M_TOKENS

    def test_get_deepinfra_format(self):
        """DeepInfra uses per-1M format"""
        assert get_provider_format("deepinfra") == PricingFormat.PER_1M_TOKENS

    def test_get_aihubmix_format(self):
        """AiHubMix uses per-1K format"""
        assert get_provider_format("aihubmix") == PricingFormat.PER_1K_TOKENS

    def test_get_unknown_provider_format(self):
        """Unknown providers default to per-1M"""
        assert get_provider_format("unknown-provider") == PricingFormat.PER_1M_TOKENS

    def test_provider_format_case_insensitive(self):
        """Provider format lookup is case-insensitive"""
        assert get_provider_format("OpenRouter") == PricingFormat.PER_1M_TOKENS
        assert get_provider_format("DEEPINFRA") == PricingFormat.PER_1M_TOKENS


class TestAutoDetectFormat:
    """Test auto-detection of pricing format"""

    def test_detect_per_token(self):
        """Detect per-token format (very small values)"""
        assert auto_detect_format(0.000000055) == PricingFormat.PER_TOKEN

    def test_detect_per_1k(self):
        """Detect per-1K format (medium values)"""
        assert auto_detect_format(0.000055) == PricingFormat.PER_1K_TOKENS

    def test_detect_per_1m(self):
        """Detect per-1M format (large values)"""
        assert auto_detect_format(0.055) == PricingFormat.PER_1M_TOKENS
        assert auto_detect_format(30) == PricingFormat.PER_1M_TOKENS

    def test_detect_boundary_values(self):
        """Test detection at boundary values"""
        assert auto_detect_format(0.0000009) == PricingFormat.PER_TOKEN
        assert auto_detect_format(0.000001) == PricingFormat.PER_1K_TOKENS
        assert auto_detect_format(0.0009) == PricingFormat.PER_1K_TOKENS
        assert auto_detect_format(0.001) == PricingFormat.PER_1M_TOKENS


class TestConvertBetweenFormats:
    """Test conversion between different formats"""

    def test_convert_1m_to_token(self):
        """Convert from per-1M to per-token"""
        result = convert_between_formats(
            0.055, PricingFormat.PER_1M_TOKENS, PricingFormat.PER_TOKEN
        )
        assert result == Decimal("0.000000055")

    def test_convert_1k_to_token(self):
        """Convert from per-1K to per-token"""
        result = convert_between_formats(
            0.055, PricingFormat.PER_1K_TOKENS, PricingFormat.PER_TOKEN
        )
        assert result == Decimal("0.000055")

    def test_convert_token_to_1m(self):
        """Convert from per-token to per-1M"""
        result = convert_between_formats(
            0.000000055, PricingFormat.PER_TOKEN, PricingFormat.PER_1M_TOKENS
        )
        assert result == Decimal("0.055")

    def test_convert_1k_to_1m(self):
        """Convert from per-1K to per-1M"""
        result = convert_between_formats(
            0.055, PricingFormat.PER_1K_TOKENS, PricingFormat.PER_1M_TOKENS
        )
        assert result == Decimal("55")


class TestValidateNormalizedPrice:
    """Test price validation"""

    def test_validate_correct_per_token(self):
        """Valid per-token prices should pass"""
        assert validate_normalized_price(0.000000055) is True
        assert validate_normalized_price(0.00003) is True  # GPT-4
        assert validate_normalized_price(0.0009) is True

    def test_validate_incorrect_per_token(self):
        """Prices that are too large should fail"""
        assert validate_normalized_price(0.055) is False  # Per-1M
        assert validate_normalized_price(30) is False  # Per-1M


class TestNormalizePriceFromProvider:
    """Test convenience function for provider-specific normalization"""

    def test_normalize_from_deepinfra(self):
        """DeepInfra uses per-1M format"""
        result = normalize_price_from_provider(0.055, "deepinfra")
        assert result == Decimal("0.000000055")

    def test_normalize_from_aihubmix(self):
        """AiHubMix uses per-1K format"""
        result = normalize_price_from_provider(0.055, "aihubmix")
        assert result == Decimal("0.000055")

    def test_normalize_from_openrouter(self):
        """OpenRouter uses per-1M format"""
        result = normalize_price_from_provider(30, "openrouter")
        assert result == Decimal("0.00003")


class TestCostCalculations:
    """Test that cost calculations are accurate"""

    def test_llama_3_1_8b_cost(self):
        """Test cost calculation for Llama-3.1-8B (known pricing)"""
        # Known: $0.055 per 1M tokens
        price_per_token = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        tokens = 1000

        cost = float(tokens * price_per_token)
        expected_cost = 0.000055  # $0.055 per 1M × 1000 tokens

        assert cost == pytest.approx(expected_cost, rel=1e-6)

    def test_gpt4_cost(self):
        """Test cost calculation for GPT-4 (known pricing)"""
        # GPT-4: ~$30 per 1M input tokens
        price_per_token = normalize_to_per_token(30, PricingFormat.PER_1M_TOKENS)
        tokens = 1000

        cost = float(tokens * price_per_token)
        expected_cost = 0.030  # $30 per 1M × 1000 tokens

        assert cost == pytest.approx(expected_cost, rel=1e-6)

    def test_mixed_input_output_cost(self):
        """Test cost with different input/output pricing"""
        # Simulating a model with different input/output pricing
        input_price = normalize_to_per_token(5, PricingFormat.PER_1M_TOKENS)
        output_price = normalize_to_per_token(15, PricingFormat.PER_1M_TOKENS)

        input_tokens = 500
        output_tokens = 100

        total_cost = float((input_tokens * input_price) + (output_tokens * output_price))
        expected_cost = (500 * 0.000005) + (100 * 0.000015)  # 0.0025 + 0.0015 = 0.004

        assert total_cost == pytest.approx(expected_cost, rel=1e-6)

    def test_large_request_cost(self):
        """Test cost calculation for large request"""
        # 100K token request at $0.055 per 1M
        price_per_token = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        tokens = 100000

        cost = float(tokens * price_per_token)
        expected_cost = 0.0055  # $0.055 per 1M × 100K tokens

        assert cost == pytest.approx(expected_cost, rel=1e-6)


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_invalid_price_string(self):
        """Invalid price strings should return None"""
        result = normalize_to_per_token("invalid", PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_scientific_notation(self):
        """Test handling of scientific notation"""
        result = normalize_to_per_token("5.5e-2", PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000055")

    def test_very_large_number(self):
        """Test handling of very large numbers"""
        result = normalize_to_per_token(1000000, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("1")

    def test_very_small_number(self):
        """Test handling of very small numbers"""
        result = normalize_to_per_token(0.000001, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0.000000000001")


# Integration-style test
class TestRealWorldScenarios:
    """Test real-world pricing scenarios"""

    def test_typical_openrouter_model(self):
        """Test typical OpenRouter model pricing"""
        pricing = {
            "prompt": "5",  # $5 per 1M tokens
            "completion": "15",  # $15 per 1M tokens
        }

        normalized = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        # Calculate cost for 2000 input, 500 output tokens
        input_cost = 2000 * float(normalized["prompt"])
        output_cost = 500 * float(normalized["completion"])
        total = input_cost + output_cost

        # Expected: (2000 × 0.000005) + (500 × 0.000015) = 0.01 + 0.0075 = 0.0175
        assert total == pytest.approx(0.0175, rel=1e-6)

    def test_typical_aihubmix_model(self):
        """Test typical AiHubMix model pricing"""
        pricing = {
            "prompt": "0.35",  # $0.35 per 1K tokens
            "completion": "0.40",  # $0.40 per 1K tokens
        }

        normalized = normalize_pricing_dict(pricing, PricingFormat.PER_1K_TOKENS)

        # Calculate cost for 1000 input, 500 output tokens
        input_cost = 1000 * float(normalized["prompt"])
        output_cost = 500 * float(normalized["completion"])
        total = input_cost + output_cost

        # Expected: (1000 × 0.00035) + (500 × 0.0004) = 0.35 + 0.20 = 0.55
        assert total == pytest.approx(0.55, rel=1e-6)
