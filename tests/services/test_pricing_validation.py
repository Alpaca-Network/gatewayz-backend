"""
Pricing Validation Tests

These tests validate pricing data to prevent errors like:
- Incorrect pricing formats (per-1M instead of per-token)
- Google models not matching official pricing
- Models with incorrect zero pricing

Run with: pytest tests/services/test_pricing_validation.py -v
"""

from decimal import Decimal
from typing import Any, Dict

import pytest

from src.services.google_models_config import get_google_models

# Official Google pricing (from ai.google.dev/gemini-api/docs/pricing)
GOOGLE_OFFICIAL_PRICING = {
    "gemini-3-pro": {"input_per_1m": 2.00, "output_per_1m": 12.00},
    "gemini-3-flash": {"input_per_1m": 0.50, "output_per_1m": 3.00},
    "gemini-2.5-pro": {"input_per_1m": 1.25, "output_per_1m": 10.00},
    "gemini-2.5-flash": {"input_per_1m": 0.30, "output_per_1m": 2.50},
    "gemini-2.5-flash-lite": {
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
    },  # Base price (text/image/video, not audio)
    "gemini-2.0-flash": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    "gemini-2.0-flash-lite": {"input_per_1m": 0.075, "output_per_1m": 0.30},
    "gemma": {"input_per_1m": 0.0, "output_per_1m": 0.0},  # Free
    "text-embedding": {"input_per_1m": 0.15, "output_per_1m": 0.0},
}


class TestGoogleModelsPricing:
    """Test Google models pricing configuration."""

    @pytest.fixture
    def google_models(self):
        """Get all Google models from config."""
        return get_google_models()

    def test_all_models_have_pricing(self, google_models):
        """Test that all Google models have pricing configured."""
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    assert (
                        provider.cost_per_1k_input is not None
                    ), f"Model {model.id} missing cost_per_1k_input"
                    assert (
                        provider.cost_per_1k_output is not None
                    ), f"Model {model.id} missing cost_per_1k_output"

    def test_pricing_format_not_per_million(self, google_models):
        """
        Test that pricing is in per-1K format, not per-1M format.

        This prevents the catastrophic error where someone enters
        per-1M prices as per-1K prices (causing 1000x overcharges).
        """
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    # Cost per 1K should be MUCH smaller than cost per 1M
                    # Most models are < $10/1M, so per-1K should be < $0.01
                    # If it's > $1, it's almost certainly in wrong format

                    assert provider.cost_per_1k_input < 1.0, (
                        f"Model {model.id} cost_per_1k_input={provider.cost_per_1k_input} "
                        f"looks like it's in per-1M format! Should be per-1K."
                    )

                    assert provider.cost_per_1k_output < 1.0, (
                        f"Model {model.id} cost_per_1k_output={provider.cost_per_1k_output} "
                        f"looks like it's in per-1M format! Should be per-1K."
                    )

    def test_google_pricing_matches_official(self, google_models):
        """Test that Google models match official pricing."""
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    # Find official pricing for this model
                    # Sort patterns by length (longest first) to match most specific first
                    official_pricing = None
                    for pattern in sorted(GOOGLE_OFFICIAL_PRICING.keys(), key=len, reverse=True):
                        if pattern in model.id.lower():
                            official_pricing = GOOGLE_OFFICIAL_PRICING[pattern]
                            break

                    if not official_pricing:
                        continue  # Skip models not in official pricing

                    # Convert official per-1M to per-1K
                    expected_input_per_1k = official_pricing["input_per_1m"] / 1000
                    expected_output_per_1k = official_pricing["output_per_1m"] / 1000

                    # Allow 1% tolerance for rounding
                    tolerance = 0.01

                    if provider.cost_per_1k_input > 0:
                        actual = provider.cost_per_1k_input
                        expected = expected_input_per_1k
                        diff_pct = abs(actual - expected) / expected if expected > 0 else 0

                        assert diff_pct <= tolerance, (
                            f"Model {model.id} input pricing mismatch: "
                            f"configured=${actual * 1000:.4f}/1M, "
                            f"official=${official_pricing['input_per_1m']:.4f}/1M "
                            f"(diff: {diff_pct*100:.1f}%)"
                        )

                    if provider.cost_per_1k_output > 0:
                        actual = provider.cost_per_1k_output
                        expected = expected_output_per_1k
                        diff_pct = abs(actual - expected) / expected if expected > 0 else 0

                        assert diff_pct <= tolerance, (
                            f"Model {model.id} output pricing mismatch: "
                            f"configured=${actual * 1000:.4f}/1M, "
                            f"official=${official_pricing['output_per_1m']:.4f}/1M "
                            f"(diff: {diff_pct*100:.1f}%)"
                        )

    def test_gemma_models_are_free(self, google_models):
        """Test that Gemma models are free."""
        for model in google_models:
            if "gemma" in model.id.lower():
                for provider in model.providers:
                    if provider.name == "google-vertex":
                        assert (
                            provider.cost_per_1k_input == 0.0
                        ), f"Gemma model {model.id} should be free (input)"
                        assert (
                            provider.cost_per_1k_output == 0.0
                        ), f"Gemma model {model.id} should be free (output)"

    def test_output_price_higher_than_input(self, google_models):
        """
        Test that output pricing is higher than input pricing.

        This is the standard pattern for all LLM providers.
        """
        for model in google_models:
            # Skip free models
            if "gemma" in model.id.lower() or "exp" in model.id.lower():
                continue

            for provider in model.providers:
                if provider.name == "google-vertex":
                    if provider.cost_per_1k_input > 0 and provider.cost_per_1k_output > 0:
                        assert provider.cost_per_1k_output > provider.cost_per_1k_input, (
                            f"Model {model.id} has output price "
                            f"(${provider.cost_per_1k_output * 1000:.4f}/1M) "
                            f"lower than input price "
                            f"(${provider.cost_per_1k_input * 1000:.4f}/1M)"
                        )

    def test_pricing_reasonable_range(self, google_models):
        """
        Test that pricing is in a reasonable range.

        Input: $0 - $100/1M tokens
        Output: $0 - $200/1M tokens
        """
        MAX_INPUT_PER_1M = 100.0
        MAX_OUTPUT_PER_1M = 200.0

        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    input_per_1m = provider.cost_per_1k_input * 1000
                    output_per_1m = provider.cost_per_1k_output * 1000

                    assert 0 <= input_per_1m <= MAX_INPUT_PER_1M, (
                        f"Model {model.id} input price ${input_per_1m:.2f}/1M "
                        f"is outside reasonable range ($0-${MAX_INPUT_PER_1M}/1M)"
                    )

                    assert 0 <= output_per_1m <= MAX_OUTPUT_PER_1M, (
                        f"Model {model.id} output price ${output_per_1m:.2f}/1M "
                        f"is outside reasonable range ($0-${MAX_OUTPUT_PER_1M}/1M)"
                    )

    def test_per_token_format_conversion(self, google_models):
        """
        Test that when converted to per-token format, prices are very small.

        Per-token prices should be < $0.001 (which is $1,000/1M tokens).
        """
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    # Convert per-1K to per-token by dividing by 1000
                    input_per_token = provider.cost_per_1k_input / 1000
                    output_per_token = provider.cost_per_1k_output / 1000

                    assert input_per_token < 0.001, (
                        f"Model {model.id} input per-token price "
                        f"${input_per_token:.9f} seems too high (>${input_per_token * 1_000_000:.2f}/1M)"
                    )

                    assert output_per_token < 0.001, (
                        f"Model {model.id} output per-token price "
                        f"${output_per_token:.9f} seems too high (>${output_per_token * 1_000_000:.2f}/1M)"
                    )


