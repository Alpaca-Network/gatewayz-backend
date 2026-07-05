"""Portable per-user memory store (Gatewayz One Phase 4).

CRUD over ``public.user_memory`` — the model-agnostic facts/preferences the
context assembler (:mod:`src.services.context_assembly_bridge`) injects into a
request's context. The table is RLS-locked (service-role only), so all access
goes through the backend service client here.

Reads are served from a short in-process TTL cache so the chat hot path (which
loads a user's memory on every request when context assembly is enabled) does not
pay a DB round-trip per request — at most one query per user per ``_CACHE_TTL``.
Reads never raise (return ``[]`` on failure); writes raise so callers/routes can
surface the error, and invalidate the user's cache entry.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_TABLE = "user_memory"
_CACHE_TTL = 60.0  # seconds
_MAX_CONTENT_LEN = 4000

# user_id -> (expires_at_monotonic, list[dict])
_cache: dict = {}
_lock = threading.Lock()


def _invalidate(user_id) -> None:
    with _lock:
        _cache.pop(user_id, None)


def get_memories(user_id, limit: int = 50, *, use_cache: bool = True) -> list[dict]:
    """Return a user's memory items, highest salience first. [] on any failure."""
    if user_id is None:
        return []
    if use_cache:
        with _lock:
            entry = _cache.get(user_id)
            if entry and entry[0] > time.monotonic():
                return entry[1][:limit]
    try:
        from src.config.supabase_config import get_supabase_client

        resp = (
            get_supabase_client()
            .table(_TABLE)
            .select("id,content,salience,kind,created_at")
            .eq("user_id", user_id)
            .order("salience", desc=True)
            .limit(max(1, min(limit, 200)))
            .execute()
        )
        rows = getattr(resp, "data", None) or []
    except Exception as e:
        logger.debug("user_memory read failed for %s: %s", user_id, e)
        return []
    with _lock:
        _cache[user_id] = (time.monotonic() + _CACHE_TTL, rows)
    return rows[:limit]


def add_memory(user_id, content: str, *, kind: str = "fact", salience: float = 0.5) -> dict:
    """Insert a memory item for a user. Raises ValueError/Exception on bad input or DB error."""
    if user_id is None:
        raise ValueError("user_id is required")
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")
    if len(content) > _MAX_CONTENT_LEN:
        content = content[:_MAX_CONTENT_LEN]
    salience = max(0.0, min(1.0, float(salience)))

    from src.config.supabase_config import get_supabase_client

    resp = (
        get_supabase_client()
        .table(_TABLE)
        .insert(
            {"user_id": user_id, "content": content, "kind": kind or "fact", "salience": salience}
        )
        .execute()
    )
    _invalidate(user_id)
    rows = getattr(resp, "data", None) or []
    return (
        rows[0]
        if rows
        else {"user_id": user_id, "content": content, "kind": kind, "salience": salience}
    )


def delete_memory(memory_id, user_id) -> bool:
    """Delete one of a user's memory items (scoped to the user). True if a row was removed."""
    from src.config.supabase_config import get_supabase_client

    resp = (
        get_supabase_client()
        .table(_TABLE)
        .delete()
        .eq("id", memory_id)
        .eq("user_id", user_id)
        .execute()
    )
    _invalidate(user_id)
    return bool(getattr(resp, "data", None))


def clear_memories(user_id) -> int:
    """Delete all of a user's memory items. Returns the number removed."""
    from src.config.supabase_config import get_supabase_client

    resp = get_supabase_client().table(_TABLE).delete().eq("user_id", user_id).execute()
    _invalidate(user_id)
    return len(getattr(resp, "data", None) or [])
