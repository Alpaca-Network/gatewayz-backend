"""
Centralized provider enablement filter.

All subsystems (registry, failover, catalog, sync, connection pool) call
``is_provider_enabled()`` so a single ``ENABLED_PROVIDERS`` env-var controls
which providers are loaded, routed to, displayed, and synced.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_logged_once: bool = False


def _get_enabled_set() -> frozenset[str] | None:
    """Return the enabled-providers set from Config (cached at class level)."""
    from src.config.config import Config

    return Config.ENABLED_PROVIDERS


def is_provider_enabled(slug: str) -> bool:
    """Return True if *slug* is allowed by ENABLED_PROVIDERS.

    When ENABLED_PROVIDERS is unset / empty, every provider is enabled.
    Comparison is case-insensitive and normalizes underscores to hyphens
    so both ``"openai"`` and ``"google_vertex"`` / ``"google-vertex"`` work.
    """
    global _logged_once

    enabled = _get_enabled_set()
    if enabled is None:
        return True  # no restriction

    if not _logged_once:
        logger.info("ENABLED_PROVIDERS active: %s", ", ".join(sorted(enabled)))
        _logged_once = True

    normalized = slug.lower().replace("_", "-")
    return normalized in enabled


def get_enabled_providers() -> frozenset[str] | None:
    """Return the raw enabled set (None = all)."""
    return _get_enabled_set()


def filter_provider_dict(d: dict[str, any]) -> dict[str, any]:
    """Return a copy of *d* keeping only keys whose slug is enabled."""
    enabled = _get_enabled_set()
    if enabled is None:
        return d
    return {k: v for k, v in d.items() if is_provider_enabled(k)}