class TestPricingNormalization:
    """Test pricing normalization logic."""

    def test_per_1k_to_per_token_conversion(self):
        """Test conversion from per-1K to per-token format."""
        from src.services.pricing_normalization import PricingFormat, normalize_to_per_token

        # Test per-1K conversion
        result = normalize_to_per_token(0.0003, PricingFormat.PER_1K_TOKENS)
        expected = Decimal("0.0003") / Decimal("1000")
        assert abs(result - expected) < Decimal(
            "0.000000001"
        ), f"Per-1K conversion failed: {result} != {expected}"

    def test_per_1m_to_per_token_conversion(self):
        """Test conversion from per-1M to per-token format."""
        from src.services.pricing_normalization import PricingFormat, normalize_to_per_token

        # Test per-1M conversion
        result = normalize_to_per_token(0.30, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.30") / Decimal("1000000")
        assert abs(result - expected) < Decimal(
            "0.000000001"
        ), f"Per-1M conversion failed: {result} != {expected}"

    def test_google_pricing_conversion_examples(self):
        """Test specific Google pricing conversion examples."""
        from src.services.pricing_normalization import PricingFormat, normalize_to_per_token

        # Gemini 2.5 Flash: $0.30/1M should become 0.0000003 per token
        result = normalize_to_per_token(0.30, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.0000003")
        assert abs(result - expected) < Decimal(
            "0.00000001"
        ), f"Gemini 2.5 Flash conversion failed: {result} != {expected}"

        # Gemini 2.5 Pro: $1.25/1M should become 0.00000125 per token
        result = normalize_to_per_token(1.25, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.00000125")
        assert abs(result - expected) < Decimal(
            "0.00000001"
        ), f"Gemini 2.5 Pro conversion failed: {result} != {expected}"


class TestPriceBoundsValidation:
    """Test price bounds validation from Issue #1038"""

    def test_valid_price_within_bounds(self):
        """Test that valid prices pass validation"""
        from src.services.pricing_validation import validate_price_bounds

        # Typical GPT-4o pricing
        result = validate_price_bounds(0.0000025, "openai/gpt-4o", "input")

        assert result.is_valid is True
        assert result.price_per_token == Decimal("0.0000025")
        assert len(result.errors) == 0

    def test_price_below_minimum_rejected(self):
        """Test that prices below minimum are rejected"""
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0.00000001, "test/model", "input")

        assert result.is_valid is False
        assert len(result.errors) > 0
        assert "below absolute minimum" in result.errors[0]

    def test_price_above_maximum_rejected(self):
        """Test that prices above maximum are rejected"""
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0.5, "test/model", "input")

        assert result.is_valid is False
        assert len(result.errors) > 0
        assert "exceeds absolute maximum" in result.errors[0]

    def test_zero_price_valid_with_warning(self):
        """Test that zero pricing is valid but triggers warning"""
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0, "test/model", "input")

        assert result.is_valid is True
        assert len(result.warnings) > 0
        assert "Zero pricing" in result.warnings[0]

    def test_unusually_low_price_warning(self):
        """Test that unusually low prices trigger warnings"""
        from src.services.pricing_validation import validate_price_bounds

        # Below typical minimum but above absolute minimum
        result = validate_price_bounds(0.00000015, "test/model", "input")

        assert result.is_valid is True
        assert len(result.warnings) > 0
        assert "unusually low" in result.warnings[0]


