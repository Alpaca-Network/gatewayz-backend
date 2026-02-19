"""
Utilities for converting rate limit results to HTTP headers.

Emits both IETF draft standard headers (RateLimit-*) and legacy vendor
headers (X-RateLimit-*) so clients can rely on either convention.

IETF draft standard (draft-ietf-httpapi-ratelimit-headers):
  RateLimit-Limit     - total allowed requests in the window
  RateLimit-Remaining - remaining requests in the current window
  RateLimit-Reset     - seconds until the window resets

Legacy / vendor headers kept for backwards compatibility:
  X-RateLimit-Limit-Requests
  X-RateLimit-Remaining-Requests
  X-RateLimit-Reset-Requests     (Unix timestamp)
  X-RateLimit-Limit-Tokens
  X-RateLimit-Remaining-Tokens
  X-RateLimit-Reset-Tokens       (Unix timestamp)
  X-RateLimit-Burst-Window
"""

import time
from typing import Any


def get_rate_limit_headers(rate_limit_result: Any) -> dict[str, str]:
    """Convert a RateLimitResult into HTTP headers for the response.

    Returns a dictionary containing both IETF standard and legacy headers, e.g.:
    {
        # IETF draft standard
        "RateLimit-Limit": "250",
        "RateLimit-Remaining": "249",
        "RateLimit-Reset": "42",          # seconds until window resets
        # Legacy X-RateLimit-* (backwards compatible)
        "X-RateLimit-Limit-Requests": "250",
        "X-RateLimit-Remaining-Requests": "249",
        "X-RateLimit-Reset-Requests": "1700000042",
        "X-RateLimit-Limit-Tokens": "10000",
        "X-RateLimit-Remaining-Tokens": "9900",
        "X-RateLimit-Reset-Tokens": "1700000042",
        "X-RateLimit-Burst-Window": "100 per 60 seconds"
    }
    """
    headers: dict[str, str] = {}

    if not rate_limit_result:
        return headers

    now = int(time.time())

    # --- Safely read attributes with defaults ---
    limit_requests = getattr(rate_limit_result, "ratelimit_limit_requests", 0)
    remaining_requests = getattr(rate_limit_result, "remaining_requests", -1)
    reset_requests = getattr(rate_limit_result, "ratelimit_reset_requests", 0)

    limit_tokens = getattr(rate_limit_result, "ratelimit_limit_tokens", 0)
    remaining_tokens = getattr(rate_limit_result, "remaining_tokens", -1)
    reset_tokens = getattr(rate_limit_result, "ratelimit_reset_tokens", 0)

    burst_window = getattr(rate_limit_result, "burst_window_description", "")

    # --- IETF draft standard headers ---
    # Use the requests dimension as the primary "RateLimit-*" values since
    # those map most naturally to the single-dimension IETF model.
    if limit_requests > 0:
        headers["RateLimit-Limit"] = str(limit_requests)
    if remaining_requests >= 0:
        headers["RateLimit-Remaining"] = str(remaining_requests)
    if reset_requests > 0:
        # RateLimit-Reset must be seconds-until-reset (delta), not a Unix timestamp
        seconds_until_reset = max(0, reset_requests - now)
        headers["RateLimit-Reset"] = str(seconds_until_reset)

    # --- Legacy X-RateLimit-* headers (kept for backwards compatibility) ---
    if limit_requests > 0:
        headers["X-RateLimit-Limit-Requests"] = str(limit_requests)
    if remaining_requests >= 0:
        headers["X-RateLimit-Remaining-Requests"] = str(remaining_requests)
    if reset_requests > 0:
        headers["X-RateLimit-Reset-Requests"] = str(reset_requests)

    if limit_tokens > 0:
        headers["X-RateLimit-Limit-Tokens"] = str(limit_tokens)
    if remaining_tokens >= 0:
        headers["X-RateLimit-Remaining-Tokens"] = str(remaining_tokens)
    if reset_tokens > 0:
        headers["X-RateLimit-Reset-Tokens"] = str(reset_tokens)

    if burst_window:
        headers["X-RateLimit-Burst-Window"] = burst_window

    return headers
