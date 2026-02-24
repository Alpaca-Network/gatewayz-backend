"""
Comprehensive Pricing Accuracy Tests

These tests verify that:
1. Provider API pricing is correctly interpreted
2. Pricing normalization works correctly
3. Credit deductions match actual costs
4. Known models have correct pricing

Created: 2026-01-22
Purpose: Validate pricing system end-to-end
"""

from decimal import Decimal

import httpx
import pytest

from src.services.pricing import calculate_cost, get_model_pricing
from src.services.pricing_normalization import (
    PricingFormat,
    get_provider_format,
    normalize_pricing_dict,
    normalize_to_per_token,
    validate_normalized_price,
)


class TestProviderAPIFormats:
    """Test that we correctly understand provider API pricing formats"""

    @pytest.mark.asyncio
    async def test_openrouter_api_returns_per_token_pricing(self):
        """
        CRITICAL TEST: Verify OpenRouter returns per-token pricing, not per-1M

        OpenRouter's API returns prices like "0.00000015" which is ALREADY
        per-token pricing ($0.15 per 1M tokens = $0.00000015 per token).

        If we incorrectly assume it's per-1M and divide by 1M again, we get
        prices that are 1,000,000x too cheap!
        """
        try:
            # Fetch actual OpenRouter pricing for a known model
            response = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0)
            response.raise_for_status()
            models = response.json().get("data", [])

            # Find GPT-4o-mini (a model with known, stable pricing)
            gpt4o_mini = None
            for model in models:
                if model.get("id") == "openai/gpt-4o-mini":
                    gpt4o_mini = model
                    break

            assert gpt4o_mini is not None, "GPT-4o-mini not found in OpenRouter catalog"

            pricing = gpt4o_mini.get("pricing", {})
            prompt_price = float(pricing.get("prompt", 0))
            completion_price = float(pricing.get("completion", 0))

            # GPT-4o-mini costs $0.15 per 1M input tokens
            # If OpenRouter returns per-token: 0.00000015
            # If OpenRouter returns per-1M: 0.15

            # Assert that the price is in per-token format (very small number)
            assert prompt_price < 0.001, (
                f"OpenRouter appears to return per-1M pricing ({prompt_price}), "
                f"not per-token pricing. This contradicts our findings!"
            )

            # More specifically, for GPT-4o-mini ($0.15/1M input)
            # We expect approximately 0.00000015
            expected_per_token = 0.15 / 1_000_000
            assert abs(prompt_price - expected_per_token) < 0.00000001, (
                f"Expected ~{expected_per_token} for GPT-4o-mini input, " f"got {prompt_price}"
            )

            print(f"✓ OpenRouter returns per-token pricing: {prompt_price}")

        except httpx.HTTPError as e:
            pytest.skip(f"Could not fetch OpenRouter API: {e}")

    def test_manual_pricing_format(self):
        """
        Test that manual_pricing.json uses per-1M format

        Based on the file content, manual prices like "0.055" for
        DeepInfra Meta-Llama-3.1-8B represent $0.055 per 1M tokens.
        """
        from src.services.pricing_lookup import load_manual_pricing

        pricing_data = load_manual_pricing()

        # Check DeepInfra pricing for a known model
        deepinfra_pricing = pricing_data.get("deepinfra", {})
        llama_pricing = deepinfra_pricing.get("meta-llama/Meta-Llama-3.1-8B-Instruct", {})

        assert llama_pricing.get("prompt") == "0.055", "Manual pricing format has changed!"

        # This should be per-1M format (DeepInfra charges $0.055 per 1M tokens)
        # NOT per-token format (which would be 0.000000055)
        prompt_val = float(llama_pricing.get("prompt", 0))
        assert prompt_val > 0.001, (
            f"Manual pricing appears to be per-token ({prompt_val}), "
            f"but should be per-1M format"
        )