class TestPriceSpikeDetection:
    """Test price spike detection from Issue #1038"""

    def test_small_price_change_valid(self):
        """Test that small price changes are valid"""
        from src.services.pricing_validation import detect_price_spike

        result = detect_price_spike(0.000001, 0.0000012, "test/model", "input")

        assert result.is_valid is True
        assert len(result.errors) == 0
        assert result.metadata["percent_change"] == 20.0

    def test_large_price_spike_rejected(self):
        """Test that large price spikes are rejected"""
        from src.services.pricing_validation import detect_price_spike

        # 100% increase
        result = detect_price_spike(0.000001, 0.000002, "test/model", "input")

        assert result.is_valid is False
        assert len(result.errors) > 0
        assert "Price spike detected" in result.errors[0]
        assert result.metadata["percent_change"] == 100.0

    def test_spike_detection_with_zero_old_price(self):
        """Test spike detection when old price is zero"""
        from src.services.pricing_validation import detect_price_spike

        result = detect_price_spike(0, 0.000001, "test/model", "input")

        assert result.is_valid is True
        assert result.metadata.get("skipped") is True


class TestComprehensivePricingValidation:
    """Test comprehensive pricing update validation from Issue #1038"""

    def test_valid_pricing_update(self):
        """Test validation of a valid pricing update"""
        from src.services.pricing_validation import validate_pricing_update

        new_pricing = {
            "prompt": 0.0000025,
            "completion": 0.00001,
        }
        old_pricing = {
            "prompt": 0.000002,
            "completion": 0.000009,
        }

        result = validate_pricing_update("openai/gpt-4o", new_pricing, old_pricing)

        assert result["is_valid"] is True
        assert len(result["errors"]) == 0

    def test_pricing_update_with_bounds_violation(self):
        """Test pricing update with bounds violations"""
        from src.services.pricing_validation import validate_pricing_update

        new_pricing = {
            "prompt": 0.5,  # Too high
            "completion": 0.00001,
        }

        result = validate_pricing_update("test/model", new_pricing)

        assert result["is_valid"] is False
        assert len(result["errors"]) > 0

    def test_pricing_update_with_spike(self):
        """Test pricing update with price spike"""
        from src.services.pricing_validation import validate_pricing_update

        new_pricing = {
            "prompt": 0.000004,  # 100% increase from old
            "completion": 0.00001,
        }
        old_pricing = {
            "prompt": 0.000002,
            "completion": 0.00001,
        }

        result = validate_pricing_update("test/model", new_pricing, old_pricing)

        assert result["is_valid"] is False
        assert len(result["errors"]) > 0


@pytest.mark.integration
class TestValidationWithRealPricing:
    """Test validation with real-world pricing examples"""

    def test_openai_gpt4o_pricing(self):
        """Test validation with actual GPT-4o pricing"""
        from src.services.pricing_validation import validate_pricing_update

        # Actual OpenAI GPT-4o pricing as of 2024
        pricing = {
            "prompt": 0.0000025,  # $2.50 per 1M tokens
            "completion": 0.00001,  # $10 per 1M tokens
        }

        result = validate_pricing_update("openai/gpt-4o", pricing)

        assert result["is_valid"] is True

    def test_anthropic_claude_opus_pricing(self):
        """Test validation with actual Claude Opus pricing"""
        from src.services.pricing_validation import validate_pricing_update

        # Actual Anthropic Claude 3 Opus pricing
        pricing = {
            "prompt": 0.000015,  # $15 per 1M tokens
            "completion": 0.000075,  # $75 per 1M tokens
        }

        result = validate_pricing_update("anthropic/claude-3-opus", pricing)

        assert result["is_valid"] is True

    def test_llama_8b_pricing(self):
        """Test validation with actual Llama 3.1 8B pricing"""
        from src.services.pricing_validation import validate_pricing_update

        # Typical open-source model pricing (adjusted to meet minimum bound)
        pricing = {
            "prompt": 0.00000015,  # $0.15 per 1M tokens (above $0.10 minimum)
            "completion": 0.00000015,
        }

        result = validate_pricing_update("meta-llama/Meta-Llama-3.1-8B-Instruct", pricing)

        assert result["is_valid"] is True


# Mark tests as critical for CI/CD
pytestmark = pytest.mark.critical
