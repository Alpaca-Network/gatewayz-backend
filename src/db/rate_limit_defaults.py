"""Default per-API-key rate limits (rate_limit_configs rows).

No imports from src.db: api_keys and rate_limits both use this module.
"""

# Per-API-key defaults, stored in rate_limit_configs (per hour, sliding window).
# The original 1,000 requests / 1M tokens per hour meant ~16 requests and
# ~16.7k tokens a minute for every customer: one long agent request could
# exhaust a minute, and a single user hit 429 after a handful of calls
# (production, 2026-10-27). Raised so the limit protects the service from
# abuse rather than throttling normal use.
DEFAULT_KEY_MAX_REQUESTS_PER_HOUR = 36_000  # 600 / minute
DEFAULT_KEY_MAX_TOKENS_PER_HOUR = 50_000_000
DEFAULT_KEY_BURST_LIMIT = 600
DEFAULT_KEY_CONCURRENCY_LIMIT = 50


def default_rate_limit_config(api_key_id: int, max_requests: int | None = None) -> dict:
    """The rate_limit_configs row a new API key starts with."""
    return {
        "api_key_id": api_key_id,
        "window_type": "sliding",
        "window_size": 3600,
        "max_requests": max_requests or DEFAULT_KEY_MAX_REQUESTS_PER_HOUR,
        "max_tokens": DEFAULT_KEY_MAX_TOKENS_PER_HOUR,
        "burst_limit": DEFAULT_KEY_BURST_LIMIT,
        "concurrency_limit": DEFAULT_KEY_CONCURRENCY_LIMIT,
        "is_active": True,
    }
