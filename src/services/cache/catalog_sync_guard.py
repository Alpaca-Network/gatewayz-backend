"""Cross-worker guard that keeps catalog caches off a half-written database.

``sync_all_providers`` rewrites the ``models`` table provider by provider. While
that runs the table is a moving target: a provider's rows may be partly upserted
and stale rows not yet delisted. Any catalog rebuild during the window — the
sync's own post-run warm, or an ordinary ``GET /models`` — reads that mid-flight
state and caches it for the full provider TTL (30 minutes), so a 12-second sync
can serve a wrong catalog for half an hour.

A process-local flag cannot fix this: Redis is shared across workers, so the
worker running the sync is rarely the worker answering the request. The marker
therefore lives in Redis, and every catalog cache *write* consults it. Reads are
untouched — during a sync callers simply fall through to the database and get
fresh (if briefly inconsistent) data instead of poisoning the cache for everyone.

The marker always carries a TTL, so a crashed sync expires instead of wedging
the catalog into permanent no-cache mode.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Redis key holding the marker. Namespaced with the catalog cache keys so the
# whole catalog surface is greppable under one prefix.
SYNC_GUARD_KEY = "gw:models:sync:in_progress"

# Safety net for a sync that dies without clearing the marker. Generous relative
# to a real sync (~12s for the current roster, minutes for a large one) but far
# below the provider cache TTL it protects.
SYNC_GUARD_TTL_SECONDS = 900


def mark_sync_started(ttl: int = SYNC_GUARD_TTL_SECONDS) -> bool:
    """Announce that a catalog sync is mutating the database.

    Returns True when the marker was written. A Redis outage returns False and
    the caller proceeds — degraded to the previous behaviour, never blocked.
    """
    try:
        from src.config.redis_config import get_redis_client, is_redis_available

        redis_client = get_redis_client()
        if not redis_client or not is_redis_available():
            logger.debug("Sync guard: Redis unavailable, catalog writes stay unguarded")
            return False

        redis_client.setex(SYNC_GUARD_KEY, ttl, "1")
        logger.info("Sync guard: catalog cache writes suppressed (ttl=%ss)", ttl)
        return True
    except Exception as e:  # pragma: no cover - defensive, never fail a sync
        logger.warning("Sync guard: failed to set marker (non-fatal): %s", e)
        return False


def mark_sync_finished() -> bool:
    """Clear the marker so catalog caches may be written again."""
    try:
        from src.config.redis_config import get_redis_client, is_redis_available

        redis_client = get_redis_client()
        if not redis_client or not is_redis_available():
            return False

        redis_client.delete(SYNC_GUARD_KEY)
        logger.info("Sync guard: catalog cache writes re-enabled")
        return True
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Sync guard: failed to clear marker (non-fatal): %s", e)
        return False


def is_sync_in_progress() -> bool:
    """Return True while a catalog sync is mutating the database.

    Fails open: if Redis cannot answer we allow the write, preserving the
    pre-guard behaviour rather than disabling caching entirely.
    """
    try:
        from src.config.redis_config import get_redis_client, is_redis_available

        redis_client = get_redis_client()
        if not redis_client or not is_redis_available():
            return False

        return bool(redis_client.exists(SYNC_GUARD_KEY))
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Sync guard: existence check failed, allowing write: %s", e)
        return False


class catalog_sync_guard:  # noqa: N801 - context manager reads as a verb at call sites
    """Context manager wrapping a sync so the marker is always cleared."""

    def __init__(self, ttl: int = SYNC_GUARD_TTL_SECONDS):
        self.ttl = ttl
        self.marked = False

    def __enter__(self) -> catalog_sync_guard:
        self.marked = mark_sync_started(self.ttl)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.marked:
            mark_sync_finished()
        return False
