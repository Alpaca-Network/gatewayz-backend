"""Cache module for storing model and provider data

DEPRECATED: This module is being phased out in favor of the unified Redis-based
caching system in src/services/model_catalog_cache.py.

COMPATIBILITY LAYER ACTIVE: This module now delegates to Redis-based cache system
while maintaining backward compatibility with existing code.

Migration path:
- Old: get_models_cache("openrouter")
- New: from src.services.model_catalog_cache import get_cached_gateway_catalog
       get_cached_gateway_catalog("openrouter")

This module will be removed in a future version.
"""

import logging
import warnings
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================================
# COMPATIBILITY LAYER: Wrapper class that delegates to Redis-based cache
# ============================================================================


class _CacheDict(dict):
    """
    Compatibility wrapper that makes cache dictionaries delegate to Redis.

    This allows existing code using _xxx_models_cache["data"] to work
    without changes, while actually using the new Redis-based cache system.

    Example:
        # OLD CODE (still works):
        if _openrouter_models_cache["data"] is not None:
            return _openrouter_models_cache["data"]

        # Internally delegates to:
        get_cached_gateway_catalog("openrouter")
    """

    def __init__(self, provider_slug: str):
        """Initialize cache wrapper for a specific provider.

        Args:
            provider_slug: Provider/gateway slug (e.g., "openrouter", "anthropic")
        """
        self.provider_slug = provider_slug
        self._deprecation_warned = False

        # Initialize with expected cache structure
        super().__init__(
            {
                "data": None,
                "timestamp": None,
                "ttl": 3600,
                "stale_ttl": 7200,
            }
        )

    def __getitem__(self, key: str) -> Any:
        """Get cache value - delegates to Redis for 'data' key."""
        if key == "data":
            # Log deprecation warning once per cache instance
            if not self._deprecation_warned:
                logger.debug(
                    f"DEPRECATION: Direct cache access for {self.provider_slug}. "
                    f"Use get_cached_gateway_catalog('{self.provider_slug}') instead."
                )
                self._deprecation_warned = True

            # Delegate to Redis-based cache
            try:
                from src.services.model_catalog_cache import get_cached_gateway_catalog

                cached_data = get_cached_gateway_catalog(self.provider_slug)

                # Update internal timestamp when data is retrieved
                if cached_data is not None:
                    super().__setitem__("timestamp", datetime.now(UTC))

                return cached_data
            except Exception as e:
                logger.error(f"Error fetching from Redis cache for {self.provider_slug}: {e}")
                # Fall back to in-memory value on error
                return super().__getitem__(key)

        # For other keys (timestamp, ttl, etc), use normal dict behavior
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Set cache value - delegates to Redis for 'data' key."""
        if key == "data":
            # Log deprecation warning
            logger.debug(
                f"DEPRECATION: Direct cache write for {self.provider_slug}. "
                f"Use set_cached_gateway_catalog('{self.provider_slug}', data) instead."
            )

            # Delegate to Redis-based cache
            try:
                from src.services.model_catalog_cache import cache_gateway_catalog

                if value is not None:
                    cache_gateway_catalog(self.provider_slug, value)
                    # Update internal timestamp
                    super().__setitem__("timestamp", datetime.now(UTC))
            except Exception as e:
                logger.error(f"Error writing to Redis cache for {self.provider_slug}: {e}")

            # Also update in-memory for fallback
            super().__setitem__(key, value)
        else:
            # For other keys, use normal dict behavior
            super().__setitem__(key, value)


# ============================================================================
# Cache dictionaries - Now using Redis-backed wrappers
# ============================================================================

# Dynamic cache registry: creates _CacheDict instances on demand.
_cache_instances: dict[str, _CacheDict] = {}


def _get_or_create_cache(slug: str) -> _CacheDict:
    """Return the _CacheDict for *slug*, creating it on first access."""
    if slug not in _cache_instances:
        _cache_instances[slug] = _CacheDict(slug)
    return _cache_instances[slug]


# Pre-created aliases for backward compatibility (scripts + legacy imports)
# OpenRouter (primary gateway)
_models_cache = _get_or_create_cache("openrouter")

# Multi-provider catalog (uses "all" as gateway slug)
_multi_provider_catalog_cache = _get_or_create_cache("all")

# Major provider caches
_featherless_models_cache = _get_or_create_cache("featherless")
_chutes_models_cache = _get_or_create_cache("chutes")
_groq_models_cache = _get_or_create_cache("groq")
_fireworks_models_cache = _get_or_create_cache("fireworks")
_together_models_cache = _get_or_create_cache("together")
_modelz_cache = _get_or_create_cache("modelz")

# Legacy caches (special handling needed)
_huggingface_cache = {"data": {}, "timestamp": None, "ttl": 3600, "stale_ttl": 7200}
_provider_cache = {"data": None, "timestamp": None, "ttl": 3600, "stale_ttl": 7200}


# DeepInfra and Portkey-based providers
_deepinfra_models_cache = _get_or_create_cache("deepinfra")
_cerebras_models_cache = _get_or_create_cache("cerebras")
_nebius_models_cache = _get_or_create_cache("nebius")
_xai_models_cache = _get_or_create_cache("xai")
_zai_models_cache = _get_or_create_cache("zai")
_novita_models_cache = _get_or_create_cache("novita")
_huggingface_models_cache = _get_or_create_cache("huggingface")
_aimo_models_cache = _get_or_create_cache("aimo")
_near_models_cache = _get_or_create_cache("near")
_fal_models_cache = _get_or_create_cache("fal")
_google_vertex_models_cache = _get_or_create_cache("google-vertex")

# Gateway and provider caches
_cloudflare_workers_ai_models_cache = _get_or_create_cache("cloudflare-workers-ai")
_clarifai_models_cache = _get_or_create_cache("clarifai")
_openai_models_cache = _get_or_create_cache("openai")
_anthropic_models_cache = _get_or_create_cache("anthropic")
_simplismart_models_cache = _get_or_create_cache("simplismart")
_sybil_models_cache = _get_or_create_cache("sybil")
_canopywave_models_cache = _get_or_create_cache("canopywave")
_morpheus_models_cache = _get_or_create_cache("morpheus")

# Special case: Alibaba cache with quota error tracking
# Keep as regular dict since it has special fields beyond standard cache structure
_alibaba_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,
    "stale_ttl": 7200,
    "quota_error": False,
    "quota_error_timestamp": None,
    "quota_error_backoff": 900,
}

# BACKWARD COMPATIBILITY: Alias for old cache name
# Some deployed modules may still reference the old name
_hug_models_cache = _huggingface_models_cache

# Error state cache for tracking failed gateway fetches
# Structure: {gateway: {"error": error_message, "timestamp": datetime, "failure_count": int}}
_gateway_error_cache = {}


# Cache access functions
def get_models_cache(gateway: str):
    """Get cache for a specific gateway

    DEPRECATED: Use src.services.model_catalog_cache.get_cached_gateway_catalog() instead.
    This function will be removed in a future version.
    """
    warnings.warn(
        "get_models_cache() is deprecated. Use get_cached_gateway_catalog() from "
        "src.services.model_catalog_cache instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    slug = gateway.lower()
    # Special cases
    if slug == "hug":
        slug = "huggingface"
    elif slug == "alibaba":
        return _alibaba_models_cache  # Special dict with quota tracking
    # Only return caches for known providers; return None for unknown slugs
    # to preserve backward-compat (callers guard with `if cache is None:`)
    if slug in _cache_instances:
        return _cache_instances[slug]
    return None


# ============================================================================
# Error state caching functions
# DEPRECATED: Error handling now automatic via circuit breaker in new cache
# ============================================================================


def set_gateway_error(gateway: str, error_message: str):
    """Cache error state for a gateway with exponential backoff

    DEPRECATED: Error state tracking is now handled automatically by the Redis-based
    cache system via circuit breaker pattern. This function is kept for compatibility
    but does nothing in the new system.

    Args:
        gateway: The gateway name (e.g., "fireworks", "deepinfra")
        error_message: The error message to cache
    """
    warnings.warn(
        f"set_gateway_error('{gateway}') is deprecated. "
        "Error tracking is now handled automatically by the cache layer.",
        DeprecationWarning,
        stacklevel=2,
    )

    # Keep the old behavior for backward compatibility with non-migrated code
    current_error = _gateway_error_cache.get(gateway)
    failure_count = 1

    if current_error:
        failure_count = current_error.get("failure_count", 0) + 1

    _gateway_error_cache[gateway] = {
        "error": error_message,
        "timestamp": datetime.now(UTC),
        "failure_count": failure_count,
    }

    logger.debug(
        f"[DEPRECATED] Cached error state for {gateway} (failure #{failure_count}): {error_message[:100]}"
    )


def get_gateway_error_ttl(failure_count: int) -> int:
    """Calculate TTL for error cache based on failure count (exponential backoff)

    Args:
        failure_count: Number of consecutive failures

    Returns:
        TTL in seconds
    """
    # Exponential backoff: 5 min, 15 min, 30 min, 1 hour, then cap at 1 hour
    if failure_count == 1:
        return 300  # 5 minutes
    elif failure_count == 2:
        return 900  # 15 minutes
    elif failure_count == 3:
        return 1800  # 30 minutes
    else:
        return 3600  # 1 hour (max)


def is_gateway_in_error_state(gateway: str) -> bool:
    """Check if a gateway is currently in error state

    DEPRECATED: Error state tracking is now handled automatically by the Redis-based
    cache system via circuit breaker pattern. This function is kept for compatibility.

    Args:
        gateway: The gateway name (e.g., "fireworks", "deepinfra")

    Returns:
        True if gateway is in error state and TTL hasn't expired, False otherwise
    """
    warnings.warn(
        f"is_gateway_in_error_state('{gateway}') is deprecated. "
        "Error tracking is now handled automatically by the cache layer.",
        DeprecationWarning,
        stacklevel=2,
    )

    # Keep old behavior for backward compatibility
    error_state = _gateway_error_cache.get(gateway)

    if not error_state:
        return False

    timestamp = error_state.get("timestamp")
    failure_count = error_state.get("failure_count", 1)

    if not timestamp:
        return False

    ttl = get_gateway_error_ttl(failure_count)
    age = (datetime.now(UTC) - timestamp).total_seconds()

    if age >= ttl:
        clear_gateway_error(gateway)
        return False

    return True


def clear_gateway_error(gateway: str):
    """Clear error state for a gateway (called after successful fetch)

    DEPRECATED: Error state tracking is now handled automatically by the Redis-based
    cache system via circuit breaker pattern. This function is kept for compatibility.

    Args:
        gateway: The gateway name (e.g., "fireworks", "deepinfra")
    """
    warnings.warn(
        f"clear_gateway_error('{gateway}') is deprecated. "
        "Error tracking is now handled automatically by the cache layer.",
        DeprecationWarning,
        stacklevel=2,
    )

    # Keep old behavior for backward compatibility
    if gateway in _gateway_error_cache:
        del _gateway_error_cache[gateway]
        logger.debug(f"[DEPRECATED] Cleared error state for {gateway}")


def get_gateway_error_message(gateway: str) -> str | None:
    """Get the cached error message for a gateway

    Args:
        gateway: The gateway name (e.g., "fireworks", "deepinfra")

    Returns:
        Error message if gateway is in error state, None otherwise
    """
    if is_gateway_in_error_state(gateway):
        error_state = _gateway_error_cache.get(gateway)
        if error_state:
            return error_state.get("error")
    return None
