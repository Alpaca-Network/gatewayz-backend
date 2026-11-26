"""One-hour cache writes are priced at the one-hour rate
(src/services/pricing/cache_pricing.py).

Anthropic: a five-minute cache write costs 1.25x input, a one-hour cache write
2x input, a cache read 0.1x. The usage breakdown
(cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens) is used
when present; otherwise a request that set cache_control.ttl == "1h" has every
cache write priced at the one-hour rate.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.pricing.cache_pricing import (
    cache_write_split,
    calculate_cost_with_cache,
    resolve_cache_multipliers,
)

RATE = 0.000003
PRICING = {"prompt": RATE, "completion": 0.0, "source": "test"}


def _cost(write, write_1h=0, provider="anthropic"):
    with (
        patch("src.services.pricing.pricing.get_model_pricing", return_value=PRICING),
        patch("src.config.config.Config.PRICING_MARKUP", 1.0),
    ):
        return calculate_cost_with_cache(
            "anthropic/claude-sonnet-4",
            prompt_tokens=write,
            completion_tokens=0,
            cache_write_tokens=write,
            provider=provider,
            cache_write_1h_tokens=write_1h,
        )


def test_anthropic_one_hour_write_multiplier_is_two():
    m = resolve_cache_multipliers("x", "anthropic")
    assert (m.read, m.write, m.one_hour_write) == (0.1, 1.25, 2.0)


def test_one_hour_tokens_cost_twice_input_and_five_minute_tokens_one_and_a_quarter():
    assert _cost(1000)["cache_write_cost"] == pytest.approx(1000 * RATE * 1.25)
    assert _cost(1000, 1000)["cache_write_cost"] == pytest.approx(1000 * RATE * 2.0)
    mixed = _cost(1000, 400)["cache_write_cost"]
    assert mixed == pytest.approx(600 * RATE * 1.25 + 400 * RATE * 2.0)


def test_providers_without_a_one_hour_rate_keep_their_write_rate():
    assert _cost(1000, 1000, provider="openai")["cache_write_cost"] == pytest.approx(1000 * RATE)


def test_the_usage_breakdown_decides_the_one_hour_part():
    usage = {
        "cache_creation_input_tokens": 700,
        "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 400},
    }
    assert cache_write_split(usage, 700) == (700, 400)
    # The breakdown wins over the request hint.
    assert cache_write_split(usage, 700, one_hour_hint=True) == (700, 400)


def test_without_a_breakdown_the_ttl_hint_prices_the_total_at_the_one_hour_rate():
    usage = {"cache_creation_input_tokens": 500}
    assert cache_write_split(usage, 500) == (500, 0)
    assert cache_write_split(usage, 500, one_hour_hint=True) == (500, 500)


def test_native_stream_usage_carries_the_breakdown():
    from src.services.providers.anthropic_native_client import anthropic_response_to_openai

    data = {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 30},
        },
    }
    usage = anthropic_response_to_openai(data, "claude")["usage"]
    assert cache_write_split(usage, usage["cache_creation_input_tokens"]) == (50, 30)
