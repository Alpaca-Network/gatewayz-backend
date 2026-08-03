"""Tests for cache-aware cost calculation.

The property that matters commercially: a cache read must cost materially less
than the same token billed as fresh input. If these tests pass but the gateway
still bills cache reads at the input rate, the cost advantage the product is
sold on does not exist.
"""

from unittest.mock import patch

import pytest

from src.services.pricing.cache_pricing import (
    DEFAULT_CACHE_MULTIPLIERS,
    CacheMultipliers,
    calculate_cost_with_cache,
    extract_cache_tokens,
    resolve_cache_multipliers,
    split_prompt_tokens,
)

FAKE_PRICING = {"prompt": 0.000003, "completion": 0.000015, "found": True, "source": "db"}


class TestResolveCacheMultipliers:
    def test_explicit_provider_wins(self):
        assert resolve_cache_multipliers("some/model", "anthropic").read == 0.1

    def test_falls_back_to_model_id_match(self):
        assert resolve_cache_multipliers("anthropic/claude-sonnet-4", None).read == 0.1

    def test_longest_provider_key_wins(self):
        """'google-vertex' must not be shadowed by the shorter 'google' key."""
        result = resolve_cache_multipliers("google-vertex/gemini-2.5-pro", None)
        assert result == resolve_cache_multipliers("x", "google-vertex")

    def test_unknown_provider_gets_conservative_default(self):
        result = resolve_cache_multipliers("mystery/model-1", None)
        assert result == DEFAULT_CACHE_MULTIPLIERS
        # Conservative means "no discount" so we never under-bill.
        assert result.read == 1.0

    def test_anthropic_write_is_a_premium_not_a_discount(self):
        assert resolve_cache_multipliers("x", "anthropic").write > 1.0


class TestSplitPromptTokens:
    def test_inclusive_total_splits_correctly(self):
        assert split_prompt_tokens(1000, cache_read_tokens=800, cache_write_tokens=0) == (
            200,
            0,
            800,
        )

    def test_exclusive_total_does_not_produce_negative_uncached(self):
        """Some providers report prompt_tokens excluding cached tokens."""
        uncached, write, read = split_prompt_tokens(
            200, cache_read_tokens=800, cache_write_tokens=0
        )
        assert uncached == 0
        assert read == 800

    def test_negative_inputs_are_clamped(self):
        assert split_prompt_tokens(100, -5, -5) == (100, 0, 0)

    def test_all_three_classes(self):
        assert split_prompt_tokens(1000, cache_read_tokens=600, cache_write_tokens=300) == (
            100,
            300,
            600,
        )


class TestExtractCacheTokens:
    def test_anthropic_spelling(self):
        usage = {"cache_read_input_tokens": 500, "cache_creation_input_tokens": 120}
        assert extract_cache_tokens(usage) == (500, 120)

    def test_openai_nested_spelling(self):
        usage = {"prompt_tokens_details": {"cached_tokens": 300}}
        assert extract_cache_tokens(usage) == (300, 0)

    def test_flat_cached_tokens_spelling(self):
        assert extract_cache_tokens({"cached_tokens": 42}) == (42, 0)

    def test_none_usage(self):
        assert extract_cache_tokens(None) == (0, 0)

    def test_no_cache_fields(self):
        assert extract_cache_tokens({"prompt_tokens": 10}) == (0, 0)

    def test_object_style_usage(self):
        class Usage:
            cache_read_input_tokens = 7
            cache_creation_input_tokens = 3

        assert extract_cache_tokens(Usage()) == (7, 3)

    def test_non_numeric_values_do_not_raise(self):
        assert extract_cache_tokens({"cache_read_input_tokens": "lots"}) == (0, 0)


class TestCalculateCostWithCache:
    def test_no_cache_tokens_delegates_to_plain_calculation(self):
        with patch("src.services.pricing.pricing.calculate_cost", return_value=0.05) as mock_calc:
            result = calculate_cost_with_cache("anthropic/claude-sonnet-4", 1000, 100)
        assert result["total_cost"] == 0.05
        mock_calc.assert_called_once()

    def test_cache_read_is_cheaper_than_uncached_input(self):
        """The core commercial property."""
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            cached = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=10_000,
                completion_tokens=100,
                cache_read_tokens=9_000,
                provider="anthropic",
            )
            uncached = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=10_000,
                completion_tokens=100,
                cache_read_tokens=0,
                provider="anthropic",
            )
        assert cached["total_cost"] < uncached["total_cost"]

    def test_cache_savings_is_reported_and_positive(self):
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            result = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=10_000,
                completion_tokens=100,
                cache_read_tokens=9_000,
                provider="anthropic",
            )
        assert result["cache_savings"] > 0

    def test_anthropic_cache_read_priced_at_one_tenth(self):
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            result = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=1_000,
                completion_tokens=0,
                cache_read_tokens=1_000,
                provider="anthropic",
            )
        # 1000 tokens * 0.000003 * 0.1 = 0.0003, before markup.
        expected_before_markup = 1_000 * FAKE_PRICING["prompt"] * 0.1
        assert result["cache_read_cost"] == pytest.approx(expected_before_markup, rel=0.5)

    def test_cache_write_costs_more_than_plain_input_for_anthropic(self):
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            write = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=1_000,
                completion_tokens=0,
                cache_write_tokens=1_000,
                provider="anthropic",
            )
        plain = 1_000 * FAKE_PRICING["prompt"]
        assert write["cache_write_cost"] > plain

    def test_breakdown_components_sum_to_total(self):
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            result = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=5_000,
                completion_tokens=500,
                cache_read_tokens=3_000,
                cache_write_tokens=1_000,
                provider="anthropic",
            )
        assert result["input_cost"] + result["output_cost"] == pytest.approx(
            result["total_cost"], rel=1e-6
        )

    def test_unknown_provider_charges_full_rate_for_cache_reads(self):
        """Never under-bill a provider whose cache economics we do not know."""
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            result = calculate_cost_with_cache(
                "mystery/model",
                prompt_tokens=1_000,
                completion_tokens=0,
                cache_read_tokens=1_000,
                provider="mystery",
            )
        assert result["cache_savings"] == pytest.approx(0.0, abs=1e-9)

    def test_pricing_lookup_failure_falls_back_without_raising(self):
        with (
            patch(
                "src.services.pricing.pricing.get_model_pricing",
                side_effect=RuntimeError("db down"),
            ),
            patch("src.services.pricing.pricing.calculate_cost", return_value=0.02),
        ):
            result = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4", 1_000, 100, cache_read_tokens=500
            )
        assert result["total_cost"] == 0.02

    def test_token_counts_are_echoed_for_observability(self):
        with patch("src.services.pricing.pricing.get_model_pricing", return_value=FAKE_PRICING):
            result = calculate_cost_with_cache(
                "anthropic/claude-sonnet-4",
                prompt_tokens=5_000,
                completion_tokens=0,
                cache_read_tokens=3_000,
                cache_write_tokens=1_000,
                provider="anthropic",
            )
        assert result["cache_read_tokens"] == 3_000
        assert result["cache_write_tokens"] == 1_000
        assert result["uncached_prompt_tokens"] == 1_000


class TestCacheMultipliersDataclass:
    def test_is_immutable(self):
        m = CacheMultipliers(read=0.1, write=1.25)
        with pytest.raises(Exception):
            m.read = 0.5
