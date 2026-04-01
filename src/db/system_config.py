import logging
import time
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

# In-memory cache for system config values
_config_cache: dict[str, Any] = {}
_config_cache_timestamp: float = 0.0
_CONFIG_CACHE_TTL = 300  # 5 minutes


def load_all_config() -> dict[str, Any]:
    """Bulk load all active system config rows into the in-memory cache.

    Returns a dict mapping config keys to their (already-deserialized) JSONB values.
    """
    global _config_cache, _config_cache_timestamp

    try:
        client = get_supabase_client()
        result = client.table("system_config").select("key, value").eq("is_active", True).execute()

        new_cache: dict[str, Any] = {}
        for row in result.data or []:
            new_cache[row["key"]] = row["value"]

        _config_cache = new_cache
        _config_cache_timestamp = time.monotonic()
        logger.info(f"System config cache loaded: {len(_config_cache)} values")
        return dict(_config_cache)

    except Exception as e:
        logger.error(f"Failed to load system config: {e}")
        # Return existing cache on failure
        return dict(_config_cache)


def get_config(key: str, default: Any = None) -> Any:
    """Get a single config value by key.

    Uses the in-memory cache with TTL-based refresh. Returns *default* if the
    key is not found or the database is unreachable.
    """
    global _config_cache, _config_cache_timestamp

    # Check if cache is still valid
    now = time.monotonic()
    if _config_cache and (now - _config_cache_timestamp) < _CONFIG_CACHE_TTL:
        return _config_cache.get(key, default)

    # Cache expired or empty — refresh
    try:
        load_all_config()
    except Exception as e:
        logger.warning(f"Config cache refresh failed, using stale cache: {e}")

    return _config_cache.get(key, default)


def refresh_config_cache() -> None:
    """Force refresh the config cache (for admin use)."""
    global _config_cache_timestamp
    _config_cache_timestamp = 0.0  # Invalidate TTL
    load_all_config()
    logger.info("System config cache force-refreshed")
