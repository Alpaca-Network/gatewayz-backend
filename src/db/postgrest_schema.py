import logging
from typing import Iterable

from src.config.supabase_config import get_supabase_client
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

_SCHEMA_CACHE_ERROR_CODES: Iterable[str] = ("PGRST204", "PGRST205")


def is_schema_cache_error(error: Exception) -> bool:
    """Return True when the Supabase/PostgREST error looks like a schema cache miss."""
    message = sanitize_for_logging(str(error))
    if any(code in message for code in _SCHEMA_CACHE_ERROR_CODES):
        return True
    return "schema cache" in message.lower()


def refresh_postgrest_schema_cache() -> bool:
    """Invoke the Supabase RPC that triggers PostgREST to reload its schema cache."""
    try:
        client = get_supabase_client()
        client.rpc("refresh_postgrest_schema_cache", {}).execute()
        logger.info("Requested PostgREST schema cache refresh via RPC.")
        return True
    except Exception as refresh_error:
        logger.warning(
            "Failed to refresh PostgREST schema cache: %s",
            sanitize_for_logging(str(refresh_error)),
        )
        return False
