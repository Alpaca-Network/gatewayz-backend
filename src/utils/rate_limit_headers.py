"""
Utilities for converting rate limit results to HTTP headers
"""

from typing import Dict, Optional
from src.services.rate_limiting import RateLimitResult as PrimaryRateLimitResult


def get_rate_limit_headers(rate_limit_result: "PrimaryRateLimitResult") -> Dict[str, str]:
    """Convert a RateLimitResult into HTTP headers for the response.

    Returns a dictionary of HTTP headers like:
    {
        "X-RateLimit-Limit-Requests": "250",
        "X-RateLimit-Remaining-Requests": "249",
        "X-RateLimit-Reset-Requests": "1700000000",
        "X-RateLimit-Limit-Tokens": "10000",
        "X-RateLimit-Remaining-Tokens": "9900",
        "X-RateLimit-Reset-Tokens": "1700000000",
        "X-RateLimit-Burst-Window": "100 per 60 seconds"
    }
    """
    headers = {}

    if rate_limit_result.ratelimit_limit_requests > 0:
        headers["X-RateLimit-Limit-Requests"] = str(rate_limit_result.ratelimit_limit_requests)

    if rate_limit_result.remaining_requests >= 0:
        headers["X-RateLimit-Remaining-Requests"] = str(rate_limit_result.remaining_requests)

    if rate_limit_result.ratelimit_reset_requests > 0:
        headers["X-RateLimit-Reset-Requests"] = str(rate_limit_result.ratelimit_reset_requests)

    if rate_limit_result.ratelimit_limit_tokens > 0:
        headers["X-RateLimit-Limit-Tokens"] = str(rate_limit_result.ratelimit_limit_tokens)

    if rate_limit_result.remaining_tokens >= 0:
        headers["X-RateLimit-Remaining-Tokens"] = str(rate_limit_result.remaining_tokens)

    if rate_limit_result.ratelimit_reset_tokens > 0:
        headers["X-RateLimit-Reset-Tokens"] = str(rate_limit_result.ratelimit_reset_tokens)

    if rate_limit_result.burst_window_description:
        headers["X-RateLimit-Burst-Window"] = rate_limit_result.burst_window_description

    return headers
