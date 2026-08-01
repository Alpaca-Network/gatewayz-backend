"""Cache-aware cost calculation.

Background
----------
``calculate_cost`` prices a request with exactly two token classes, input and
output. Providers that support prompt caching bill three:

* **uncached input** -- full input rate
* **cache write** (the turn that populates the cache) -- a premium on the input
  rate, because the provider has to store the prefix
* **cache read** (every subsequent turn that hits it) -- a steep discount

Without this split, a cache read is billed at the full input rate. For a coding
agent replaying a large static prefix on every turn -- the workload the gateway
is positioned for -- that is the difference between being cheaper than going
direct to the provider and being roughly an order of magnitude more expensive
on the dominant token class.

Multipliers
-----------
Providers publish cache rates as multiples of their base input rate rather than
as absolute per-token prices, and those multiples are stable across models
within a provider. Encoding them as multipliers means a new Claude or GPT model
gets correct cache pricing the moment its base rate lands in the catalog, with
no separate backfill.

Per-model overrides still win when the catalog supplies explicit
``cache_read_price`` / ``cache_write_price`` values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheMultipliers:
    """Cache rates expressed as multiples of the base input (prompt) rate."""

    read: float
    write: float


# Provider defaults. Keys are matched against the model ID and the resolved
# provider slug, longest match first.
#
# anthropic: cache reads are 0.1x base input; 5-minute cache writes are 1.25x.
# openai:    cached input is billed at 0.5x; there is no separate write charge,
#            so the write multiplier is 1.0 (write turns cost normal input).
# google:    implicit/explicit context caching lands at ~0.25x reads.
# deepseek:  context caching reads are ~0.1x.
PROVIDER_CACHE_MULTIPLIERS: dict[str, CacheMultipliers] = {
    "anthropic": CacheMultipliers(read=0.1, write=1.25),
    "openai": CacheMultipliers(read=0.5, write=1.0),
    "google": CacheMultipliers(read=0.25, write=1.0),
    "google-vertex": CacheMultipliers(read=0.25, write=1.0),
    "deepseek": CacheMultipliers(read=0.1, write=1.0),
}

# Used when a provider supplies cache token counts but we have no published
# multipliers for it. Deliberately conservative: assume the discount is modest
# so we never under-bill a provider we do not understand.
DEFAULT_CACHE_MULTIPLIERS = CacheMultipliers(read=1.0, write=1.0)


def resolve_cache_multipliers(
    model_id: str,
    provider: str | None = None,
) -> CacheMultipliers:
    """Pick the cache multipliers for a model.

    Matches on the explicit provider slug first, then on any provider name
    appearing in the model ID (catalogue IDs are conventionally
    ``provider/model``).
    """
    if provider:
        hit = PROVIDER_CACHE_MULTIPLIERS.get(provider.strip().lower())
        if hit:
            return hit

    lowered = (model_id or "").lower()
    # Longest key first so "google-vertex" wins over "google".
    for key in sorted(PROVIDER_CACHE_MULTIPLIERS, key=len, reverse=True):
        if key in lowered:
            return PROVIDER_CACHE_MULTIPLIERS[key]

    logger.debug(
        "No cache multipliers known for model '%s' (provider=%s); billing cache "
        "tokens at the full input rate",
        model_id,
        provider,
    )
    return DEFAULT_CACHE_MULTIPLIERS


def split_prompt_tokens(
    prompt_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> tuple[int, int, int]:
    """Split a total prompt-token count into (uncached, write, read).

    ``prompt_tokens`` is expected to be the inclusive total. Providers vary in
    whether they report it inclusive or exclusive of cached tokens, so this
    clamps rather than trusting the arithmetic: a negative uncached remainder
    means the caller passed an exclusive total, in which case the remainder is
    simply zero and no tokens are double-billed.
    """
    cache_read = max(0, cache_read_tokens or 0)
    cache_write = max(0, cache_write_tokens or 0)
    uncached = max(0, (prompt_tokens or 0) - cache_read - cache_write)
    return uncached, cache_write, cache_read


def calculate_cost_with_cache(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    provider: str | None = None,
) -> dict[str, float]:
    """Cost a completion, pricing cached input at its own rate.

    Returns a breakdown rather than a bare float so callers can persist the
    components -- the per-request cache hit rate is the metric that proves the
    cost advantage, and it is not recoverable from a total.

    Falls back to the plain two-class calculation when no cache tokens are
    present, so existing behaviour is untouched for non-caching requests.
    """
    from src.services.pricing.pricing import calculate_cost, get_model_pricing

    cache_read = max(0, cache_read_tokens or 0)
    cache_write = max(0, cache_write_tokens or 0)

    if not cache_read and not cache_write:
        total = calculate_cost(model_id, prompt_tokens, completion_tokens)
        return {
            "total_cost": total,
            "input_cost": total,
            "output_cost": 0.0,
            "cache_read_cost": 0.0,
            "cache_write_cost": 0.0,
            "cache_savings": 0.0,
            "uncached_prompt_tokens": prompt_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    try:
        pricing = get_model_pricing(model_id)
        prompt_rate = float(pricing.get("prompt", 0.0))
        completion_rate = float(pricing.get("completion", 0.0))
    except Exception as e:
        logger.warning("Cache pricing lookup failed for %s: %s", model_id, e)
        total = calculate_cost(model_id, prompt_tokens, completion_tokens)
        return {
            "total_cost": total,
            "input_cost": total,
            "output_cost": 0.0,
            "cache_read_cost": 0.0,
            "cache_write_cost": 0.0,
            "cache_savings": 0.0,
            "uncached_prompt_tokens": prompt_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
        }

    multipliers = resolve_cache_multipliers(model_id, provider)

    # Explicit per-model rates from the catalog win over provider multipliers.
    read_rate = float(pricing.get("cache_read") or prompt_rate * multipliers.read)
    write_rate = float(pricing.get("cache_write") or prompt_rate * multipliers.write)

    uncached, cache_write_n, cache_read_n = split_prompt_tokens(
        prompt_tokens, cache_read, cache_write
    )

    uncached_cost = uncached * prompt_rate
    cache_read_cost = cache_read_n * read_rate
    cache_write_cost = cache_write_n * write_rate
    output_cost = completion_tokens * completion_rate

    subtotal = uncached_cost + cache_read_cost + cache_write_cost + output_cost

    from src.config.config import Config

    markup = Config.PRICING_MARKUP
    total = subtotal * markup

    # What the same request would have cost with every input token at the full
    # rate. This is the number the cost-advantage claim rests on, so it is
    # recorded rather than recomputed later from incomplete data.
    uncached_equivalent = (
        (uncached + cache_write_n + cache_read_n) * prompt_rate + output_cost
    ) * markup

    logger.info(
        "Cache-aware cost for %s: %d uncached + %d write + %d read prompt tokens, "
        "%d completion = $%.6f (saved $%.6f vs uncached)",
        model_id,
        uncached,
        cache_write_n,
        cache_read_n,
        completion_tokens,
        total,
        max(0.0, uncached_equivalent - total),
    )

    return {
        "total_cost": total,
        "input_cost": (uncached_cost + cache_read_cost + cache_write_cost) * markup,
        "output_cost": output_cost * markup,
        "cache_read_cost": cache_read_cost * markup,
        "cache_write_cost": cache_write_cost * markup,
        "cache_savings": max(0.0, uncached_equivalent - total),
        "uncached_prompt_tokens": uncached,
        "cache_read_tokens": cache_read_n,
        "cache_write_tokens": cache_write_n,
    }


def extract_cache_tokens(usage: dict | object | None) -> tuple[int, int]:
    """Pull ``(cache_read_tokens, cache_write_tokens)`` out of a usage object.

    Handles the three spellings in circulation: Anthropic's
    ``cache_read_input_tokens`` / ``cache_creation_input_tokens``, OpenAI's
    nested ``prompt_tokens_details.cached_tokens``, and the flat
    ``cached_tokens`` some OpenAI-compatible providers emit.
    """
    if usage is None:
        return 0, 0

    def _get(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    read = _get(usage, "cache_read_input_tokens") or 0
    write = _get(usage, "cache_creation_input_tokens") or 0

    if not read:
        details = _get(usage, "prompt_tokens_details")
        if details is not None:
            read = _get(details, "cached_tokens") or 0
    if not read:
        read = _get(usage, "cached_tokens") or 0

    try:
        return int(read or 0), int(write or 0)
    except (TypeError, ValueError):
        return 0, 0