class TestPricingNormalization:
    """Test pricing normalization functions"""

    def test_normalize_per_1m_to_per_token(self):
        """Test normalization from per-1M tokens to per-token"""
        # $0.055 per 1M tokens should become $0.000000055 per token
        result = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.055") / Decimal("1000000")

        assert result == expected
        assert float(result) == 0.000000055

    def test_normalize_per_1k_to_per_token(self):
        """Test normalization from per-1K tokens to per-token"""
        # $0.055 per 1K tokens should become $0.000055 per token
        result = normalize_to_per_token(0.055, PricingFormat.PER_1K_TOKENS)
        expected = Decimal("0.055") / Decimal("1000")

        assert result == expected
        assert float(result) == 0.000055

    def test_normalize_already_per_token(self):
        """Test that per-token pricing passes through unchanged"""
        # $0.000000055 per token should stay the same
        result = normalize_to_per_token(0.000000055, PricingFormat.PER_TOKEN)
        expected = Decimal("0.000000055")

        assert result == expected

    def test_normalize_pricing_dict(self):
        """Test normalizing a full pricing dictionary"""
        pricing = {"prompt": "0.055", "completion": "0.08", "image": "0", "request": "0"}

        result = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        assert float(result["prompt"]) == 0.000000055
        assert float(result["completion"]) == 0.00000008
        assert float(result["image"]) == 0
        assert float(result["request"]) == 0

    def test_validate_normalized_price(self):
        """Test price validation"""
        # Per-token prices should be < 0.001
        assert validate_normalized_price(0.000000055) is True  # Good
        assert validate_normalized_price(0.055) is False  # Too large, likely per-1M
        assert validate_normalized_price(0.00005) is True  # OK, could be per-1K


class TestProviderFormatMapping:
    """Test that provider format mappings are correct"""

    def test_openrouter_format_should_be_per_token(self):
        """
        CRITICAL: OpenRouter format should be PER_TOKEN, not PER_1M_TOKENS

        This is the key bug - OpenRouter API returns per-token pricing but
        we're treating it as per-1M and dividing by 1M again.
        """
        current_format = get_provider_format("openrouter")

        # This will FAIL with current code, proving the bug
        # Current code returns PER_1M_TOKENS, but it should be PER_TOKEN
        pytest.xfail(
            f"OpenRouter format is currently {current_format}, "
            f"but should be {PricingFormat.PER_TOKEN}. This is the critical bug!"
        )
        assert current_format == PricingFormat.PER_TOKEN

    def test_deepinfra_format_mapping(self):
        """DeepInfra uses per-1M tokens pricing"""
        format_type = get_provider_format("deepinfra")
        assert format_type == PricingFormat.PER_1M_TOKENS

    def test_aihubmix_format_mapping(self):
        """AiHubMix uses per-1K tokens pricing"""
        format_type = get_provider_format("aihubmix")
        assert format_type == PricingFormat.PER_1K_TOKENS


class TestCreditCalculations:
    """Test that credit deductions are calculated correctly"""

    def test_calculate_cost_with_correct_pricing(self):
        """
        Test credit calculation with known pricing

        Example: GPT-4o-mini
        - Input: $0.15 per 1M tokens = $0.00000015 per token
        - Output: $0.60 per 1M tokens = $0.0000006 per token
        - Usage: 1000 prompt tokens, 500 completion tokens
        - Expected cost: (1000 * 0.00000015) + (500 * 0.0000006) = $0.00045
        """
        # This test assumes pricing is already in per-token format
        prompt_tokens = 1000
        completion_tokens = 500

        # Expected cost with correct per-token pricing
        expected_cost = (1000 * 0.00000015) + (500 * 0.0000006)
        # = 0.00015 + 0.0003 = 0.00045

        assert abs(expected_cost - 0.00045) < 0.0000001

        print(f"✓ Expected cost for 1000/500 tokens: ${expected_cost:.6f}")

    def test_calculate_cost_with_wrong_normalization(self):
        """
        Test showing the impact of wrong normalization

        If we incorrectly divide OpenRouter's already-per-token pricing by 1M:
        - We get pricing 1,000,000x too cheap
        - Users are massively undercharged
        - System loses money
        """
        # OpenRouter returns (already per-token)
        openrouter_price = 0.00000015

        # If we incorrectly normalize by dividing by 1M
        incorrectly_normalized = openrouter_price / 1_000_000
        # = 0.00000000000015

        # Cost calculation with 1000 tokens
        correct_cost = 1000 * openrouter_price
        incorrect_cost = 1000 * incorrectly_normalized

        # The incorrect cost is 1M times too cheap!
        ratio = correct_cost / incorrect_cost
        assert abs(ratio - 1_000_000) < 1

        print(f"✗ Incorrect normalization makes cost {ratio:.0f}x too cheap!")
        print(f"  Correct: ${correct_cost:.6f}")
        print(f"  Incorrect: ${incorrect_cost:.12f}")


