import logging
from typing import Iterable

from src.config.config import Config
from src.config.supabase_config import get_supabase_client
from src.utils.security_validators import sanitize_for_logging

try:  # pragma: no cover - optional dependency checked in tests
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SCHEMA_CACHE_ERROR_CODES: Iterable[str] = ("PGRST204", "PGRST205")


def is_schema_cache_error(error: Exception) -> bool:
    """Return True when the Supabase/PostgREST error looks like a schema cache miss."""
    message = sanitize_for_logging(str(error))
    if any(code in message for code in _SCHEMA_CACHE_ERROR_CODES):
        return True
    return "schema cache" in message.lower()


def _notify_postgrest_via_direct_connection() -> bool:
    """
    Send NOTIFY pgrst,'reload schema' through a direct Postgres connection.

    This fallback path is used when PostgREST's schema cache is so stale that the
    refresh RPC itself is missing from the cache (PGRST202). Operators can opt-in by
    providing SUPABASE_DB_DSN (service role connection string). When unavailable we
    simply return False so callers know the refresh did not happen.
    """
    dsn = getattr(Config, "SUPABASE_DB_DSN", None)
    if not dsn:
        logger.debug(
            "SUPABASE_DB_DSN not configured; skipping direct Postgres schema refresh fallback."
        )
        return False
    if psycopg is None:
        logger.warning(
            "psycopg is not installed; cannot invoke PostgREST schema refresh via direct connection."
        )
        return False

    try:
        with psycopg.connect(dsn, autocommit=True) as connection:  # type: ignore[attr-defined]
            with connection.cursor() as cursor:
                cursor.execute("NOTIFY pgrst, 'reload schema';")
        logger.info("Triggered PostgREST schema cache refresh via direct Postgres connection.")
        return True
    except Exception as pg_error:  # pragma: no cover - exercised via unit tests with monkeypatch
        logger.warning(
            "Direct Postgres schema cache refresh failed: %s",
            sanitize_for_logging(str(pg_error)),
        )
        return False


def refresh_postgrest_schema_cache() -> bool:
    """
    Attempt to reload PostgREST's schema cache.

    Primary path: invoke the refresh RPC exposed through PostgREST (fast path).
    Fallback path: if the RPC is missing or PostgREST refuses the request, fall back to a
    direct Postgres NOTIFY when SUPABASE_DB_DSN is available.
    """
    try:
        client = get_supabase_client()
        client.rpc("refresh_postgrest_schema_cache", {}).execute()
        logger.info("Requested PostgREST schema cache refresh via RPC.")
        return True
    except Exception as refresh_error:
        logger.warning(
            "Failed to refresh PostgREST schema cache via RPC: %s",
            sanitize_for_logging(str(refresh_error)),
        )
        return _notify_postgrest_via_direct_connection()
