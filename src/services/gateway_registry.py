"""
DB-backed gateway registry with in-memory caching.

Replaces the hardcoded GATEWAY_REGISTRY dict in catalog.py with a
cached lookup that reads from the ``providers`` table.  Falls back to
a hardcoded registry on cold start when the database is unreachable.
"""

import logging
import os
import time
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache state
# ---------------------------------------------------------------------------
_registry_cache: dict[str, dict] = {}  # slug -> gateway config
_cache_timestamp: float = 0.0
_CACHE_TTL = 300  # 5 minutes
_DEFAULT_FETCH_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Hardcoded fallback – ensures the app boots even if the DB is down.
# Mirrors the 33 entries previously in catalog.py GATEWAY_REGISTRY.
# ---------------------------------------------------------------------------
_FALLBACK_REGISTRY: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI",
        "color": "bg-emerald-600",
        "priority": "fast",
        "site_url": "https://openai.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "anthropic": {
        "name": "Anthropic",
        "color": "bg-amber-700",
        "priority": "fast",
        "site_url": "https://anthropic.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "openrouter": {
        "name": "OpenRouter",
        "color": "bg-blue-500",
        "priority": "fast",
        "site_url": "https://openrouter.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "groq": {
        "name": "Groq",
        "color": "bg-orange-500",
        "priority": "fast",
        "site_url": "https://groq.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": "zap",
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "together": {
        "name": "Together",
        "color": "bg-indigo-500",
        "priority": "fast",
        "site_url": "https://together.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "fireworks": {
        "name": "Fireworks",
        "color": "bg-red-500",
        "priority": "fast",
        "site_url": "https://fireworks.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "vercel-ai-gateway": {
        "name": "Vercel AI",
        "color": "bg-slate-900",
        "priority": "fast",
        "site_url": "https://vercel.com/ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "featherless": {
        "name": "Featherless",
        "color": "bg-green-500",
        "priority": "slow",
        "site_url": "https://featherless.ai",
        "timeout": 60,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "chutes": {
        "name": "Chutes",
        "color": "bg-yellow-500",
        "priority": "slow",
        "site_url": "https://chutes.ai",
        "timeout": 60,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "deepinfra": {
        "name": "DeepInfra",
        "color": "bg-cyan-500",
        "priority": "slow",
        "site_url": "https://deepinfra.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "google-vertex": {
        "name": "Google",
        "color": "bg-blue-600",
        "priority": "fast",
        "site_url": "https://cloud.google.com/vertex-ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": ["google"],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "cerebras": {
        "name": "Cerebras",
        "color": "bg-amber-600",
        "priority": "slow",
        "site_url": "https://cerebras.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "nebius": {
        "name": "Nebius",
        "color": "bg-slate-600",
        "priority": "slow",
        "site_url": "https://nebius.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "xai": {
        "name": "xAI",
        "color": "bg-black",
        "priority": "slow",
        "site_url": "https://x.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "novita": {
        "name": "Novita",
        "color": "bg-violet-600",
        "priority": "slow",
        "site_url": "https://novita.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "huggingface": {
        "name": "Hugging Face",
        "color": "bg-yellow-600",
        "priority": "slow",
        "site_url": "https://huggingface.co",
        "timeout": 60,
        "has_fetch_function": True,
        "aliases": ["hug"],
        "icon": None,
        "fetch_slug_override": "hug",
        "logo_url": None,
    },
    "aimo": {
        "name": "AiMo",
        "color": "bg-pink-600",
        "priority": "slow",
        "site_url": "https://aimo.network",
        "timeout": 60,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "near": {
        "name": "NEAR",
        "color": "bg-teal-600",
        "priority": "slow",
        "site_url": "https://near.ai",
        "timeout": 60,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "fal": {
        "name": "Fal",
        "color": "bg-emerald-600",
        "priority": "slow",
        "site_url": "https://fal.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "helicone": {
        "name": "Helicone",
        "color": "bg-indigo-600",
        "priority": "slow",
        "site_url": "https://helicone.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "alpaca": {
        "name": "Alpaca Network",
        "color": "bg-green-700",
        "priority": "slow",
        "site_url": "https://alpaca.network",
        "timeout": 30,
        "has_fetch_function": False,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "alibaba": {
        "name": "Alibaba",
        "color": "bg-orange-700",
        "priority": "slow",
        "site_url": "https://dashscope.aliyun.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "clarifai": {
        "name": "Clarifai",
        "color": "bg-purple-600",
        "priority": "slow",
        "site_url": "https://clarifai.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "onerouter": {
        "name": "Infron AI",
        "color": "bg-emerald-500",
        "priority": "slow",
        "site_url": "https://infron.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "zai": {
        "name": "Z.AI",
        "color": "bg-purple-700",
        "priority": "slow",
        "site_url": "https://z.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "simplismart": {
        "name": "SimpliSmart",
        "color": "bg-sky-500",
        "priority": "slow",
        "site_url": "https://simplismart.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "sybil": {
        "name": "Sybil",
        "color": "bg-purple-500",
        "priority": "slow",
        "site_url": "https://sybil.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "aihubmix": {
        "name": "AiHubMix",
        "color": "bg-rose-500",
        "priority": "slow",
        "site_url": "https://aihubmix.com",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "anannas": {
        "name": "Anannas",
        "color": "bg-lime-600",
        "priority": "slow",
        "site_url": "https://anannas.ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "cloudflare-workers-ai": {
        "name": "Cloudflare Workers AI",
        "color": "bg-orange-500",
        "priority": "slow",
        "site_url": "https://developers.cloudflare.com/workers-ai",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "morpheus": {
        "name": "Morpheus",
        "color": "bg-cyan-600",
        "priority": "slow",
        "site_url": "https://mor.org",
        "timeout": 30,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "canopywave": {
        "name": "Canopy Wave",
        "color": "bg-teal-500",
        "priority": "slow",
        "site_url": "https://canopywave.io",
        "timeout": 60,
        "has_fetch_function": True,
        "aliases": [],
        "icon": None,
        "fetch_slug_override": None,
        "logo_url": None,
    },
    "notdiamond": {
        "name": "NotDiamond",
        "color": "bg-violet-500",
        "priority": "fast",
        "site_url": "https://notdiamond.ai",
        "timeout": 30,
        "has_fetch_function": False,
        "aliases": [],
        "icon": "zap",
        "fetch_slug_override": None,
        "logo_url": None,
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_registry_from_db() -> dict[str, dict]:
    """Load the gateway registry from the ``providers`` table.

    Builds a dict keyed by provider slug with gateway config extracted
    from the row's top-level fields and its ``metadata`` JSONB column.

    On failure, returns the existing in-memory cache (if populated) or
    the hardcoded ``_FALLBACK_REGISTRY``.
    """
    global _registry_cache, _cache_timestamp

    try:
        # Lazy import to avoid circular imports at module load time.
        from src.db.providers_db import get_all_providers

        rows = get_all_providers(is_active_only=True)
        if not rows:
            logger.warning(
                "providers table returned no active rows; " "keeping existing cache or falling back"
            )
            return _registry_cache if _registry_cache else dict(_FALLBACK_REGISTRY)

        new_registry: dict[str, dict] = {}
        for row in rows:
            slug = row.get("slug")
            if not slug:
                continue
            meta = row.get("metadata") or {}
            new_registry[slug] = {
                "name": row.get("name", slug),
                "site_url": row.get("site_url"),
                "logo_url": row.get("logo_url"),
                "api_key_env_var": row.get("api_key_env_var"),
                "fetch_module_path": row.get("fetch_module_path"),
                "fetch_function_name": row.get("fetch_function_name"),
                # Phase 1/2 fields
                "color": meta.get("color", "bg-gray-500"),
                "priority": meta.get("priority", "slow"),
                "icon": meta.get("icon"),
                "aliases": meta.get("aliases", []),
                "timeout": meta.get("timeout", _DEFAULT_FETCH_TIMEOUT),
                "has_fetch_function": meta.get("has_fetch_function", True),
                "fetch_slug_override": meta.get("fetch_slug_override"),
                "latency_tier": meta.get("latency_tier"),
                "pricing_format": meta.get("pricing_format"),
                "failover_priority": meta.get("failover_priority"),
                # Phase 3 infrastructure fields
                "base_url": row.get("base_url") or meta.get("pool_base_url"),
                "models_endpoint": meta.get("models_endpoint"),
                "chat_completions_endpoint": meta.get("chat_completions_endpoint"),
                "min_expected_models": meta.get("min_expected_models", 1),
                "header_type": meta.get("header_type", "bearer"),
                "default_headers": meta.get("default_headers", {}),
                "custom_timeout_ms": meta.get("custom_timeout_ms"),
                "hostnames": meta.get("hostnames", []),
                "monitor_402_frequency": meta.get("monitor_402_frequency", False),
                "async_streaming": meta.get("async_streaming", False),
            }

        _registry_cache = new_registry
        _cache_timestamp = time.monotonic()
        logger.info("Gateway registry loaded from DB: %d providers", len(new_registry))
        return new_registry

    except Exception as e:
        logger.error("Failed to load gateway registry from DB: %s", e)
        return _registry_cache if _registry_cache else dict(_FALLBACK_REGISTRY)


def _get_registry() -> dict[str, dict]:
    """Return the gateway registry, refreshing if the TTL has expired."""
    global _registry_cache, _cache_timestamp

    now = time.monotonic()
    if _registry_cache and (now - _cache_timestamp) < _CACHE_TTL:
        return _registry_cache

    return _load_registry_from_db()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_gateway_registry() -> dict[str, dict]:
    """Return the full gateway registry dict (slug -> config).

    Respects ENABLED_PROVIDERS — disabled providers are excluded from results.
    """
    from src.utils.provider_filter import filter_provider_dict

    return filter_provider_dict(_get_registry())


def get_valid_gateway_values() -> set[str]:
    """Compute the set of all accepted gateway identifiers.

    Includes every slug, every alias from every gateway, and the
    special ``"all"`` keyword.
    """
    registry = _get_registry()
    slugs: set[str] = set(registry.keys())
    aliases: set[str] = {alias for cfg in registry.values() for alias in cfg.get("aliases", [])}
    return slugs | aliases | {"all"}


def get_gateway_slug_resolution() -> dict[str, str]:
    """Build a map from any slug or alias to its canonical fetch slug.

    If a gateway defines ``fetch_slug_override``, that value is used as
    the fetch slug; otherwise the gateway's own slug is used.

    Example entries::

        {"huggingface": "hug", "hug": "hug",
         "google": "google-vertex", "google-vertex": "google-vertex"}
    """
    registry = _get_registry()
    resolution: dict[str, str] = {}
    for slug, cfg in registry.items():
        fetch_slug = cfg.get("fetch_slug_override") or slug
        resolution[slug] = fetch_slug
        for alias in cfg.get("aliases", []):
            resolution[alias] = fetch_slug
    return resolution


def get_provider_slugs() -> list[str]:
    """Return the list of provider fetch slugs for catalog fetching.

    Excludes gateways where ``has_fetch_function`` is ``False`` and
    applies ``fetch_slug_override`` when set (e.g. ``"huggingface"``
    becomes ``"hug"``).
    """
    registry = _get_registry()
    return [
        cfg.get("fetch_slug_override") or slug
        for slug, cfg in registry.items()
        if cfg.get("has_fetch_function", True)
    ]


def get_provider_fetch_timeout(slug: str) -> int:
    """Return the configured model-fetch timeout for *slug* (in seconds).

    Resolves aliases transparently.  Falls back to 30 s when the slug
    is not found.
    """
    slug = (slug or "").lower()
    registry = _get_registry()

    # Direct lookup
    if slug in registry:
        return registry[slug].get("timeout", _DEFAULT_FETCH_TIMEOUT)

    # Resolve alias -> canonical slug
    for _key, cfg in registry.items():
        if slug in cfg.get("aliases", []):
            return cfg.get("timeout", _DEFAULT_FETCH_TIMEOUT)

    return _DEFAULT_FETCH_TIMEOUT


def get_provider_api_key(slug: str) -> str | None:
    """Resolve the actual API key value for *slug*.

    Reads ``api_key_env_var`` from the registry, then resolves it via
    ``Config`` (which mirrors ``os.environ`` for known vars) with a
    fallback to ``os.environ.get()``.
    """
    registry = _get_registry()
    entry = registry.get(slug)
    if not entry:
        return None
    env_var = entry.get("api_key_env_var")
    if not env_var:
        return None
    # Prefer Config (supports .env files and test overrides)
    try:
        from src.config import Config

        val = getattr(Config, env_var, None)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(env_var)


def validate_gateway(gateway: str | None) -> None:
    """Raise HTTP 400 if *gateway* is not a recognised identifier.

    Accepts ``None`` silently (no gateway filter requested).

    Raises:
        HTTPException: 400 with a message listing valid identifiers.
    """
    if gateway is None:
        return
    if gateway.lower() not in get_valid_gateway_values():
        sorted_valid = sorted(get_valid_gateway_values() - {"all"})
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid gateway '{gateway}'. "
                f"Must be 'all' or one of: {', '.join(sorted_valid)}"
            ),
        )


def refresh_registry_cache() -> None:
    """Force-invalidate and reload the registry cache."""
    global _cache_timestamp
    _cache_timestamp = 0.0
    _load_registry_from_db()
    try:
        from src.services.dynamic_provider_loader import invalidate_loader_cache

        invalidate_loader_cache()
    except ImportError:
        pass
    logger.info("Gateway registry cache force-refreshed")