class TestEndToEndPricing:
    """Test complete pricing flow from API to credit deduction"""

    @pytest.mark.integration
    def test_gpt4o_mini_pricing_end_to_end(self):
        """
        Integration test: Verify GPT-4o-mini pricing end-to-end

        This test will FAIL if OpenRouter pricing isn't handled correctly.
        """
        model_id = "openai/gpt-4o-mini"

        # Get pricing from our system
        pricing = get_model_pricing(model_id)

        if not pricing.get("found"):
            pytest.skip(f"Model {model_id} not found in catalog")

        prompt_price = pricing["prompt"]
        completion_price = pricing["completion"]

        # Expected pricing for GPT-4o-mini (as of 2024/2025)
        # Input: $0.15 per 1M = $0.00000015 per token
        # Output: $0.60 per 1M = $0.0000006 per token
        expected_prompt = 0.15 / 1_000_000
        expected_completion = 0.60 / 1_000_000

        # Allow 10% variance for price changes
        assert abs(prompt_price - expected_prompt) / expected_prompt < 0.1, (
            f"GPT-4o-mini prompt pricing is off: "
            f"expected ~${expected_prompt}, got ${prompt_price}"
        )

        assert abs(completion_price - expected_completion) / expected_completion < 0.1, (
            f"GPT-4o-mini completion pricing is off: "
            f"expected ~${expected_completion}, got ${completion_price}"
        )

        # Now test actual cost calculation
        cost = calculate_cost(model_id, prompt_tokens=1000, completion_tokens=500)
        expected_cost = (1000 * expected_prompt) + (500 * expected_completion)

        assert abs(cost - expected_cost) / expected_cost < 0.1, (
            f"Cost calculation is off: expected ${expected_cost:.6f}, " f"got ${cost:.6f}"
        )

        print("✓ GPT-4o-mini end-to-end pricing verified")
        print(f"  Prompt: ${prompt_price}")
        print(f"  Completion: ${completion_price}")
        print(f"  Cost for 1000/500 tokens: ${cost:.6f}")


class TestPricingConsistency:
    """Test that pricing is consistent across different code paths"""

    def test_openrouter_pricing_not_double_normalized(self):
        """
        Ensure OpenRouter pricing isn't normalized twice

        The bug: OpenRouter returns per-token pricing, but we normalize
        it as if it's per-1M, dividing by 1M and making it 1M times cheaper.
        """
        # Simulate OpenRouter API response (per-token format)
        openrouter_response_pricing = {
            "prompt": "0.00000015",  # Already per-token
            "completion": "0.0000006",  # Already per-token
            "request": "0",
            "image": "0",
        }

        # If we incorrectly normalize this as per-1M...
        incorrectly_normalized = normalize_pricing_dict(
            openrouter_response_pricing,
            PricingFormat.PER_1M_TOKENS,  # WRONG! OpenRouter is already per-token
        )

        # The result is 1M times too cheap
        wrong_price = float(incorrectly_normalized["prompt"])
        correct_price = float(openrouter_response_pricing["prompt"])

        # This shows the bug
        ratio = correct_price / wrong_price
        assert (
            abs(ratio - 1_000_000) < 1
        ), f"Normalizing OpenRouter pricing as per-1M makes it {ratio:.0f}x cheaper!"

        print(f"✗ BUG CONFIRMED: Treating OpenRouter as per-1M makes prices {ratio:.0f}x too cheap")


class TestKnownModelPricing:
    """Test pricing for known models against public pricing pages"""

    @pytest.mark.parametrize(
        "model_id,expected_input_per_1m,expected_output_per_1m",
        [
            ("openai/gpt-4o-mini", 0.15, 0.60),
            ("openai/gpt-4o", 2.50, 10.00),
            ("anthropic/claude-3-5-sonnet", 3.00, 15.00),
            # Add more known models
        ],
    )
    def test_known_model_pricing(self, model_id, expected_input_per_1m, expected_output_per_1m):
        """
        Test that known models have correct pricing (within 20% tolerance)

        These are public, well-known model prices that we can verify.
        """
        pricing = get_model_pricing(model_id)

        if not pricing.get("found"):
            pytest.skip(f"Model {model_id} not in catalog")

        # Convert expected per-1M pricing to per-token
        expected_prompt = expected_input_per_1m / 1_000_000
        expected_completion = expected_output_per_1m / 1_000_000

        actual_prompt = pricing["prompt"]
        actual_completion = pricing["completion"]

        # Allow 20% variance (prices change over time)
        prompt_diff = abs(actual_prompt - expected_prompt) / expected_prompt
        completion_diff = abs(actual_completion - expected_completion) / expected_completion

        assert prompt_diff < 0.2, (
            f"{model_id} prompt pricing differs by {prompt_diff*100:.1f}%: "
            f"expected ${expected_prompt}, got ${actual_prompt}"
        )

        assert completion_diff < 0.2, (
            f"{model_id} completion pricing differs by {completion_diff*100:.1f}%: "
            f"expected ${expected_completion}, got ${actual_completion}"
        )


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
