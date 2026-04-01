"""
In-memory cache for model ID mapping tables.

Loads model_aliases, model_provider_mappings, and model_routing_rules from the
database at startup and caches them in process memory. Automatically refreshes
every 15 minutes on next access.

This is intentionally synchronous — all callers (transform_model_id,
detect_provider_from_model_id, apply_model_alias) are in hot-path inference code
that must not incur per-request DB latency. The in-memory cache mirrors the
previous hardcoded-dict structure with zero overhead difference.

Usage:
    # At startup (called from staggered_db_warmup in startup.py):
    load_model_mappings_cache()

    # In model_transformations.py:
    from src.services.model_mappings_cache import (
        get_aliases,
        get_provider_mappings,
        get_routing_rules,
        get_provider_native_values,
    )
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory cache state ─────────────────────────────────────────────────────

# alias (lowercase) → canonical_id
_aliases: dict[str, str] = {}

# provider → {simplified_model_id (lowercase) → provider_native_model_id}
_provider_mappings: dict[str, dict[str, str]] = {}

# model_pattern (lowercase) → force_provider
_routing_rules: dict[str, str] = {}

# provider → set of native model IDs (for O(1) reverse lookup)
_provider_native_values: dict[str, set[str]] = {}

_cache_loaded: bool = False
_cache_loaded_at: float = 0.0
_CACHE_TTL: float = 900.0  # 15 minutes


# ── Public loaders ────────────────────────────────────────────────────────────


def load_model_mappings_cache(force: bool = False) -> None:
    """
    Load (or refresh) all 3 mapping tables into in-memory dicts.

    Safe to call multiple times — skips reload if cache is fresh unless
    force=True. Intended to be called via asyncio.to_thread() from startup.py.

    Args:
        force: If True, reload even if cache is still within TTL.
    """
    global _aliases, _provider_mappings, _routing_rules, _provider_native_values
    global _cache_loaded, _cache_loaded_at

    now = time.monotonic()
    if not force and _cache_loaded and (now - _cache_loaded_at) < _CACHE_TTL:
        logger.debug("Model mappings cache is fresh (%.0fs old), skipping reload", now - _cache_loaded_at)
        return

    logger.info("Loading model mappings cache from database...")

    try:
        from src.db.model_mappings import (
            get_all_model_aliases,
            get_all_model_provider_mappings,
            get_all_model_routing_rules,
        )

        # Load aliases
        alias_rows = get_all_model_aliases()
        new_aliases: dict[str, str] = {}
        for row in alias_rows:
            alias = (row.get("alias") or "").lower()
            canonical = row.get("canonical_id") or ""
            if alias and canonical:
                new_aliases[alias] = canonical

        # Load provider mappings — build nested dict by provider
        mapping_rows = get_all_model_provider_mappings()
        new_provider_mappings: dict[str, dict[str, str]] = {}
        for row in mapping_rows:
            provider = (row.get("provider") or "").lower()
            model_id = (row.get("model_id") or "").lower()
            provider_model_id = row.get("provider_model_id") or ""
            if provider and model_id and provider_model_id:
                if provider not in new_provider_mappings:
                    new_provider_mappings[provider] = {}
                new_provider_mappings[provider][model_id] = provider_model_id

        # Load routing rules
        rule_rows = get_all_model_routing_rules()
        new_routing_rules: dict[str, str] = {}
        for row in rule_rows:
            pattern = (row.get("model_pattern") or "").lower()
            provider = row.get("force_provider") or ""
            if pattern and provider:
                new_routing_rules[pattern] = provider

        # Build reverse lookup: provider → set of native model IDs
        new_native_values: dict[str, set[str]] = {
            provider: set(mapping.values())
            for provider, mapping in new_provider_mappings.items()
        }

        # Atomic swap — replace all caches at once
        _aliases = new_aliases
        _provider_mappings = new_provider_mappings
        _routing_rules = new_routing_rules
        _provider_native_values = new_native_values
        _cache_loaded = True
        _cache_loaded_at = time.monotonic()

        logger.info(
            "Model mappings cache loaded: %d aliases, %d provider mappings (%d providers), %d routing rules",
            len(_aliases),
            sum(len(m) for m in _provider_mappings.values()),
            len(_provider_mappings),
            len(_routing_rules),
        )

    except Exception as e:
        logger.error("Failed to load model mappings cache: %s", e)
        # Do not mark cache as loaded — next call will retry


def invalidate_model_mappings_cache() -> None:
    """Force a reload on next access by resetting the loaded-at timestamp."""
    global _cache_loaded_at
    _cache_loaded_at = 0.0
    logger.info("Model mappings cache invalidated — will reload on next access")


# ── Cache accessors ───────────────────────────────────────────────────────────
# Each accessor auto-refreshes if the cache is stale. The refresh is synchronous
# (not async) because transform_model_id / detect_provider_from_model_id are
# called in sync context deep inside request handlers.


def _ensure_loaded() -> None:
    """Refresh cache if stale. Called by every accessor."""
    now = time.monotonic()
    if not _cache_loaded or (now - _cache_loaded_at) >= _CACHE_TTL:
        load_model_mappings_cache()


def get_aliases() -> dict[str, str]:
    """
    Return alias → canonical_id mapping (auto-refreshes if stale).

    Keys are lowercase. Values are canonical model IDs as stored in the DB.
    """
    _ensure_loaded()
    return _aliases


def get_provider_mappings(provider: str = "") -> dict[str, str]:
    """
    Return simplified_model_id → provider_native_model_id for the given provider.

    If provider is empty string or not found, returns {}.
    Keys are lowercase (matching the normalized lookup in transform_model_id).
    """
    _ensure_loaded()
    if not provider:
        # Return entire nested dict when no provider specified (used by detect_provider)
        return _provider_mappings  # type: ignore[return-value]
    return _provider_mappings.get(provider.lower(), {})


def get_all_provider_mappings() -> dict[str, dict[str, str]]:
    """
    Return the full nested dict: provider → {model_id → provider_model_id}.

    Used for provider detection when iterating all providers.
    """
    _ensure_loaded()
    return _provider_mappings


def get_routing_rules() -> dict[str, str]:
    """
    Return model_pattern → force_provider override mapping (auto-refreshes if stale).

    Keys are lowercase.
    """
    _ensure_loaded()
    return _routing_rules


def get_provider_native_values(provider: str) -> set[str]:
    """
    Return the set of provider-native model IDs for a given provider.

    Used for O(1) reverse lookup in detect_provider_from_model_id.
    Returns empty set if provider not found.
    """
    _ensure_loaded()
    return _provider_native_values.get(provider.lower(), set())


def get_cache_stats() -> dict[str, Any]:
    """Return diagnostic stats about the current cache state."""
    return {
        "loaded": _cache_loaded,
        "age_seconds": round(time.monotonic() - _cache_loaded_at, 1) if _cache_loaded else None,
        "ttl_seconds": _CACHE_TTL,
        "alias_count": len(_aliases),
        "provider_count": len(_provider_mappings),
        "total_mapping_count": sum(len(m) for m in _provider_mappings.values()),
        "routing_rule_count": len(_routing_rules),
    }
