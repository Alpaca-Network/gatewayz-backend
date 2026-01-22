"""
Butter.dev LLM Response Caching Client

This module provides integration with Butter.dev, a caching proxy for LLM APIs
that identifies patterns in responses and serves cached responses to reduce
costs and improve latency.

Key Features:
- Eligibility checking for caching based on user preferences and provider compatibility
- Pooled client creation for routing through Butter.dev proxy
- Cache hit detection and metrics tracking
- Automatic fallback support on errors

See: https://butter.dev
"""

import logging
import time
from typing import Any

from src.config.config import Config

logger = logging.getLogger(__name__)

# Providers that are compatible with Butter.dev (OpenAI-compatible API format)
BUTTER_COMPATIBLE_PROVIDERS: set[str] = {
    # Primary aggregators
    "openrouter",
    "featherless",
    "together",
    "fireworks",
    "deepinfra",
    "groq",
    "cerebras",
    # Direct providers with OpenAI-compatible APIs
    "openai",
    "xai",
    "perplexity",
    "huggingface",
    "chutes",
    # Additional compatible gateways
    "near",
    "aihubmix",
    "morpheus",
    "simplismart",
    "sybil",
    "nosana",
    "cloudflare-workers-ai",
    "akash",
    "onerouter",
    "anannas",
    "alpaca-network",
    "vercel-ai-gateway",
    "helicone",
    "aimo",
}

# Providers that should NOT use caching (non-deterministic, real-time, etc.)
BUTTER_EXCLUDED_PROVIDERS: set[str] = {
    # Google uses different API format
    "google",
    "google-vertex",
    # Anthropic native API is not OpenAI-compatible
    "anthropic",
    # Image generation should not be cached
    "fal",
    "stability",
    # Voice/audio providers
    "resemble",
    "elevenlabs",
}


def is_provider_butter_compatible(provider: str) -> bool:
    """
    Check if a provider is compatible with Butter.dev caching.

    Args:
        provider: Provider slug (e.g., 'openrouter', 'fireworks')

    Returns:
        True if the provider can be routed through Butter.dev
    """
    provider_lower = provider.lower()

    # Check explicit exclusions first
    if provider_lower in BUTTER_EXCLUDED_PROVIDERS:
        return False

    # Check explicit inclusions
    if provider_lower in BUTTER_COMPATIBLE_PROVIDERS:
        return True

    # Default to not compatible for unknown providers
    return False


def should_use_butter_cache(
    user: dict[str, Any] | None,
    provider: str,
    model: str | None = None,
) -> tuple[bool, str]:
    """
    Determine if a request should be routed through Butter.dev caching.

    Args:
        user: User dict with preferences (or None for anonymous)
        provider: Provider slug for the request
        model: Optional model name (for future model-specific logic)

    Returns:
        Tuple of (should_use_cache, reason_code)
        - should_use_cache: True if the request should go through Butter.dev
        - reason_code: String explaining the decision (for logging/debugging)
    """
    # System-wide killswitch
    if not Config.BUTTER_DEV_ENABLED:
        return False, "system_disabled"

    # Anonymous users don't have preferences
    if not user:
        return False, "anonymous_user"

    # Check user preference
    preferences = user.get("preferences") or {}
    enable_cache = preferences.get("enable_butter_cache", False)

    if not enable_cache:
        return False, "user_preference_disabled"

    # Check provider compatibility
    if not is_provider_butter_compatible(provider):
        return False, f"provider_incompatible:{provider}"

    # Future: Add model-specific exclusions here
    # Some models (e.g., creative writing, image generation prompts) may not
    # benefit from caching due to desired response variety

    return True, "enabled"


def get_user_cache_preference(user: dict[str, Any] | None) -> bool:
    """
    Get the user's Butter.dev cache preference.

    Args:
        user: User dict with preferences (or None)

    Returns:
        True if user has enabled caching, False otherwise
    """
    if not user:
        return False

    preferences = user.get("preferences") or {}
    return preferences.get("enable_butter_cache", False)


def detect_cache_hit(response_time_seconds: float, threshold: float = 0.5) -> bool:
    """
    Heuristically detect if a response was a cache hit based on latency.

    Butter.dev cache hits typically respond in <100ms, while cache misses
    go to the actual provider (typically 1-5+ seconds).

    Args:
        response_time_seconds: Time taken for the response
        threshold: Maximum time (seconds) to consider as cache hit (default: 0.5s)

    Returns:
        True if the response time suggests a cache hit

    Note:
        This is a heuristic. Butter.dev may add headers in the future to
        explicitly indicate cache hits, which would be more reliable.
    """
    return response_time_seconds < threshold


class ButterCacheTimer:
    """
    Context manager for timing Butter.dev requests and detecting cache hits.

    Usage:
        with ButterCacheTimer() as timer:
            response = await make_butter_request(...)

        if timer.is_cache_hit:
            # Handle cache hit (cost = $0)

        logger.info(f"Butter request took {timer.elapsed_seconds:.3f}s (hit={timer.is_cache_hit})")
    """

    def __init__(self, hit_threshold: float = 0.5):
        """
        Initialize the timer.

        Args:
            hit_threshold: Maximum response time (seconds) to consider as cache hit
        """
        self.hit_threshold = hit_threshold
        self.start_time: float | None = None
        self.end_time: float | None = None

    def __enter__(self) -> "ButterCacheTimer":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.end_time = time.perf_counter()

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        if self.start_time is None:
            return 0.0
        end = self.end_time or time.perf_counter()
        return end - self.start_time

    @property
    def elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return self.elapsed_seconds * 1000

    @property
    def is_cache_hit(self) -> bool:
        """Determine if this was likely a cache hit based on response time."""
        return detect_cache_hit(self.elapsed_seconds, self.hit_threshold)


def get_butter_request_metadata(
    is_cache_hit: bool,
    actual_cost_usd: float,
    response_time_ms: float,
    provider: str,
) -> dict[str, Any]:
    """
    Build metadata dict for storing with chat_completion_requests.

    Args:
        is_cache_hit: Whether this was a cache hit
        actual_cost_usd: Cost that would have been charged without caching
        response_time_ms: Response time in milliseconds
        provider: Provider that was proxied through Butter.dev

    Returns:
        Metadata dict to store with the request
    """
    metadata = {
        "butter_cache_hit": is_cache_hit,
        "butter_provider": provider,
        "butter_response_time_ms": round(response_time_ms, 2),
    }

    if is_cache_hit:
        # Track the actual cost for savings calculation
        metadata["actual_cost_usd"] = round(actual_cost_usd, 6)

    return metadata


def log_butter_cache_result(
    provider: str,
    model: str,
    is_cache_hit: bool,
    response_time_ms: float,
    savings_usd: float = 0.0,
) -> None:
    """
    Log Butter.dev cache result for debugging and monitoring.

    Args:
        provider: Provider slug
        model: Model name
        is_cache_hit: Whether this was a cache hit
        response_time_ms: Response time in milliseconds
        savings_usd: Cost saved from cache hit
    """
    if is_cache_hit:
        logger.info(
            f"Butter.dev cache HIT: provider={provider}, model={model}, "
            f"latency={response_time_ms:.1f}ms, savings=${savings_usd:.6f}"
        )
    else:
        logger.debug(
            f"Butter.dev cache MISS: provider={provider}, model={model}, "
            f"latency={response_time_ms:.1f}ms"
        )
