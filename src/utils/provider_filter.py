"""
Centralized provider enablement filter.

All subsystems (registry, failover, catalog, sync, connection pool) call
``is_provider_enabled()`` so a single ``ENABLED_PROVIDERS`` env-var controls
which providers are loaded, routed to, displayed, and synced.
"""

from __future__ import annotations

import logging
from typing import Any

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


# ---------------------------------------------------------------------------
# Per-provider model allowlist (PROVIDER_MODEL_ALLOWLIST)
# ---------------------------------------------------------------------------

_allowlist_cache: tuple[str, dict[str, frozenset[str]] | None] | None = None


def _normalize_slug(slug: str) -> str:
    return (slug or "").strip().lower().replace("_", "-")


def _normalize_model_id(provider: str, model_id: str) -> str:
    """Lowercase *model_id* and drop a leading ``"<provider>/"`` prefix."""
    normalized = (model_id or "").strip().lower()
    prefix = f"{provider}/"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    return normalized


def _parse_allowlist(raw: str) -> dict[str, frozenset[str]] | None:
    import json

    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("PROVIDER_MODEL_ALLOWLIST is not valid JSON; ignoring it (no restriction)")
        return None
    if not isinstance(data, dict):
        logger.warning("PROVIDER_MODEL_ALLOWLIST must be a JSON object; ignoring it")
        return None

    parsed: dict[str, frozenset[str]] = {}
    for slug, models in data.items():
        if not isinstance(slug, str) or not isinstance(models, list):
            logger.warning("PROVIDER_MODEL_ALLOWLIST entry %r is not slug -> list; skipped", slug)
            continue
        provider = _normalize_slug(slug)
        parsed[provider] = frozenset(
            _normalize_model_id(provider, m) for m in models if isinstance(m, str) and m.strip()
        )
    if parsed:
        logger.info(
            "PROVIDER_MODEL_ALLOWLIST active: %s",
            ", ".join(f"{p} ({len(ids)} models)" for p, ids in sorted(parsed.items())),
        )
    return parsed or None


def get_model_allowlist() -> dict[str, frozenset[str]] | None:
    """Return the parsed allowlist (None = no provider is restricted).

    Parsed once per distinct raw value, so a test that patches Config sees its value.
    """
    global _allowlist_cache
    from src.config.config import Config

    raw = getattr(Config, "PROVIDER_MODEL_ALLOWLIST", "") or ""
    if _allowlist_cache is None or _allowlist_cache[0] != raw:
        _allowlist_cache = (raw, _parse_allowlist(raw))
    return _allowlist_cache[1]


def is_model_allowed(provider: str, model_id: str | None) -> bool:
    """Return True if *provider* may list or serve *model_id*.

    Providers without an allowlist entry are unrestricted. Matching is
    case-insensitive on the provider's native id; a ``"<provider>/"`` prefix
    on *model_id* is ignored. O(1).
    """
    allowlist = get_model_allowlist()
    if not allowlist:
        return True
    slug = _normalize_slug(provider)
    allowed = allowlist.get(slug)
    if allowed is None:
        return True
    return _normalize_model_id(slug, model_id or "") in allowed


def _catalog_row_ids(model: dict[str, Any]) -> list[str]:
    """Ids a catalog or DB row may carry its provider-native id under."""
    ids = []
    for key in ("provider_model_id", "id", "slug", "model_name"):
        value = model.get(key)
        if isinstance(value, str) and value:
            ids.append(value)
    return ids


def filter_models_by_allowlist(provider: str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop rows of *provider*'s catalog that its allowlist does not name.

    Returns *models* unchanged (same object) when the provider is unrestricted.
    """
    allowlist = get_model_allowlist()
    if not allowlist or _normalize_slug(provider) not in allowlist or not models:
        return models
    kept = [
        m
        for m in models
        if isinstance(m, dict) and any(is_model_allowed(provider, i) for i in _catalog_row_ids(m))
    ]
    if len(kept) != len(models):
        logger.debug(
            "PROVIDER_MODEL_ALLOWLIST: %s catalog %d -> %d models", provider, len(models), len(kept)
        )
    return kept


def filter_chain_by_model_allowlist(model_id: str | None, provider_chain: list[str]) -> list[str]:
    """Remove providers whose allowlist excludes *model_id* from a routing chain.

    The request's model id is checked as sent and as transformed to each
    restricted provider's native id.
    """
    allowlist = get_model_allowlist()
    if not allowlist or not model_id or not provider_chain:
        return provider_chain

    kept: list[str] = []
    for provider in provider_chain:
        if _normalize_slug(provider) not in allowlist or is_model_allowed(provider, model_id):
            kept.append(provider)
            continue
        try:
            from src.services.model_transformations import transform_model_id

            native = transform_model_id(model_id, provider)
        except Exception:
            native = None
        if native and is_model_allowed(provider, native):
            kept.append(provider)
        else:
            logger.info(
                "PROVIDER_MODEL_ALLOWLIST: '%s' not allowed on %s; dropped from chain",
                model_id,
                provider,
            )
    return kept
