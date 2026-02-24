import asyncio
import hashlib
import json
import logging
import time

# orjson is 5-10x faster than stdlib json for large payloads; use it when available.
# Add orjson to requirements.txt to enable: `orjson>=3.9`
try:
    import orjson as _orjson

    def _dumps_fast(obj: object) -> str:
        """Serialize obj to a JSON string using orjson (returns bytes → decoded to str)."""
        return _orjson.dumps(obj).decode()

except ImportError:  # pragma: no cover
    _orjson = None  # type: ignore[assignment]

    def _dumps_fast(obj: object) -> str:  # type: ignore[misc]
        """Fallback to stdlib json when orjson is not installed."""
        return json.dumps(obj)


from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from src.db.gateway_analytics import (
    get_all_gateways_summary,
    get_gateway_stats,
    get_provider_stats,
    get_top_models_by_provider,
    get_trending_models,
)
from src.services.models import (
    enhance_model_with_huggingface_data,
    enhance_model_with_provider_info,
    fetch_specific_model,
    get_cached_models,
    get_model_count_by_provider,
    normalize_provider_slug,
)
from src.services.modelz_client import (
    check_model_exists_on_modelz,
    fetch_modelz_tokens,
    get_modelz_model_details,
    get_modelz_model_ids,
)
from src.services.prometheus_metrics import set_gateway_model_count
from src.services.providers import enhance_providers_with_logos_and_sites, get_cached_providers
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Single router for all model catalog endpoints
router = APIRouter()

# Constants for query parameter descriptions (to avoid duplication)
DESC_INCLUDE_HUGGINGFACE = "Include Hugging Face metrics if available"
DESC_GATEWAY_AUTO_DETECT = (
    "Gateway to use: 'openrouter', 'onerouter', 'featherless', 'deepinfra', 'chutes', "
    "'groq', 'fireworks', 'together', 'cerebras', 'nebius', 'xai', 'novita', "
    "'huggingface' (or 'hug'), 'aimo', 'near', 'fal', 'helicone', 'anannas', 'aihubmix', "
    "'vercel-ai-gateway', 'google-vertex', 'simplismart', 'sybil', 'morpheus', or auto-detect if not specified"
)
DESC_GATEWAY_WITH_ALL = (
    "Gateway to use: 'openrouter', 'onerouter', 'featherless', 'deepinfra', 'chutes', "
    "'groq', 'fireworks', 'together', 'cerebras', 'nebius', 'xai', 'novita', "
    "'huggingface' (or 'hug'), 'aimo', 'near', 'fal', 'helicone', 'anannas', 'aihubmix', "
    "'vercel-ai-gateway', 'google-vertex', 'simplismart', 'sybil', 'morpheus', or 'all'"
)
ERROR_MODELS_DATA_UNAVAILABLE = "Models data unavailable"
ERROR_PROVIDER_DATA_UNAVAILABLE = "Provider data unavailable"

# Query parameter description constants
DESC_LIMIT_NUMBER_OF_RESULTS = "Limit number of results"
DESC_OFFSET_FOR_PAGINATION = "Offset for pagination"
DESC_TIME_RANGE_ALL = "Time range: '1h', '24h', '7d', '30d', 'all'"
DESC_TIME_RANGE_NO_ALL = "Time range: '1h', '24h', '7d', '30d'"
DESC_NUMBER_OF_MODELS_TO_RETURN = "Number of models to return"

# Model filter description constants
DESC_ALL_MODELS = "All models"
DESC_GRADUATED_MODELS_ONLY = "Graduated models only"
DESC_NON_GRADUATED_MODELS_ONLY = "Non-graduated models only"

# Gateway configuration - centralized source of truth for all gateways.
# This is used by /gateways endpoint for frontend auto-discovery.
#
# Required fields for every entry:
#   name      (str)  – human-readable display name
#   color     (str)  – Tailwind CSS background class used by the frontend
#   priority  (str)  – "fast" (eager load) or "slow" (deferred load)
#   site_url  (str)  – canonical homepage URL for logo/link generation
#
# Optional fields:
#   timeout   (int)  – model-list fetch timeout in seconds (default: 30)
#   icon      (str)  – override icon name shown in the UI (e.g. "zap")
#   aliases   (list) – alternative slug strings that map to this entry
GATEWAY_REGISTRY = {
    # Fast gateways (priority loading)
    # timeout: seconds allowed for a provider model-list fetch (default 30s)
    "openai": {
        "name": "OpenAI",
        "color": "bg-emerald-600",
        "priority": "fast",
        "site_url": "https://openai.com",
        "timeout": 30,
    },
    "anthropic": {
        "name": "Anthropic",
        "color": "bg-amber-700",
        "priority": "fast",
        "site_url": "https://anthropic.com",
        "timeout": 30,
    },
    "openrouter": {
        "name": "OpenRouter",
        "color": "bg-blue-500",
        "priority": "fast",
        "site_url": "https://openrouter.ai",
        "timeout": 30,
    },
    "groq": {
        "name": "Groq",
        "color": "bg-orange-500",
        "priority": "fast",
        "icon": "zap",
        "site_url": "https://groq.com",
        "timeout": 30,
    },
    "together": {
        "name": "Together",
        "color": "bg-indigo-500",
        "priority": "fast",
        "site_url": "https://together.ai",
        "timeout": 30,
    },
    "fireworks": {
        "name": "Fireworks",
        "color": "bg-red-500",
        "priority": "fast",
        "site_url": "https://fireworks.ai",
        "timeout": 30,
    },
    "vercel-ai-gateway": {
        "name": "Vercel AI",
        "color": "bg-slate-900",
        "priority": "fast",
        "site_url": "https://vercel.com/ai",
        "timeout": 30,
    },
    # Slow gateways (deferred loading)
    "featherless": {
        "name": "Featherless",
        "color": "bg-green-500",
        "priority": "slow",
        "site_url": "https://featherless.ai",
        "timeout": 60,
    },
    "chutes": {
        "name": "Chutes",
        "color": "bg-yellow-500",
        "priority": "slow",
        "site_url": "https://chutes.ai",
        "timeout": 60,
    },
    "deepinfra": {
        "name": "DeepInfra",
        "color": "bg-cyan-500",
        "priority": "slow",
        "site_url": "https://deepinfra.com",
        "timeout": 30,
    },
    "google-vertex": {
        "name": "Google",
        "color": "bg-blue-600",
        "priority": "fast",
        "site_url": "https://cloud.google.com/vertex-ai",
        "aliases": ["google"],
        "timeout": 30,
    },
    "cerebras": {
        "name": "Cerebras",
        "color": "bg-amber-600",
        "priority": "slow",
        "site_url": "https://cerebras.ai",
        "timeout": 30,
    },
    "nebius": {
        "name": "Nebius",
        "color": "bg-slate-600",
        "priority": "slow",
        "site_url": "https://nebius.ai",
        "timeout": 30,
    },
    "xai": {
        "name": "xAI",
        "color": "bg-black",
        "priority": "slow",
        "site_url": "https://x.ai",
        "timeout": 30,
    },
    "novita": {
        "name": "Novita",
        "color": "bg-violet-600",
        "priority": "slow",
        "site_url": "https://novita.ai",
        "timeout": 30,
    },
    "huggingface": {
        "name": "Hugging Face",
        "color": "bg-yellow-600",
        "priority": "slow",
        "site_url": "https://huggingface.co",
        "aliases": ["hug"],
        "timeout": 60,  # HF API is significantly slower than other providers
    },
    "aimo": {
        "name": "AiMo",
        "color": "bg-pink-600",
        "priority": "slow",
        "site_url": "https://aimo.network",
        "timeout": 60,
    },
    "near": {
        "name": "NEAR",
        "color": "bg-teal-600",
        "priority": "slow",
        "site_url": "https://near.ai",
        "timeout": 60,
    },
    "fal": {
        "name": "Fal",
        "color": "bg-emerald-600",
        "priority": "slow",
        "site_url": "https://fal.ai",
        "timeout": 30,
    },
    "helicone": {
        "name": "Helicone",
        "color": "bg-indigo-600",
        "priority": "slow",
        "site_url": "https://helicone.ai",
        "timeout": 30,
    },
    "alpaca": {
        "name": "Alpaca Network",
        "color": "bg-green-700",
        "priority": "slow",
        "site_url": "https://alpaca.network",
        "timeout": 30,
    },
    "alibaba": {
        "name": "Alibaba",
        "color": "bg-orange-700",
        "priority": "slow",
        "site_url": "https://dashscope.aliyun.com",
        "timeout": 30,
    },
    "clarifai": {
        "name": "Clarifai",
        "color": "bg-purple-600",
        "priority": "slow",
        "site_url": "https://clarifai.com",
        "timeout": 30,
    },
    "onerouter": {
        "name": "Infron AI",
        "color": "bg-emerald-500",
        "priority": "slow",
        "site_url": "https://infron.ai",
        "timeout": 30,
    },
    "zai": {
        "name": "Z.AI",
        "color": "bg-purple-700",
        "priority": "slow",
        "site_url": "https://z.ai",
        "timeout": 30,
    },
    "simplismart": {
        "name": "SimpliSmart",
        "color": "bg-sky-500",
        "priority": "slow",
        "site_url": "https://simplismart.ai",
        "timeout": 30,
    },
    "sybil": {
        "name": "Sybil",
        "color": "bg-purple-500",
        "priority": "slow",
        "site_url": "https://sybil.com",
        "timeout": 30,
    },
    "aihubmix": {
        "name": "AiHubMix",
        "color": "bg-rose-500",
        "priority": "slow",
        "site_url": "https://aihubmix.com",
        "timeout": 30,
    },
    "anannas": {
        "name": "Anannas",
        "color": "bg-lime-600",
        "priority": "slow",
        "site_url": "https://anannas.ai",
        "timeout": 30,
    },
    "cloudflare-workers-ai": {
        "name": "Cloudflare Workers AI",
        "color": "bg-orange-500",
        "priority": "slow",
        "site_url": "https://developers.cloudflare.com/workers-ai",
        "timeout": 30,
    },
    "morpheus": {
        "name": "Morpheus",
        "color": "bg-cyan-600",
        "priority": "slow",
        "site_url": "https://mor.org",
        "timeout": 30,
    },
    "canopywave": {
        "name": "Canopy Wave",
        "color": "bg-teal-500",
        "priority": "slow",
        "site_url": "https://canopywave.io",
        "timeout": 60,
    },
    "notdiamond": {
        "name": "NotDiamond",
        "color": "bg-violet-500",
        "priority": "fast",
        "site_url": "https://notdiamond.ai",
        "icon": "zap",
        "timeout": 30,
    },
}

# Default fetch timeout (seconds) used when a provider has no explicit "timeout" entry.
_DEFAULT_FETCH_TIMEOUT: int = 30


def get_provider_fetch_timeout(provider_slug: str) -> int:
    """Return the configured model-fetch timeout (in seconds) for *provider_slug*.

    Falls back to ``_DEFAULT_FETCH_TIMEOUT`` (30 s) when the slug is not found
    in ``GATEWAY_REGISTRY`` or has no explicit ``"timeout"`` value.  Also
    resolves alias slugs (e.g. ``"hug"`` → ``"huggingface"``) transparently.

    Args:
        provider_slug: Gateway/provider key or alias (e.g. ``"huggingface"``, ``"hug"``).

    Returns:
        Timeout in seconds as an integer.
    """
    slug = (provider_slug or "").lower()

    # Direct registry lookup
    if slug in GATEWAY_REGISTRY:
        return GATEWAY_REGISTRY[slug].get("timeout", _DEFAULT_FETCH_TIMEOUT)

    # Resolve alias → canonical key
    for key, config in GATEWAY_REGISTRY.items():
        if slug in config.get("aliases", []):
            return config.get("timeout", _DEFAULT_FETCH_TIMEOUT)

    return _DEFAULT_FETCH_TIMEOUT


# Pre-computed set of all valid gateway values (registry keys + aliases + "all").
# Used for input validation in query parameters across multiple endpoints.
_GATEWAY_REGISTRY_KEYS: set[str] = set(GATEWAY_REGISTRY.keys())
_GATEWAY_ALIASES: set[str] = {
    alias for config in GATEWAY_REGISTRY.values() for alias in config.get("aliases", [])
}
VALID_GATEWAY_VALUES: set[str] = _GATEWAY_REGISTRY_KEYS | _GATEWAY_ALIASES | {"all"}

# Maps a GATEWAY_REGISTRY key to the fetch slug passed to get_cached_models() when the two
# differ.  Currently only "huggingface" uses the legacy alias "hug".
GATEWAY_FETCH_SLUG_OVERRIDES: dict[str, str] = {
    "huggingface": "hug",
}

# Pre-computed map: any alias or registry key → its canonical fetch slug.
# e.g. "google" → "google-vertex", "hug" → "hug", "huggingface" → "hug"
_GATEWAY_SLUG_RESOLUTION: dict[str, str] = {}
for _key, _cfg in GATEWAY_REGISTRY.items():
    _fetch_slug = GATEWAY_FETCH_SLUG_OVERRIDES.get(_key, _key)
    _GATEWAY_SLUG_RESOLUTION[_key] = _fetch_slug
    for _alias in _cfg.get("aliases", []):
        _GATEWAY_SLUG_RESOLUTION[_alias] = _fetch_slug

# Entries in GATEWAY_REGISTRY that do not yet have a fetch function in
# PROVIDER_FETCH_FUNCTIONS (model_catalog_sync.py) are excluded so the
# generic fetch loop does not attempt to call them.
_REGISTRY_KEYS_WITHOUT_FETCH: frozenset[str] = frozenset(
    {
        "notdiamond",
        "alpaca",
    }
)

# Ordered list of provider fetch slugs derived from GATEWAY_REGISTRY.
# This replaces the previous pattern of hardcoding each provider slug individually.
PROVIDER_SLUGS: list[str] = [
    GATEWAY_FETCH_SLUG_OVERRIDES.get(key, key)
    for key in GATEWAY_REGISTRY
    if key not in _REGISTRY_KEYS_WITHOUT_FETCH
]


def _validate_gateway(gateway: str | None) -> None:
    """Raise HTTP 400 if the gateway value is not a known gateway identifier.

    Args:
        gateway: The raw gateway query parameter value (may be None).

    Raises:
        HTTPException: 400 with a message listing valid gateway identifiers.
    """
    if gateway is None:
        return
    if gateway.lower() not in VALID_GATEWAY_VALUES:
        sorted_valid = sorted(VALID_GATEWAY_VALUES - {"all"})
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid gateway '{gateway}'. "
                f"Must be 'all' or one of: {', '.join(sorted_valid)}"
            ),
        )


@router.get("/routers", tags=["routers"])
async def get_intelligent_routers():
    """
    Get available intelligent routers with configurations.

    This endpoint provides documentation for router:* model strings,
    enabling frontend auto-discovery of intelligent routing capabilities.

    Returns router information including:
    - id: Unique router identifier
    - name: Display name for UI
    - description: Router purpose and capabilities
    - modes: Available optimization modes
    - syntax: Primary and alias syntax patterns
    - use_cases: Recommended use cases
    """
    try:
        routers = [
            {
                "id": "general",
                "name": "General Router",
                "description": "NotDiamond-powered intelligent routing for general-purpose tasks",
                "powered_by": "NotDiamond",
                "modes": [
                    {
                        "value": "balanced",
                        "label": "Balanced",
                        "description": "Balance quality, cost, and latency",
                    },
                    {
                        "value": "quality",
                        "label": "Quality",
                        "description": "Optimize for response quality",
                    },
                    {
                        "value": "cost",
                        "label": "Cost",
                        "description": "Optimize for lowest cost",
                    },
                    {
                        "value": "latency",
                        "label": "Latency",
                        "description": "Optimize for fastest response",
                    },
                ],
                "syntax": {
                    "primary": "router:general:<mode>",
                    "examples": [
                        "router:general",
                        "router:general:quality",
                        "router:general:cost",
                        "router:general:latency",
                    ],
                    "aliases": [
                        "gatewayz-general",
                        "gatewayz-general-quality",
                        "gatewayz-general-cost",
                        "gatewayz-general-latency",
                    ],
                },
                "use_cases": [
                    "creative writing",
                    "summarization",
                    "Q&A",
                    "general chat",
                    "data analysis",
                ],
            },
            {
                "id": "code",
                "name": "Code Router",
                "description": "Code-optimized routing with benchmark-based model selection",
                "powered_by": "Gatewayz",
                "modes": [
                    {
                        "value": "auto",
                        "label": "Auto",
                        "description": "Automatically select best model for task",
                    },
                    {
                        "value": "price",
                        "label": "Price",
                        "description": "Optimize for lowest cost",
                    },
                    {
                        "value": "quality",
                        "label": "Quality",
                        "description": "Optimize for highest quality",
                    },
                    {
                        "value": "agentic",
                        "label": "Agentic",
                        "description": "Premium models for complex multi-step tasks",
                    },
                ],
                "syntax": {
                    "primary": "router:code:<mode>",
                    "examples": [
                        "router:code",
                        "router:code:price",
                        "router:code:quality",
                        "router:code:agentic",
                    ],
                    "aliases": [
                        "gatewayz-code",
                        "gatewayz-code-price",
                        "gatewayz-code-quality",
                        "gatewayz-code-agentic",
                    ],
                },
                "use_cases": [
                    "code generation",
                    "debugging",
                    "refactoring",
                    "code review",
                    "architecture design",
                ],
            },
        ]

        return {
            "data": routers,
            "total": len(routers),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error fetching routers: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch routers: {str(e)}",
        )


@router.get("/gateways", tags=["gateways"])
async def get_gateways():
    """
    Get all available gateway providers with their display configuration.

    This endpoint enables frontend auto-discovery of gateways, eliminating
    the need to manually update the frontend when new gateways are added.

    Returns a list of gateway configurations including:
    - id: Unique gateway identifier
    - name: Display name for UI
    - color: Tailwind CSS color class for badges
    - priority: 'fast' or 'slow' for loading order
    - site_url: Gateway's website URL
    - logo_url: Auto-generated favicon URL
    - aliases: Alternative IDs that map to this gateway
    """
    try:
        gateways = []
        for gateway_id, config in GATEWAY_REGISTRY.items():
            site_url = config.get("site_url", "")
            # Generate logo URL from site_url
            logo_url = None
            if site_url:
                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(site_url)
                    domain = parsed.netloc or parsed.path
                    if domain.startswith("www."):
                        domain = domain[4:]
                    logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                except Exception as e:
                    logger.warning(
                        f"Failed to generate logo URL for {gateway_id} from {site_url}: {e}"
                    )

            gateways.append(
                {
                    "id": gateway_id,
                    "name": config.get("name", gateway_id.title()),
                    "color": config.get("color", "bg-gray-500"),
                    "priority": config.get("priority", "slow"),
                    "site_url": site_url,
                    "logo_url": logo_url,
                    "icon": config.get("icon"),
                    "aliases": config.get("aliases", []),
                }
            )

        # Sort by priority (fast first), then by name
        gateways.sort(key=lambda g: (0 if g["priority"] == "fast" else 1, g["name"]))

        # Add intelligent routers section
        routers = [
            {
                "id": "general",
                "name": "General Router",
                "description": "NotDiamond-powered intelligent routing for general-purpose tasks",
                "modes": ["balanced", "quality", "cost", "latency"],
                "syntax": {
                    "primary": "router:general:<mode>",
                    "examples": [
                        "router:general",
                        "router:general:quality",
                        "router:general:cost",
                        "router:general:latency",
                    ],
                    "aliases": [
                        "gatewayz-general",
                        "gatewayz-general-quality",
                        "gatewayz-general-cost",
                        "gatewayz-general-latency",
                    ],
                },
                "use_cases": [
                    "creative writing",
                    "summarization",
                    "Q&A",
                    "general chat",
                    "data analysis",
                ],
            },
            {
                "id": "code",
                "name": "Code Router",
                "description": "Code-optimized routing with benchmark-based model selection",
                "modes": ["auto", "price", "quality", "agentic"],
                "syntax": {
                    "primary": "router:code:<mode>",
                    "examples": [
                        "router:code",
                        "router:code:price",
                        "router:code:quality",
                        "router:code:agentic",
                    ],
                    "aliases": [
                        "gatewayz-code",
                        "gatewayz-code-price",
                        "gatewayz-code-quality",
                        "gatewayz-code-agentic",
                    ],
                },
                "use_cases": [
                    "code generation",
                    "debugging",
                    "refactoring",
                    "code review",
                    "architecture design",
                ],
            },
        ]

        return {
            "data": gateways,
            "routers": routers,
            "total": len(gateways),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error fetching gateways: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch gateways: {str(e)}",
        )


@router.get("/gateways/status", tags=["gateways"])
async def get_gateways_status():
    """
    Get diagnostic status for all gateways including error states and model counts.

    This endpoint helps diagnose why gateways may be returning 0 models by showing:
    - Current error state (if in circuit breaker)
    - Cached model count
    - Cache age
    - Last error message (if any)
    """
    from src.services.model_catalog_cache import (
        get_gateway_error_message,
        is_gateway_in_error_state,
    )

    try:
        status_list = []
        for gateway_id, config in GATEWAY_REGISTRY.items():
            # Get error state
            in_error_state = is_gateway_in_error_state(gateway_id)
            error_message = get_gateway_error_message(gateway_id) if in_error_state else None

            # Get cached model count
            try:
                models = await asyncio.to_thread(get_cached_models, gateway_id)
                model_count = len(models) if models else 0
            except Exception as e:
                model_count = 0
                if not in_error_state:
                    error_message = str(e)
                logger.warning(
                    "Failed to get cached model count for gateway '%s': %s", gateway_id, e
                )

            status_list.append(
                {
                    "id": gateway_id,
                    "name": config.get("name", gateway_id.title()),
                    "model_count": model_count,
                    "in_error_state": in_error_state,
                    "error_message": error_message,
                    "priority": config.get("priority", "slow"),
                }
            )

        # Sort by model count (0 first to highlight issues)
        status_list.sort(key=lambda g: (g["model_count"], g["name"]))

        # Summary stats
        total_models = sum(g["model_count"] for g in status_list)
        gateways_with_errors = sum(1 for g in status_list if g["in_error_state"])
        gateways_with_zero = sum(1 for g in status_list if g["model_count"] == 0)

        return {
            "data": status_list,
            "summary": {
                "total_gateways": len(status_list),
                "total_models": total_models,
                "gateways_with_errors": gateways_with_errors,
                "gateways_with_zero_models": gateways_with_zero,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error fetching gateway status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch gateway status: {str(e)}",
        )


def normalize_developer_segment(value: str | None) -> str | None:
    """Align developer/provider identifiers with Hugging Face style slugs."""
    if value is None:
        return None
    # Convert to string if it's a Query object or other type
    value = str(value) if not isinstance(value, str) else value
    normalized = value.strip()
    if not normalized:
        return None
    # Remove leading @ that some gateways include
    normalized = normalized.lstrip("@")
    return normalized


def normalize_model_segment(value: str | None) -> str | None:
    """Normalize model identifiers without altering intentional casing."""
    if value is None:
        return None
    # Convert to string if it's a Query object or other type
    value = str(value) if not isinstance(value, str) else value
    normalized = value.strip()
    return normalized or None


def annotate_provider_sources(providers: list[dict], source: str) -> list[dict]:
    annotated = []
    for provider in providers or []:
        entry = provider.copy()
        entry.setdefault("source_gateway", source)
        entry.setdefault("source_gateways", [source])
        if source not in entry["source_gateways"]:
            entry["source_gateways"].append(source)
        annotated.append(entry)
    return annotated


# Cache for derive_providers_from_models results.
# Key: (gateway_name, models_hash) -> (result, timestamp)
_derived_providers_cache: dict[tuple, tuple] = {}
_DERIVED_PROVIDERS_TTL = 300  # 5 minutes


def derive_providers_from_models(models: list[dict], gateway_name: str) -> list[dict]:
    """
    Generic function to derive provider list from model list for any gateway.
    Used for gateways that don't have a dedicated provider endpoint.

    Results are cached for 5 minutes per (gateway_name, models_hash) pair to
    avoid re-iterating the full model list on every call.
    """
    # Build a cheap cache key from the gateway name and a hash of model ids.
    model_ids = [m.get("id", "") for m in (models or [])]
    models_hash = hashlib.md5(
        json.dumps(model_ids, sort_keys=True).encode(), usedforsecurity=False
    ).hexdigest()
    cache_key = (gateway_name, models_hash)

    now = time.monotonic()
    cached = _derived_providers_cache.get(cache_key)
    if cached is not None:
        result, ts = cached
        if now - ts < _DERIVED_PROVIDERS_TTL:
            return result

    providers: dict[str, dict] = {}
    for model in models or []:
        # Try different fields to get provider name
        provider_slug = None

        # Try provider_slug field
        provider_slug = model.get("provider_slug") or model.get("provider")

        # Try extracting from model ID (format: provider/model-name)
        if not provider_slug:
            model_id = model.get("id", "")
            if "/" in model_id:
                provider_slug = model_id.split("/")[0]

        # Try name field
        if not provider_slug:
            name = model.get("name", "")
            if "/" in name:
                provider_slug = name.split("/")[0]

        if not provider_slug:
            continue

        # Clean up slug — use canonical normalization from src/services/models.py
        provider_slug = normalize_provider_slug(provider_slug)

        if provider_slug not in providers:
            providers[provider_slug] = {
                "slug": provider_slug,
                "site_url": model.get("provider_site_url"),
                "logo_url": model.get("model_logo_url") or model.get("logo_url"),
                "moderated_by_openrouter": False,
                "source_gateway": gateway_name,
                "source_gateways": [gateway_name],
            }

    result = list(providers.values())
    # Evict stale entries to prevent unbounded memory growth
    stale_keys = [
        k for k, (_, ts) in _derived_providers_cache.items() if now - ts >= _DERIVED_PROVIDERS_TTL
    ]
    for k in stale_keys:
        _derived_providers_cache.pop(k, None)
    _derived_providers_cache[cache_key] = (result, now)
    return result


def merge_provider_lists(*provider_lists: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for providers in provider_lists:
        for provider in providers or []:
            slug = provider.get("slug")
            if not slug:
                continue
            if slug not in merged:
                copied = provider.copy()
                sources_seen: dict[str, None] = dict.fromkeys(
                    s for s in (copied.get("source_gateways") or []) if s
                )
                source = copied.get("source_gateway")
                if source:
                    sources_seen[source] = None
                copied["source_gateways"] = list(sources_seen)
                merged[slug] = copied
            else:
                existing = merged[slug]
                sources_seen = dict.fromkeys(existing.get("source_gateways") or [])
                for src in provider.get("source_gateways") or []:
                    if src:
                        sources_seen[src] = None
                source = provider.get("source_gateway")
                if source:
                    sources_seen[source] = None
                existing["source_gateways"] = list(sources_seen)
    return list(merged.values())


def merge_models_by_slug(*model_lists: list[dict]) -> list[dict]:
    """Merge multiple model lists by slug, avoiding duplicates"""
    merged = []
    seen = set()
    for model_list in model_lists:
        for model in model_list or []:
            key = (model.get("canonical_slug") or model.get("id") or "").lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(model)
    return merged


# Provider and Models Information Endpoints
@router.get("/provider", tags=["providers"])
async def get_providers(
    moderated_only: bool = Query(False, description="Filter for moderated providers only"),
    limit: int = Query(100, ge=1, le=500, description=DESC_LIMIT_NUMBER_OF_RESULTS),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    gateway: str | None = Query(
        "all",
        description=DESC_GATEWAY_WITH_ALL,
    ),
):
    """Get all available provider list with detailed metric data including model count and logo URLs"""
    try:
        gateway_value = (gateway or "all").lower()
        # Resolve any alias to its canonical fetch slug (e.g. "google" → "google-vertex")
        gateway_value = _GATEWAY_SLUG_RESOLUTION.get(gateway_value, gateway_value)

        openrouter_models = []
        provider_groups: list[list[dict]] = []

        if gateway_value in ("openrouter", "all"):
            raw_providers = await asyncio.to_thread(get_cached_providers)
            if not raw_providers and gateway_value == "openrouter":
                # Graceful degradation - log warning and continue with empty providers
                logger.warning("OpenRouter provider data unavailable - returning empty response")

            enhanced_openrouter = annotate_provider_sources(
                enhance_providers_with_logos_and_sites(raw_providers or []),
                "openrouter",
            )
            provider_groups.append(enhanced_openrouter)
            openrouter_models = await asyncio.to_thread(get_cached_models, "openrouter") or []

        # Add support for other gateways
        other_gateways = [
            "featherless",
            "deepinfra",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "google-vertex",
            "fal",
            "aihubmix",
            "anannas",
            "vercel-ai-gateway",
            "simplismart",
        ]
        all_models = {}  # Track models for each gateway

        for gw in other_gateways:
            if gateway_value in (gw, "all"):
                gw_models = await asyncio.to_thread(get_cached_models, gw) or []
                all_models[gw] = gw_models
                if gw_models:
                    derived_providers = derive_providers_from_models(gw_models, gw)
                    provider_groups.append(derived_providers)
                elif gateway_value == gw:
                    # Don't raise 503 - graceful degradation, return empty response
                    logger.warning(
                        f"{gw.capitalize()} models data unavailable - returning empty response"
                    )

        if not provider_groups:
            # Graceful degradation - return empty response instead of 503
            logger.warning(
                f"No provider data available for gateway={gateway_value} - returning empty response"
            )
            return {
                "data": [],
                "total": 0,
                "returned": 0,
                "offset": offset or 0,
                "limit": limit,
                "gateway": gateway_value,
                "timestamp": datetime.now(UTC).isoformat(),
            }

        combined_providers = merge_provider_lists(*provider_groups)

        models_for_counts: list[dict] = []
        if gateway_value in ("openrouter", "all"):
            models_for_counts.extend(openrouter_models)
        # Add models from other gateways for counting
        for gw, models in all_models.items():
            if gateway_value in (gw, "all"):
                models_for_counts.extend(models)

        if moderated_only:
            combined_providers = [
                provider
                for provider in combined_providers
                if provider.get("moderated_by_openrouter")
            ]

        total_providers = len(combined_providers)
        if offset:
            combined_providers = combined_providers[offset:]
        if limit:
            combined_providers = combined_providers[:limit]

        for provider in combined_providers:
            provider_slug = provider.get("slug")
            provider["model_count"] = get_model_count_by_provider(provider_slug, models_for_counts)

        return {
            "data": combined_providers,
            "total": total_providers,
            "returned": len(combined_providers),
            "offset": offset or 0,
            "limit": limit,
            "gateway": gateway_value,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get providers: {e}")
        raise HTTPException(status_code=500, detail="Failed to get providers")


# ============================================================================
# INTERNAL HELPER FUNCTIONS (Used by public API endpoints below)
# These functions contain the actual logic but are not directly exposed as routes
# ============================================================================


async def get_models(
    provider: str | None = Query(None, description="Filter models by provider"),
    is_private: bool | None = Query(
        None,
        description="Filter by private models: true=private only, false=non-private only, null=all models",
    ),
    limit: int | None = Query(None, ge=1, le=1000, description=DESC_LIMIT_NUMBER_OF_RESULTS),
    offset: int | None = Query(0, ge=0, description=DESC_OFFSET_FOR_PAGINATION),
    include_huggingface: bool = Query(
        True, description="Include Hugging Face metrics for models that have hugging_face_id"
    ),
    gateway: str | None = Query(
        "all",
        description=DESC_GATEWAY_WITH_ALL,
    ),
    unique_models: bool = Query(
        False,
        description=(
            "Return deduplicated models with provider arrays. "
            "Only applies when gateway='all'. Default: False (flat catalog). "
            "When true, each model appears once with an array of providers offering it."
        ),
    ),
):
    """Get all metric data of available models with optional filtering, pagination, Hugging Face integration, and provider logos"""

    try:
        _validate_gateway(gateway)
        provider = normalize_developer_segment(provider)
        logger.debug(
            f"/models endpoint called with gateway parameter: {repr(gateway)}, unique_models={unique_models}"
        )
        gateway_value = (gateway or "all").lower()
        # Resolve any alias to its canonical fetch slug (e.g. "google" → "google-vertex")
        gateway_value = _GATEWAY_SLUG_RESOLUTION.get(gateway_value, gateway_value)
        logger.debug(
            f"Getting models with provider={provider}, limit={limit}, offset={offset}, gateway={gateway_value}"
        )

        # PERFORMANCE: Check response cache FIRST (before expensive provider fetches)
        # This provides 5-10ms response times for cached requests (vs 500ms-2s uncached)
        from src.services.catalog_response_cache import (
            cache_catalog_response,
            get_cached_catalog_response,
        )

        # Build cache params from all request parameters
        cache_params = {
            "limit": limit,
            "offset": offset,
            "provider": provider,
            "is_private": is_private,
            "include_huggingface": include_huggingface,
            "unique_models": unique_models,
        }

        # Try to get from cache
        cached_response = await get_cached_catalog_response(gateway_value, cache_params)
        if cached_response:
            logger.info(f"✅ Returning cached response for gateway={gateway_value}")
            cached_json = _dumps_fast(cached_response)
            etag_data = json.dumps(cached_response.get("data", [])[:5], sort_keys=True)
            etag_value = hashlib.md5(etag_data.encode(), usedforsecurity=False).hexdigest()[:16]
            return Response(
                content=cached_json,
                media_type="application/json",
                headers={
                    "Cache-Control": "private, max-age=60, must-revalidate",
                    "ETag": f'"{etag_value}"',
                    "Vary": "Accept-Encoding",
                    "X-Cache": "HIT",
                },
            )

        openrouter_models: list[dict] = []

        # OPTIMIZATION: For gateway=all, use the aggregated multi-provider cache
        # This uses the parallel fetch with 45s timeout and proper caching
        # Instead of calling each provider individually (slow!)
        if gateway_value == "all":
            logger.info(
                f"Using aggregated multi-provider catalog cache for gateway=all (unique_models={unique_models})"
            )
            all_models_from_cache = (
                await asyncio.to_thread(get_cached_models, "all", use_unique_models=unique_models)
                or []
            )
            # The aggregated catalog is returned directly without individual provider calls
            # We still need to populate all_models dict for the response structure
            # The models already have source_gateway field populated
            # Return early to skip individual provider fetches
            if all_models_from_cache:
                logger.info(f"Returning {len(all_models_from_cache)} models from aggregated cache")
                # For gateway=all, we skip the individual provider logic and filter/paginate
                # the aggregated result directly
                all_models_list = all_models_from_cache
            else:
                # PERFORMANCE FIX: Use parallel fetching instead of sequential
                # This reduces fetch time from 25*60s = 1500s to ~30s max
                # Each provider has 15s timeout enforced via nested executor
                logger.warning("Aggregated cache returned empty, using PARALLEL provider fetches")
                try:
                    from src.services.parallel_catalog_fetch import fetch_and_merge_all_providers

                    all_models_list = merge_models_by_slug(
                        await fetch_and_merge_all_providers(timeout=30.0)
                    )
                    logger.info(f"Parallel fetch returned {len(all_models_list)} models")
                except Exception as e:
                    logger.error(f"Parallel fetch failed: {e}")
                    all_models_list = []
        else:
            all_models_list = []
            # Log warning if unique_models is requested for non-all gateway
            if unique_models:
                logger.debug(
                    f"unique_models=True ignored for gateway='{gateway_value}' "
                    "(only applies to gateway='all')"
                )

        # Skip individual provider fetches if we already have the aggregated list
        skip_individual_fetches = gateway_value == "all" and all_models_list

        # Import circuit breaker for individual provider fetches
        from src.utils.circuit_breaker import get_provider_circuit_breaker

        breaker = get_provider_circuit_breaker()

        # Individual provider fetches (only used when gateway != "all" OR cache is empty)
        # Skip if parallel fetch already populated all_models_list
        if gateway_value in ("openrouter", "all") and not (
            gateway_value == "all" and all_models_list
        ):
            if not breaker.should_skip("openrouter"):
                try:
                    openrouter_models = get_cached_models("openrouter") or []
                    # Record success even for empty results - empty is not a failure
                    breaker.record_success("openrouter")
                    if not openrouter_models:
                        logger.warning(
                            "OpenRouter models unavailable (gateway=%s) - possible causes: "
                            "API key not configured, API is down, network issues, or cache warming incomplete. "
                            "Continuing with other providers if available.",
                            gateway_value,
                        )
                except Exception as e:
                    # Only record failure for actual exceptions
                    breaker.record_failure("openrouter", str(e))
                    logger.error(f"Error fetching openrouter models: {e}")
                    openrouter_models = []
            else:
                logger.info("Skipping openrouter (circuit breaker open)")

        # Fetch models for all providers in PROVIDER_SLUGS using the registry-derived list.
        # "openrouter" is handled separately above (circuit breaker); include its result here
        # so that provider_models is the single source of truth for all downstream code.
        provider_models: dict[str, list[dict]] = {"openrouter": openrouter_models}

        if not skip_individual_fetches:
            for _slug in PROVIDER_SLUGS:
                if _slug == "openrouter":
                    # Already fetched above with circuit-breaker logic; skip here.
                    continue
                if gateway_value not in (_slug, "all"):
                    continue
                _fetched = get_cached_models(_slug) or []
                provider_models[_slug] = _fetched
                if not _fetched and gateway_value == _slug:
                    logger.warning("%s models unavailable - continuing without them", _slug)

        # Select the model list for the requested gateway.
        if gateway_value != "all":
            models = provider_models.get(gateway_value, [])
        else:
            # For "all" gateway, use aggregated cache if available
            # This is much faster than merging individual provider models
            if all_models_list:
                logger.info(
                    f"Using aggregated cache for gateway=all ({len(all_models_list)} models)"
                )
                models = all_models_list
            else:
                # Fallback: merge individual provider models (slow path)
                logger.warning("Aggregated cache unavailable, merging individual provider models")
                models = merge_models_by_slug(*provider_models.values())

        if not models:
            if gateway_value == "nebius":
                logger.info(
                    "Returning empty Nebius catalog response because no public model listing exists"
                )
            elif gateway_value == "xai":
                logger.info(
                    "Returning empty xAI catalog response because no public model listing exists"
                )
            else:
                logger.warning(
                    "No models available for gateway=%s. Returning empty response. "
                    "This may indicate provider API keys not configured or all providers are down.",
                    gateway_value,
                )
                # Instead of raising 503, return an empty but valid response
                # This prevents the entire API from failing when a single provider is unavailable

        provider_groups: list[list[dict]] = []

        if gateway_value in ("openrouter", "all"):
            providers = await asyncio.to_thread(get_cached_providers)
            if not providers and gateway_value == "openrouter":
                logger.warning(
                    "OpenRouter provider data unavailable - returning empty providers list"
                )
            enhanced_providers = annotate_provider_sources(
                enhance_providers_with_logos_and_sites(providers or []),
                "openrouter",
            )
            provider_groups.append(enhanced_providers)

        # Derive provider groups for all other fetched slugs using provider_models.
        for _slug in PROVIDER_SLUGS:
            if _slug == "openrouter":
                continue  # handled above with special OpenRouter provider list logic
            if gateway_value not in (_slug, "all"):
                continue
            _slug_models = provider_models.get(_slug, [])
            _models_for_providers = _slug_models if gateway_value == "all" else models
            if _models_for_providers:
                _derived = derive_providers_from_models(_models_for_providers, _slug)
                _annotated = annotate_provider_sources(_derived, _slug)
                provider_groups.append(_annotated)

        enhanced_providers = merge_provider_lists(*provider_groups)
        logger.info(f"Retrieved {len(enhanced_providers)} enhanced providers from cache")

        if provider:
            provider_lower = provider.lower()
            original_count = len(models)
            filtered_models = []
            for model in models:
                model_id = (model.get("id") or "").lower()
                provider_slug = normalize_provider_slug(model.get("provider_slug") or "")
                if model_id.startswith(provider_lower + "/") or provider_lower == provider_slug:
                    filtered_models.append(model)
            models = filtered_models
            logger.info(
                f"Filtered models by provider '{provider}': {original_count} -> {len(models)}"
            )

        # Filter by is_private flag
        if is_private is not None:
            original_count = len(models)
            if is_private:
                # Only show private models (Near AI models)
                models = [m for m in models if m.get("is_private") is True]
                logger.info(f"Filtered for private models only: {original_count} -> {len(models)}")
            else:
                # Only show non-private models
                models = [m for m in models if not m.get("is_private")]
                logger.info(
                    f"Filtered to exclude private models: {original_count} -> {len(models)}"
                )

        total_models = len(models)
        logger.info(f"Catalog response: {total_models} total models, gateway={gateway_value}")
        try:
            set_gateway_model_count(gateway_value, total_models)
        except Exception:
            pass  # Never let metrics update block the response

        # Ensure offset and limit are integers
        try:
            offset_int = int(str(offset)) if offset else 0
            limit_int = int(str(limit)) if limit else None
        except (ValueError, TypeError) as e:
            logger.warning(
                "Invalid pagination parameters (offset=%r, limit=%r), using defaults: %s",
                offset,
                limit,
                e,
            )
            offset_int = 0
            limit_int = None

        # Apply pagination (offset and limit)
        if offset_int:
            models = models[offset_int:]
            logger.info(f"Applied offset {offset_int}: {len(models)} models remaining")

        if limit_int:
            models = models[:limit_int]
            logger.info(
                f"Applied limit {limit_int}: returning {len(models)} models (total available: {total_models})"
            )

        # Optimize model enhancement for fast response
        # Only enhance with provider info (fast operation)
        enhanced_models = []
        for model in models:
            enhanced_model = enhance_model_with_provider_info(model, enhanced_providers)
            enhanced_models.append(enhanced_model)

        # If HuggingFace data requested, fetch it asynchronously in background
        # This allows the response to return immediately without waiting
        if include_huggingface:
            # Schedule background task to enrich with HF data
            # Note: In production, this would use a background task queue
            # For now, we'll enrich a limited subset to avoid blocking
            for i, model in enumerate(
                enhanced_models[:10]
            ):  # Only enrich first 10 to keep response fast
                try:
                    enhanced_models[i] = enhance_model_with_huggingface_data(model)
                except Exception as e:
                    logger.debug(f"Failed to enrich model {model.get('id')} with HF data: {e}")
                    # Continue without HF data if fetch fails

        note = {
            "openrouter": "OpenRouter catalog",
            "onerouter": "Infron AI catalog",
            "featherless": "Featherless catalog",
            "deepinfra": "DeepInfra catalog",
            "chutes": "Chutes.ai catalog",
            "groq": "Groq catalog",
            "fireworks": "Fireworks catalog",
            "together": "Together catalog",
            "google-vertex": "Google Vertex AI catalog",
            "cerebras": "Cerebras catalog",
            "nebius": "Nebius catalog (no public listing is currently available)",
            "xai": "Xai catalog",
            "novita": "Novita catalog",
            "hug": "Hugging Face catalog",
            "aimo": "AIMO Network catalog",
            "near": "Near AI catalog",
            "fal": "Fal.ai catalog",
            "anannas": "Anannas catalog",
            "aihubmix": "AiHubMix catalog",
            "vercel-ai-gateway": "Vercel AI Gateway catalog",
            "simplismart": "Simplismart catalog",
            "all": "Combined OpenRouter, Featherless, DeepInfra, Chutes, Groq, Fireworks, Together, Google Vertex AI, Cerebras, Nebius, Xai, Novita, Hugging Face, AIMO, Near AI, Fal.ai, Anannas, AiHubMix, Infron AI, Vercel AI Gateway, and Simplismart catalogs",
        }.get(gateway_value, "OpenRouter catalog")

        # Calculate pagination metadata
        has_more = (offset_int + len(enhanced_models)) < total_models
        next_offset = offset_int + len(enhanced_models) if has_more else None

        result = {
            "data": enhanced_models,
            "total": total_models,
            "returned": len(enhanced_models),
            "offset": offset_int,
            "limit": limit_int,
            "has_more": has_more,
            "next_offset": next_offset,
            "include_huggingface": include_huggingface,
            "gateway": gateway_value,
            "note": note,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.debug(
            f"Returning /models response with keys: {list(result.keys())}, gateway={gateway_value}, first_model={enhanced_models[0]['id'] if enhanced_models else 'none'}"
        )

        # Cache the response for future requests
        try:
            await cache_catalog_response(gateway_value, cache_params, result)
        except Exception as cache_error:
            logger.warning(f"Failed to cache response: {cache_error}")

        # Return response with cache headers
        # Use private caching to prevent CDN issues after deployments
        # Cache for 60 seconds in browser only (not CDN)
        return Response(
            content=_dumps_fast(result),
            media_type="application/json",
            headers={
                "Cache-Control": "private, max-age=60, must-revalidate",  # Browser cache only
                "ETag": f'"{hashlib.md5(json.dumps(enhanced_models[:5], sort_keys=True).encode(), usedforsecurity=False).hexdigest()[:16]}"',
                "Vary": "Accept-Encoding",  # Vary cache by encoding
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get models: {e}")
        raise HTTPException(status_code=500, detail="Failed to get models")


async def get_specific_model(
    provider_name: str,
    model_name: str,
    include_huggingface: bool = Query(True, description=DESC_INCLUDE_HUGGINGFACE),
    gateway: str | None = Query(
        None,
        description=DESC_GATEWAY_AUTO_DETECT,
    ),
):
    """Get specific model data of a given provider with detailed information from any gateway

    This endpoint supports fetching model data from multiple model providers:
    - OpenRouter: Full model endpoint data including performance metrics
    - Featherless: Model catalog data
    - DeepInfra: Model catalog data from DeepInfra's API
    - Chutes: Model catalog data from Chutes.ai
    - Fal.ai: Image/video/audio generation models (e.g., fal-ai/stable-diffusion-v15)
    - Hugging Face: Open-source models from Hugging Face Hub
    - And other gateways: groq, fireworks, together, cerebras, nebius, xai, novita, aimo, near

    If gateway is not specified, it will automatically detect which gateway the model belongs to.

    Examples:
        GET /v1/models/openai/gpt-4?gateway=openrouter
        GET /v1/models/meta-llama/llama-3?gateway=featherless
        GET /v1/models/fal-ai/stable-diffusion-v15?gateway=fal
        GET /v1/models/fal-ai/stable-diffusion-v15 (auto-detects fal gateway)
    """
    # Prevent this route from catching /v1/* API endpoints
    normalized_provider = normalize_developer_segment(provider_name) or provider_name
    normalized_model = normalize_model_segment(model_name) or model_name

    provider_name = normalized_provider
    model_name = normalized_model

    if provider_name == "v1":
        raise HTTPException(status_code=404, detail=f"Model {provider_name}/{model_name} not found")

    try:
        # Fetch model data from appropriate gateway
        model_data = fetch_specific_model(provider_name, model_name, gateway)

        if not model_data:
            gateway_msg = f" from gateway '{gateway}'" if gateway else ""
            raise HTTPException(
                status_code=404, detail=f"Model {provider_name}/{model_name} not found{gateway_msg}"
            )

        # Determine which gateway was used
        detected_gateway = model_data.get("source_gateway", gateway or "all")

        # Get enhanced providers data for all gateways
        provider_groups: list[list[dict]] = []

        # Always try to get OpenRouter providers for cross-reference
        openrouter_providers = await asyncio.to_thread(get_cached_providers)
        if openrouter_providers:
            enhanced_openrouter = annotate_provider_sources(
                enhance_providers_with_logos_and_sites(openrouter_providers),
                "openrouter",
            )
            provider_groups.append(enhanced_openrouter)

        # Add providers from other gateways based on detected gateway
        if detected_gateway in [
            "featherless",
            "deepinfra",
            "chutes",
            "groq",
            "fireworks",
            "together",
        ]:
            # Get models from the detected gateway to derive providers
            gateway_models = get_cached_models(detected_gateway)
            if gateway_models:
                derived_providers = derive_providers_from_models(gateway_models, detected_gateway)
                annotated_providers = annotate_provider_sources(derived_providers, detected_gateway)
                provider_groups.append(annotated_providers)

        # Handle gateways that use derive_providers_from_models
        if detected_gateway in [
            "cerebras",
            "nebius",
            "xai",
            "novita",
            "hug",
            "aimo",
            "near",
            "fal",
            "anannas",
            "aihubmix",
            "vercel-ai-gateway",
            "simplismart",
        ]:
            gateway_models = get_cached_models(detected_gateway)
            if gateway_models:
                derived_providers = derive_providers_from_models(gateway_models, detected_gateway)
                annotated_providers = annotate_provider_sources(derived_providers, detected_gateway)
                provider_groups.append(annotated_providers)

        # Merge all provider data
        enhanced_providers = merge_provider_lists(*provider_groups) if provider_groups else []

        # Enhance with provider information and logos
        if isinstance(model_data, dict):
            model_data = enhance_model_with_provider_info(model_data, enhanced_providers)

            # Then enhance with Hugging Face data if requested
            if include_huggingface and model_data.get("hugging_face_id"):
                model_data = enhance_model_with_huggingface_data(model_data)

        return {
            "data": model_data,
            "provider": provider_name,
            "model": model_name,
            "gateway": detected_gateway,
            "include_huggingface": include_huggingface,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get specific model %s/%s: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail="Failed to get model data")


async def get_developer_models(
    developer_name: str,
    limit: int = Query(100, ge=1, le=1000, description=DESC_LIMIT_NUMBER_OF_RESULTS),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    include_huggingface: bool = Query(True, description="Include Hugging Face metrics"),
    gateway: str | None = Query("all", description="Gateway: 'openrouter' or 'all'"),
):
    """
    Get all models from a specific developer/provider (e.g., anthropic, openai, meta)

    Args:
        developer_name: Provider/developer name (e.g., 'anthropic', 'openai', 'meta')
        limit: Maximum number of models to return
        offset: Number of models to skip (for pagination)
        include_huggingface: Whether to include HuggingFace metrics
        gateway: Which gateway to query ('openrouter' or 'all')

    Returns:
        JSON response with:
        - models: List of model objects from the developer
        - developer: Developer name
        - total: Total number of models found
        - count: Number of models returned (after pagination)

    Example:
        GET /catalog/developer/anthropic/models
        GET /catalog/developer/openai/models?limit=10
    """
    try:
        from src.services.catalog_response_cache import (
            cache_catalog_response,
            get_cached_catalog_response,
        )

        developer_name = normalize_developer_segment(developer_name) or developer_name
        logger.info("Getting models for developer: %s", sanitize_for_logging(developer_name))

        # Get models from specified gateway
        gateway_value = (gateway or "all").lower()

        # Build cache params for this request
        cache_params = {
            "developer": developer_name,
            "limit": limit,
            "offset": offset,
            "include_huggingface": include_huggingface,
        }

        # Try to get from cache
        cached_response = await get_cached_catalog_response(gateway_value, cache_params)
        if cached_response:
            logger.info(f"Returning cached developer models for {developer_name}")
            return cached_response

        models = get_cached_models(gateway_value)

        if not models:
            raise HTTPException(status_code=503, detail=ERROR_MODELS_DATA_UNAVAILABLE)

        # Filter models by developer/provider
        developer_lower = developer_name.lower()
        filtered_models = []

        for model in models:
            model_id = (model.get("id") or "").lower()
            provider_slug = normalize_provider_slug(model.get("provider_slug") or "")

            # Check if model ID starts with developer name (e.g., "anthropic/claude-3")
            # or if provider_slug matches
            if model_id.startswith(f"{developer_lower}/") or provider_slug == developer_lower:
                filtered_models.append(model)

        if not filtered_models:
            logger.warning(
                "No models found for developer: %s", sanitize_for_logging(developer_name)
            )
            return {
                "developer": developer_name,
                "models": [],
                "total": 0,
                "count": 0,
                "offset": offset,
                "limit": limit,
            }

        total_models = len(filtered_models)
        logger.info(
            "Found %d models for developer '%s'", total_models, sanitize_for_logging(developer_name)
        )

        # Apply pagination
        if offset:
            filtered_models = filtered_models[offset:]
        if limit:
            filtered_models = filtered_models[:limit]

        # Enhance models with provider info and HuggingFace data
        providers = await asyncio.to_thread(get_cached_providers)
        enhanced_providers = enhance_providers_with_logos_and_sites(providers or [])

        enhanced_models = []
        for model in filtered_models:
            enhanced_model = enhance_model_with_provider_info(model, enhanced_providers)
            if include_huggingface and enhanced_model.get("hugging_face_id"):
                enhanced_model = enhance_model_with_huggingface_data(enhanced_model)
            enhanced_models.append(enhanced_model)

        # Build response
        response = {
            "developer": developer_name,
            "models": enhanced_models,
            "total": total_models,
            "count": len(enhanced_models),
            "offset": offset,
            "limit": limit,
            "gateway": gateway_value,
        }

        # Cache the response for future requests
        try:
            await cache_catalog_response(gateway_value, cache_params, response)
        except Exception as cache_error:
            logger.warning(f"Failed to cache response: {cache_error}")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get models for developer %s: %s",
            sanitize_for_logging(developer_name),
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail=f"Failed to get developer models: {str(e)}")


# ==================== NEW: Gateway & Provider Statistics Endpoints ====================


@router.get("/provider/{provider_name}/stats", tags=["statistics"])
async def get_provider_statistics(
    provider_name: str,
    gateway: str | None = Query(None, description="Filter by specific gateway"),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_ALL),
):
    """
    Get comprehensive statistics for a specific provider

    This endpoint provides usage statistics for a provider across all or a specific gateway.
    **This fixes the "Total Tokens: 0" and "Top Model: N/A" issues in your UI!**

    Args:
        provider_name: Provider name (e.g., 'openai', 'anthropic', 'meta-llama')
        gateway: Optional gateway filter
        time_range: Time range for statistics

    Returns:
        Provider statistics including:
        - Total requests and tokens
        - Total cost and averages
        - Top model used
        - Model breakdown
        - Speed metrics

    Example:
        GET /catalog/provider/openai/stats?time_range=24h
        GET /catalog/provider/anthropic/stats?gateway=openrouter&time_range=7d
    """
    try:
        logger.info(
            "Fetching stats for provider: %s, gateway: %s, time_range: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(gateway),
            sanitize_for_logging(time_range),
        )

        stats = get_provider_stats(
            provider_name=provider_name, gateway=gateway, time_range=time_range
        )

        if "error" in stats:
            raise HTTPException(status_code=500, detail=stats["error"])

        return {"success": True, "data": stats, "timestamp": datetime.now(UTC).isoformat()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get provider stats for %s: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail=f"Failed to get provider statistics: {str(e)}")


@router.get("/gateway/{gateway}/stats", tags=["statistics"])
async def get_gateway_statistics(
    gateway: str, time_range: str = Query("24h", description=DESC_TIME_RANGE_ALL)
):
    """
    Get comprehensive statistics for a specific gateway

    This endpoint provides usage statistics for a gateway (e.g., openrouter, groq, deepinfra).
    **This fixes the "Top Provider: N/A" issue in your UI!**

    Args:
        gateway: Gateway name ('openrouter', 'featherless', 'deepinfra', 'chutes', 'groq', 'google-vertex', 'helicone', etc.)
        time_range: Time range for statistics

    Returns:
        Gateway statistics including:
        - Total requests, tokens, and cost
        - Unique users, models, and providers
        - Top provider used through this gateway
        - Provider breakdown
        - Performance metrics

    Example:
        GET /catalog/gateway/openrouter/stats?time_range=24h
        GET /catalog/gateway/deepinfra/stats?time_range=7d
    """
    try:
        logger.info(
            "Fetching stats for gateway: %s, time_range: %s",
            sanitize_for_logging(gateway),
            sanitize_for_logging(time_range),
        )

        # Validate gateway
        valid_gateways = [
            "openrouter",
            "featherless",
            "deepinfra",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "google-vertex",
            "simplismart",
        ]
        if gateway.lower() not in valid_gateways:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid gateway. Must be one of: {', '.join(valid_gateways)}",
            )

        stats = get_gateway_stats(gateway=gateway, time_range=time_range)

        if "error" in stats:
            raise HTTPException(status_code=500, detail=stats["error"])

        return {"success": True, "data": stats, "timestamp": datetime.now(UTC).isoformat()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get gateway stats for %s: %s",
            sanitize_for_logging(gateway),
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail=f"Failed to get gateway statistics: {str(e)}")


async def get_trending_models_endpoint(
    gateway: str | None = Query("all", description="Gateway filter or 'all'"),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_NO_ALL),
    limit: int = Query(10, description=DESC_NUMBER_OF_MODELS_TO_RETURN, ge=1, le=100),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
    sort_by: str = Query("requests", description="Sort by: 'requests', 'tokens', 'users'"),
):
    """
    Get trending models based on usage

    This endpoint returns the most popular models sorted by usage metrics.
    **This helps populate "Top Model" in your UI!**

    Args:
        gateway: Gateway filter ('all' for all gateways)
        time_range: Time range for trending calculation
        limit: Number of models to return
        offset: Number of results to skip for pagination
        sort_by: Sort criteria ('requests', 'tokens', 'users')

    Returns:
        List of trending models with statistics

    Example:
        GET /catalog/models/trending?time_range=24h&limit=10
        GET /catalog/models/trending?gateway=deepinfra&sort_by=tokens
        GET /catalog/models/trending?limit=10&offset=10
    """
    try:
        _validate_gateway(gateway)
        logger.info(
            "Fetching trending models: gateway=%s, time_range=%s, sort_by=%s, offset=%d",
            sanitize_for_logging(gateway),
            sanitize_for_logging(time_range),
            sanitize_for_logging(sort_by),
            offset,
        )

        # Validate sort_by
        valid_sort = ["requests", "tokens", "users"]
        if sort_by not in valid_sort:
            raise HTTPException(
                status_code=400, detail=f"Invalid sort_by. Must be one of: {', '.join(valid_sort)}"
            )

        # Fetch more results than requested to support offset pagination
        trending = get_trending_models(
            gateway=gateway, time_range=time_range, limit=limit + offset, sort_by=sort_by
        )

        # Apply offset pagination
        total_count = len(trending)
        trending = trending[offset : offset + limit]

        return {
            "success": True,
            "data": trending,
            "count": len(trending),
            "gateway": gateway,
            "time_range": time_range,
            "sort_by": sort_by,
            "timestamp": datetime.now(UTC).isoformat(),
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "has_more": offset + limit < total_count,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get trending models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get trending models: {str(e)}")


@router.get("/gateways/summary", tags=["statistics"])
async def get_all_gateways_summary_endpoint(
    time_range: str = Query("24h", description=DESC_TIME_RANGE_ALL)
):
    """
    Get summary statistics for all gateways

    This endpoint provides a comprehensive overview of usage across all gateways.
    **Perfect for dashboard overview showing all providers!**

    Args:
        time_range: Time range for statistics

    Returns:
        Dictionary with statistics for each gateway and overall totals

    Example:
        GET /catalog/gateways/summary?time_range=24h
    """
    try:
        logger.info(
            "Fetching summary for all gateways: time_range=%s", sanitize_for_logging(time_range)
        )

        summary = get_all_gateways_summary(time_range=time_range)

        if "error" in summary:
            raise HTTPException(status_code=500, detail=summary["error"])

        return {
            "success": True,
            "data": summary,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get gateways summary: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get gateways summary: {str(e)}")


@router.get("/provider/{provider_name}/top-models", tags=["statistics"])
async def get_provider_top_models_endpoint(
    provider_name: str,
    limit: int = Query(5, description=DESC_NUMBER_OF_MODELS_TO_RETURN, ge=1, le=20),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_ALL),
):
    """
    Get top models for a specific provider

    This endpoint returns the most used models from a provider.

    Args:
        provider_name: Provider name (e.g., 'openai', 'anthropic')
        limit: Number of models to return
        offset: Number of results to skip for pagination
        time_range: Time range for statistics

    Returns:
        List of top models with usage statistics

    Example:
        GET /catalog/provider/openai/top-models?limit=5&time_range=7d
        GET /catalog/provider/openai/top-models?limit=5&offset=5&time_range=7d
    """
    try:
        provider_name = normalize_developer_segment(provider_name) or provider_name
        logger.info("Fetching top models for provider: %s", sanitize_for_logging(provider_name))

        # Fetch more results than requested to support offset pagination
        top_models = get_top_models_by_provider(
            provider_name=provider_name, limit=limit + offset, time_range=time_range
        )

        # Apply offset pagination
        total_count = len(top_models)
        top_models = top_models[offset : offset + limit]

        return {
            "success": True,
            "provider": provider_name,
            "data": top_models,
            "count": len(top_models),
            "time_range": time_range,
            "timestamp": datetime.now(UTC).isoformat(),
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "has_more": offset + limit < total_count,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get top models for %s: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail=f"Failed to get top models: {str(e)}")


# ==================== NEW: Model Comparison Endpoints ====================


async def compare_model_across_gateways(
    provider_name: str,
    model_name: str,
    gateways: str | None = Query("all", description="Comma-separated gateways or 'all'"),
):
    """
    Compare the same model across different gateways

    This endpoint fetches the same model from multiple gateways and compares:
    - Pricing (if available)
    - Availability
    - Features/capabilities
    - Provider information

    Args:
        provider_name: Provider name (e.g., 'openai', 'anthropic')
        model_name: Model name (e.g., 'gpt-4', 'claude-3')
        gateways: Comma-separated list of gateways or 'all'

    Returns:
        Comparison data across gateways with recommendation

    Example:
        GET /catalog/model/openai/gpt-4/compare
        GET /catalog/model/anthropic/claude-3/compare?gateways=openrouter,featherless
    """
    try:
        provider_name = normalize_developer_segment(provider_name) or provider_name
        model_name = normalize_model_segment(model_name) or model_name
        logger.info(
            "Comparing model %s/%s across gateways",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
        )

        # Parse gateways list
        if gateways and gateways.lower() != "all":
            gateway_list = [g.strip().lower() for g in gateways.split(",")]
        else:
            gateway_list = [
                "openrouter",
                "featherless",
                "deepinfra",
                "chutes",
                "groq",
                "fireworks",
                "together",
                "google-vertex",
                "vercel-ai-gateway",
                "simplismart",
            ]

        model_id = f"{provider_name}/{model_name}"
        comparisons = []

        # Fetch model from each gateway
        for gateway in gateway_list:
            try:
                model_data = fetch_specific_model(provider_name, model_name, gateway)

                if model_data:
                    # Extract relevant comparison data
                    pricing = model_data.get("pricing", {})

                    comparison = {
                        "gateway": gateway,
                        "available": True,
                        "model_id": model_data.get("id"),
                        "name": model_data.get("name"),
                        "pricing": {
                            "prompt": pricing.get("prompt"),
                            "completion": pricing.get("completion"),
                            "prompt_cost_per_1m": pricing.get("prompt"),
                            "completion_cost_per_1m": pricing.get("completion"),
                        },
                        "context_length": model_data.get("context_length", 0),
                        "architecture": model_data.get("architecture", {}),
                        "provider_site_url": model_data.get("provider_site_url"),
                        "source_gateway": model_data.get("source_gateway"),
                    }

                    comparisons.append(comparison)
                else:
                    comparisons.append(
                        {
                            "gateway": gateway,
                            "available": False,
                            "model_id": model_id,
                            "name": f"{provider_name}/{model_name}",
                            "pricing": None,
                            "context_length": None,
                            "architecture": None,
                            "provider_site_url": None,
                            "source_gateway": gateway,
                        }
                    )

            except Exception as e:
                logger.warning(
                    "Failed to fetch model from %s: %s",
                    sanitize_for_logging(gateway),
                    sanitize_for_logging(str(e)),
                )
                comparisons.append(
                    {"gateway": gateway, "available": False, "error": str(e), "model_id": model_id}
                )

        # Calculate recommendation based on pricing
        recommendation = _calculate_recommendation(comparisons)

        # Calculate potential savings
        savings_info = _calculate_savings(comparisons)

        return {
            "success": True,
            "model_id": model_id,
            "provider": provider_name,
            "model": model_name,
            "comparisons": comparisons,
            "recommendation": recommendation,
            "savings": savings_info,
            "available_count": sum(1 for c in comparisons if c.get("available")),
            "total_gateways_checked": len(comparisons),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to compare model %s/%s: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail=f"Failed to compare model: {str(e)}")


async def batch_compare_models(
    model_ids: list[str] = Query(
        ..., description="List of model IDs (e.g., ['openai/gpt-4', 'anthropic/claude-3'])"
    ),
    criteria: str = Query(
        "price", description="Comparison criteria: 'price', 'context', 'availability'"
    ),
):
    """
    Compare multiple models at once

    This endpoint allows comparing multiple models based on specific criteria.

    Args:
        model_ids: List of model IDs in format "provider/model"
        criteria: Comparison criteria

    Returns:
        Comparison data for all models

    Example:
        POST /catalog/models/batch-compare?model_ids=openai/gpt-4&model_ids=anthropic/claude-3&criteria=price
    """
    try:
        logger.info(
            "Batch comparing %d models by %s", len(model_ids), sanitize_for_logging(criteria)
        )

        results = []

        for model_id in model_ids:
            # Parse model_id
            if "/" not in model_id:
                results.append(
                    {
                        "model_id": model_id,
                        "error": "Invalid model ID format. Expected 'provider/model'",
                    }
                )
                continue

            provider_part, model_part = model_id.split("/", 1)
            provider_name = normalize_developer_segment(provider_part) or provider_part.strip()
            model_name = normalize_model_segment(model_part) or model_part.strip()
            normalized_model_id = f"{provider_name}/{model_name}"

            try:
                # Get model from all gateways
                all_gateways = [
                    "openrouter",
                    "featherless",
                    "groq",
                    "fireworks",
                    "together",
                    "google-vertex",
                    "vercel-ai-gateway",
                    "simplismart",
                ]
                models_data = []

                for gateway in all_gateways:
                    model_data = fetch_specific_model(provider_name, model_name, gateway)
                    if model_data:
                        models_data.append({"gateway": gateway, "data": model_data})

                if models_data:
                    # Extract comparison data based on criteria
                    if criteria == "price":
                        comparison_data = _extract_price_comparison(models_data)
                    elif criteria == "context":
                        comparison_data = _extract_context_comparison(models_data)
                    elif criteria == "availability":
                        comparison_data = _extract_availability_comparison(
                            models_data, all_gateways
                        )
                    else:
                        comparison_data = {"error": f"Unknown criteria: {criteria}"}

                    results.append(
                        {
                            "model_id": normalized_model_id,
                            "comparison": comparison_data,
                            "gateways_available": len(models_data),
                        }
                    )
                else:
                    results.append(
                        {"model_id": normalized_model_id, "error": "Model not found in any gateway"}
                    )

            except Exception as e:
                logger.error(
                    "Error comparing %s: %s",
                    sanitize_for_logging(normalized_model_id),
                    sanitize_for_logging(str(e)),
                )
                results.append({"model_id": normalized_model_id, "error": str(e)})

        return {
            "success": True,
            "criteria": criteria,
            "models_compared": len(model_ids),
            "results": results,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed batch comparison: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to batch compare models: {str(e)}")


# ============================================================================
# PUBLIC API ENDPOINTS - Unified /models routes
# These are the actual FastAPI route handlers exposed to clients
# ============================================================================


@router.get("/models", tags=["models"])
async def get_all_models(
    provider: str | None = Query(None, description="Filter models by provider"),
    is_private: bool | None = Query(
        None,
        description="Filter by private models: true=private only, false=non-private only, null=all models",
    ),
    limit: int = Query(
        100, ge=1, le=1000, description=f"{DESC_LIMIT_NUMBER_OF_RESULTS} (default: 100, max: 1000)"
    ),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    include_huggingface: bool = Query(
        False,
        description="Include Hugging Face metrics for models that have hugging_face_id (slower, default: false)",
    ),
    gateway: str | None = Query(
        "all",
        description=DESC_GATEWAY_WITH_ALL,
    ),
    unique_models: bool = Query(
        False,
        description=(
            "Return deduplicated models with provider arrays. "
            "Only applies when gateway='all'. Default: False (flat catalog). "
            "When true, each model appears once with an array of providers offering it. "
            "Example response: {id: 'gpt-4', providers: [{slug: 'openrouter', pricing: {...}}, ...]}"
        ),
    ),
):
    return await get_models(
        provider=provider,
        is_private=is_private,
        limit=limit,
        offset=offset,
        include_huggingface=include_huggingface,
        gateway=gateway,
        unique_models=unique_models,
    )


@router.get("/models/trending", tags=["statistics"])
async def get_trending_models_api(
    gateway: str | None = Query("all", description="Gateway filter or 'all'"),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_NO_ALL),
    limit: int = Query(10, description=DESC_NUMBER_OF_MODELS_TO_RETURN, ge=1, le=100),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
    sort_by: str = Query("requests", description="Sort by: 'requests', 'tokens', 'users'"),
):
    return await get_trending_models_endpoint(
        gateway=gateway,
        time_range=time_range,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.get("/models/low-latency", tags=["models"])
async def get_low_latency_models_api(
    include_ultra_only: bool = Query(
        False,
        description="Only include ultra-low-latency models (<100ms response time)",
    ),
    include_alternatives: bool = Query(
        True,
        description="Include suggested fast alternatives for common model families",
    ),
    limit: int = Query(default=50, ge=1, le=500, description="Limit number of results"),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
):
    """
    Get models optimized for low latency.

    Returns models with sub-500ms average response times based on production metrics.
    Useful for real-time applications, chatbots, and latency-sensitive use cases.

    **Ultra-low-latency models** (<100ms):
    - groq/moonshotai/kimi-k2-instruct-0905 (29ms)
    - groq/openai/gpt-oss-120b (74ms)

    **Low-latency models** (<500ms):
    - Groq models (fastest provider)
    - Select OpenRouter models (gemini-flash, gemma, etc.)
    - Fireworks optimized models
    """
    from src.services.request_prioritization import (
        PROVIDER_LATENCY_TIERS,
        get_fastest_providers,
        get_low_latency_models,
        get_ultra_low_latency_models,
        suggest_low_latency_alternative,
    )

    if include_ultra_only:
        models = get_ultra_low_latency_models()
    else:
        models = get_low_latency_models()

    # Apply offset pagination to the models list
    total_count = len(models)
    paginated_models = models[offset : offset + limit]

    result = {
        "models": paginated_models,
        "count": len(paginated_models),
        "providers_by_speed": get_fastest_providers(),
        "provider_tiers": {
            provider: {
                "tier": tier,
                "description": {
                    1: "Ultra-fast (<100ms typical)",
                    2: "Fast (100-500ms typical)",
                    3: "Standard (500ms-2s typical)",
                    4: "Variable (depends on model/load)",
                }.get(tier, "Unknown"),
            }
            for provider, tier in PROVIDER_LATENCY_TIERS.items()
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_count,
            "has_more": offset + limit < total_count,
        },
    }

    if include_alternatives:
        result["alternatives"] = {
            "claude": suggest_low_latency_alternative("claude"),
            "gpt-4": suggest_low_latency_alternative("gpt-4"),
            "gpt-3.5": suggest_low_latency_alternative("gpt-3.5"),
            "llama": suggest_low_latency_alternative("llama"),
            "mistral": suggest_low_latency_alternative("mistral"),
            "gemini": suggest_low_latency_alternative("gemini"),
            "deepseek-r1": suggest_low_latency_alternative("deepseek-r1"),
        }

    return result


@router.get("/models/unique", tags=["models"])
async def get_unique_models_with_providers(
    limit: int | None = Query(
        100, description="Limit number of results (default: 100, max: 1000)", ge=1, le=1000
    ),
    offset: int | None = Query(0, description="Offset for pagination", ge=0),
    min_providers: int | None = Query(
        None,
        description="Minimum number of providers (e.g., 2 for models with multiple providers)",
        ge=1,
    ),
    sort_by: str = Query(
        "provider_count",
        description="Sort by: 'provider_count' (most providers), 'name' (alphabetical), 'cheapest_price' (lowest price)",
    ),
    order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    include_inactive: bool = Query(False, description="Include inactive models"),
):
    """
    Get unique models with their provider information.

    This endpoint shows deduplicated models (e.g., "GPT-4" appears once) with:
    - How many providers offer each model
    - Which providers offer it
    - Pricing from each provider
    - Health status and performance metrics per provider
    - Cheapest and fastest provider for each model

    **Use Cases:**
    - Find models available from multiple providers
    - Compare pricing across providers for the same model
    - Identify the cheapest provider for a specific model
    - See which providers offer the most models

    **Examples:**
    - Most widely available models: `?sort_by=provider_count&order=desc`
    - Models with 3+ providers: `?min_providers=3&sort_by=provider_count`
    - Cheapest unique models: `?sort_by=cheapest_price&order=asc`
    - Alphabetical list: `?sort_by=name&order=asc`

    **Response Format:**
    ```json
    {
      "models": [
        {
          "id": "gpt-4",
          "name": "GPT-4",
          "provider_count": 3,
          "providers": [
            {
              "slug": "openrouter",
              "name": "OpenRouter",
              "pricing": {"prompt": "0.03", "completion": "0.06"},
              "context_length": 8192,
              "health_status": "healthy",
              "average_response_time_ms": 1200
            },
            {
              "slug": "groq",
              "name": "Groq",
              "pricing": {"prompt": "0.025", "completion": "0.05"},
              "context_length": 8192,
              "health_status": "healthy",
              "average_response_time_ms": 950
            }
          ],
          "cheapest_provider": "groq",
          "fastest_provider": "groq",
          "cheapest_prompt_price": 0.025,
          "fastest_response_time": 950
        }
      ],
      "total": 234,
      "limit": 100,
      "offset": 0
    }
    ```
    """
    try:
        from src.db.models_catalog_db import (
            get_all_unique_models_for_catalog,
            transform_unique_models_batch,
        )
        from src.services.model_catalog_cache import get_cached_unique_models_smart

        # Validate sort_by; silently fall back to default for backwards compatibility
        valid_unique_sort = {"provider_count", "name", "cheapest_price"}
        if sort_by not in valid_unique_sort:
            logger.warning(
                "Invalid sort_by='%s' for /models/unique; defaulting to 'provider_count'",
                sanitize_for_logging(sort_by),
            )
            sort_by = "provider_count"

        logger.info(
            f"Fetching unique models: limit={limit}, offset={offset}, "
            f"min_providers={min_providers}, sort_by={sort_by}, order={order}"
        )

        # SMART CACHING: Always try cache first (handles all filter combinations)
        api_models = await get_cached_unique_models_smart(
            include_inactive=include_inactive,
            min_providers=min_providers,
            sort_by=sort_by,
            order=order,
        )

        if api_models:
            logger.info(f"Using cached unique models ({len(api_models)} models)")
        else:
            # Cache miss - fetch from database
            # IMPORTANT: These are sync DB functions; wrap in asyncio.to_thread
            # to avoid blocking the event loop in this async route handler.
            logger.info("Cache miss - fetching unique models from database")
            db_unique_models = await asyncio.to_thread(
                get_all_unique_models_for_catalog, include_inactive=include_inactive
            )

            # Transform to API format
            api_models = await asyncio.to_thread(transform_unique_models_batch, db_unique_models)

            # Apply filters
            if min_providers is not None:
                api_models = [m for m in api_models if m.get("provider_count", 0) >= min_providers]

            # Sort the results
            if sort_by == "provider_count":
                api_models.sort(key=lambda m: m.get("provider_count", 0), reverse=(order == "desc"))
            elif sort_by == "name":
                api_models.sort(key=lambda m: m.get("name", "").lower(), reverse=(order == "desc"))
            elif sort_by == "cheapest_price":
                api_models.sort(
                    key=lambda m: (
                        m.get("cheapest_prompt_price")
                        if m.get("cheapest_prompt_price") is not None
                        else float("inf")
                    ),
                    reverse=(order == "desc"),
                )

            # Cache the result for this specific filter/sort combination
            from src.services.model_catalog_cache import cache_unique_models_with_filters

            await cache_unique_models_with_filters(
                models=api_models,
                include_inactive=include_inactive,
                min_providers=min_providers,
                sort_by=sort_by,
                order=order,
            )

        # Calculate total before pagination
        total = len(api_models)

        # Apply pagination
        paginated_models = api_models[offset : offset + limit] if limit else api_models[offset:]

        logger.info(
            f"Returning {len(paginated_models)} unique models "
            f"(total: {total}, filtered by min_providers={min_providers})"
        )

        return {
            "models": paginated_models,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {
                "min_providers": min_providers,
                "include_inactive": include_inactive,
            },
            "sort": {
                "by": sort_by,
                "order": order,
            },
        }

    except Exception as e:
        logger.error(f"Error fetching unique models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch unique models: {str(e)}")


@router.post("/models/batch-compare", tags=["comparison"])
async def batch_compare_models_api(
    model_ids: list[str] = Query(
        ..., description="List of model IDs (e.g., ['openai/gpt-4', 'anthropic/claude-3'])"
    ),
    criteria: str = Query(
        "price", description="Comparison criteria: 'price', 'context', 'availability'"
    ),
):
    return await batch_compare_models(model_ids=model_ids, criteria=criteria)


@router.get("/models/{provider_name}/{model_name:path}/compare", tags=["comparison"])
async def compare_model_gateways_api(
    provider_name: str,
    model_name: str,
    gateways: str | None = Query("all", description="Comma-separated gateways or 'all'"),
):
    return await compare_model_across_gateways(
        provider_name=provider_name,
        model_name=model_name,
        gateways=gateways,
    )


@router.get("/models/{provider_name}/{model_name:path}", tags=["models"])
async def get_specific_model_api(
    provider_name: str,
    model_name: str,
    include_huggingface: bool = Query(True, description=DESC_INCLUDE_HUGGINGFACE),
    gateway: str | None = Query(
        None,
        description=DESC_GATEWAY_AUTO_DETECT,
    ),
):
    return await get_specific_model(
        provider_name=provider_name,
        model_name=model_name,
        include_huggingface=include_huggingface,
        gateway=gateway,
    )


@router.get("/models/{provider_name}/{model_name:path}", tags=["models"])
async def get_specific_model_api_legacy(
    provider_name: str,
    model_name: str,
    include_huggingface: bool = Query(True, description=DESC_INCLUDE_HUGGINGFACE),
    gateway: str | None = Query(
        None,
        description=DESC_GATEWAY_AUTO_DETECT,
    ),
):
    """Legacy endpoint without /v1/ prefix for backward compatibility"""
    return await get_specific_model(
        provider_name=provider_name,
        model_name=model_name,
        include_huggingface=include_huggingface,
        gateway=gateway,
    )


@router.get("/models/{developer_name}", tags=["models"])
async def get_developer_models_api(
    developer_name: str,
    limit: int = Query(100, ge=1, le=1000, description=DESC_LIMIT_NUMBER_OF_RESULTS),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    include_huggingface: bool = Query(True, description="Include Hugging Face metrics"),
    gateway: str | None = Query("all", description="Gateway: 'openrouter' or 'all'"),
):
    return await get_developer_models(
        developer_name=developer_name,
        limit=limit,
        offset=offset,
        include_huggingface=include_huggingface,
        gateway=gateway,
    )


@router.get("/models/search", tags=["models"])
async def search_models(
    q: str | None = Query(
        None, description="Search query (searches in model name, provider, description)"
    ),
    modality: str | None = Query(
        None, description="Filter by modality: text, image, audio, video, multimodal"
    ),
    is_private: bool | None = Query(
        None,
        description="Filter by private models: true=private only, false=non-private only, null=all models",
    ),
    min_context: int | None = Query(None, description="Minimum context window size (tokens)"),
    max_context: int | None = Query(None, description="Maximum context window size (tokens)"),
    min_price: float | None = Query(None, description="Minimum price per token (USD)"),
    max_price: float | None = Query(None, description="Maximum price per token (USD)"),
    gateway: str | None = Query(
        "all",
        description="Gateway filter: openrouter, featherless, deepinfra, chutes, groq, fireworks, together, google-vertex, helicone, aihubmix, vercel-ai-gateway, or all",
    ),
    sort_by: str = Query("price", description="Sort by: price, context, popularity, name"),
    order: str = Query("asc", description="Sort order: asc or desc"),
    limit: int = Query(20, description="Number of results", ge=1, le=100),
    offset: int = Query(0, description="Pagination offset", ge=0),
):
    """
    Advanced model search with multiple filters.

    This endpoint allows you to search and filter models across all gateways
    with powerful query capabilities.

    **Examples:**
    - Search for cheap models: `?max_price=0.0001&sort_by=price`
    - Find models with large context: `?min_context=100000&sort_by=context&order=desc`
    - Search by name: `?q=gpt-4&gateway=openrouter`
    - Filter by modality: `?modality=image&sort_by=popularity`
    - Filter for private models only: `?is_private=true`
    - Exclude private models: `?is_private=false`

    **Returns:**
    - List of models matching the criteria
    - Total count of matching models
    - Applied filters
    """
    try:
        _validate_gateway(gateway)

        # Validate sort_by; silently fall back to default for backwards compatibility
        valid_search_sort = {"price", "context", "popularity", "name"}
        if sort_by not in valid_search_sort:
            logger.warning(
                "Invalid sort_by='%s' for /models/search; defaulting to 'price'",
                sanitize_for_logging(sort_by),
            )
            sort_by = "price"

        # Get all models from specified gateways
        all_models = []
        gateway_value = gateway.lower() if gateway else "all"

        # Fetch from selected gateways
        if gateway_value in ("openrouter", "all"):
            openrouter_models = get_cached_models("openrouter") or []
            all_models.extend(openrouter_models)

        if gateway_value in ("featherless", "all"):
            featherless_models = get_cached_models("featherless") or []
            all_models.extend(featherless_models)

        if gateway_value in ("deepinfra", "all"):
            deepinfra_models = get_cached_models("deepinfra") or []
            all_models.extend(deepinfra_models)

        if gateway_value in ("chutes", "all"):
            chutes_models = get_cached_models("chutes") or []
            all_models.extend(chutes_models)

        if gateway_value in ("groq", "all"):
            groq_models = get_cached_models("groq") or []
            all_models.extend(groq_models)

        if gateway_value in ("fireworks", "all"):
            fireworks_models = get_cached_models("fireworks") or []
            all_models.extend(fireworks_models)

        if gateway_value in ("together", "all"):
            together_models = get_cached_models("together") or []
            all_models.extend(together_models)

        if gateway_value in ("google-vertex", "all"):
            google_models = get_cached_models("google-vertex") or []
            all_models.extend(google_models)

        if gateway_value in ("aihubmix", "all"):
            aihubmix_models = get_cached_models("aihubmix") or []
            all_models.extend(aihubmix_models)

        if gateway_value in ("vercel-ai-gateway", "all"):
            vercel_models = get_cached_models("vercel-ai-gateway") or []
            all_models.extend(vercel_models)

        if gateway_value in ("simplismart", "all"):
            simplismart_models = get_cached_models("simplismart") or []
            all_models.extend(simplismart_models)

        # Apply filters
        filtered_models = all_models

        # Text search filter
        if q:
            q_lower = q.lower()
            filtered_models = [
                m
                for m in filtered_models
                if (
                    q_lower in m.get("id", "").lower()
                    or q_lower in m.get("name", "").lower()
                    or q_lower in m.get("description", "").lower()
                    or q_lower in str(m.get("provider", "")).lower()
                )
            ]

        # Modality filter
        if modality:
            modality_lower = modality.lower()
            filtered_models = [
                m
                for m in filtered_models
                if modality_lower in str(m.get("modality", "text")).lower()
                or modality_lower in str(m.get("architecture", {}).get("modality", "text")).lower()
            ]

        # Private models filter
        if is_private is not None:
            if is_private:
                # Only show private models (Near AI models)
                filtered_models = [m for m in filtered_models if m.get("is_private") is True]
            else:
                # Only show non-private models
                filtered_models = [m for m in filtered_models if not m.get("is_private")]

        # Context window filters
        if min_context is not None:
            filtered_models = [
                m for m in filtered_models if m.get("context_length", 0) >= min_context
            ]

        if max_context is not None:
            filtered_models = [
                m for m in filtered_models if m.get("context_length", float("inf")) <= max_context
            ]

        # Price filters (check pricing data)
        if min_price is not None or max_price is not None:

            def get_model_price(model):
                pricing = model.get("pricing", {})
                if isinstance(pricing, dict):
                    prompt_price = pricing.get("prompt")
                    completion_price = pricing.get("completion")
                    if prompt_price and completion_price:
                        # Return average price
                        return (float(prompt_price) + float(completion_price)) / 2
                return None

            if min_price is not None:
                filtered_models = [
                    m
                    for m in filtered_models
                    if (price := get_model_price(m)) is not None and price >= min_price
                ]

            if max_price is not None:
                filtered_models = [
                    m
                    for m in filtered_models
                    if (price := get_model_price(m)) is not None and price <= max_price
                ]

        # Sorting
        def get_sort_key(model):
            if sort_by == "price":
                pricing = model.get("pricing", {})
                if isinstance(pricing, dict):
                    prompt = pricing.get("prompt", 0)
                    completion = pricing.get("completion", 0)
                    try:
                        return (float(prompt or 0) + float(completion or 0)) / 2
                    except (TypeError, ValueError):
                        pass
                return float("inf")  # Put models without pricing at the end

            elif sort_by == "context":
                return float(model.get("context_length", 0) or 0)

            elif sort_by == "popularity":
                # Use ranking if available, otherwise 0
                return model.get("rank", model.get("ranking", 0))

            elif sort_by == "name":
                return model.get("name", model.get("id", ""))

            return 0

        # Sort
        reverse = order.lower() == "desc"
        filtered_models.sort(key=get_sort_key, reverse=reverse)

        # Apply pagination
        total_count = len(filtered_models)
        paginated_models = filtered_models[offset : offset + limit]

        return {
            "success": True,
            "data": paginated_models,
            "meta": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "returned": len(paginated_models),
                "filters_applied": {
                    "query": q,
                    "modality": modality,
                    "is_private": is_private,
                    "min_context": min_context,
                    "max_context": max_context,
                    "min_price": min_price,
                    "max_price": max_price,
                    "gateway": gateway,
                    "sort_by": sort_by,
                    "order": order,
                },
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to search models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to search models: {str(e)}")


# Helper functions for model comparison


def _calculate_recommendation(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate which gateway is recommended based on pricing"""
    available = [c for c in comparisons if c.get("available")]

    if not available:
        return {"gateway": None, "reason": "Model not available in any gateway"}

    # Filter out comparisons without pricing
    with_pricing = [
        c
        for c in available
        if c.get("pricing") and c["pricing"].get("prompt") and c["pricing"].get("completion")
    ]

    if not with_pricing:
        return {
            "gateway": available[0]["gateway"],
            "reason": "First available gateway (pricing data not available)",
        }

    # Calculate total cost (prompt + completion) for comparison
    for comp in with_pricing:
        try:
            prompt_price = float(comp["pricing"]["prompt"]) if comp["pricing"]["prompt"] else 0
            completion_price = (
                float(comp["pricing"]["completion"]) if comp["pricing"]["completion"] else 0
            )
            comp["_total_cost"] = prompt_price + completion_price
        except (ValueError, TypeError) as e:
            logger.debug(
                "Failed to parse pricing for gateway '%s' in recommendation: %s",
                comp.get("gateway"),
                e,
            )
            comp["_total_cost"] = float("inf")

    # Find cheapest
    cheapest = min(with_pricing, key=lambda x: x.get("_total_cost", float("inf")))

    return {
        "gateway": cheapest["gateway"],
        "reason": f"Lowest pricing (${cheapest['_total_cost']}/1M tokens combined)",
        "pricing": cheapest["pricing"],
    }


def _calculate_savings(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate potential savings"""
    available = [c for c in comparisons if c.get("available") and c.get("pricing")]

    if len(available) < 2:
        return {"potential_savings": 0.0, "most_expensive_gateway": None, "cheapest_gateway": None}

    # Calculate total costs
    costs = []
    for comp in available:
        try:
            pricing = comp["pricing"]
            if pricing and pricing.get("prompt") and pricing.get("completion"):
                prompt = float(pricing["prompt"])
                completion = float(pricing["completion"])
                total = prompt + completion
                costs.append({"gateway": comp["gateway"], "total_cost": total})
        except (ValueError, TypeError) as e:
            logger.debug(
                "Failed to parse pricing for gateway '%s' in savings calculation: %s",
                comp.get("gateway"),
                e,
            )
            continue

    if len(costs) < 2:
        return {"potential_savings": 0.0, "most_expensive_gateway": None, "cheapest_gateway": None}

    cheapest = min(costs, key=lambda x: x["total_cost"])
    most_expensive = max(costs, key=lambda x: x["total_cost"])

    savings_amount = most_expensive["total_cost"] - cheapest["total_cost"]
    savings_percent = (
        (savings_amount / most_expensive["total_cost"]) * 100
        if most_expensive["total_cost"] > 0
        else 0
    )

    return {
        "potential_savings_per_1m_tokens": round(savings_amount, 4),
        "savings_percentage": round(savings_percent, 2),
        "cheapest_gateway": cheapest["gateway"],
        "cheapest_cost": round(cheapest["total_cost"], 4),
        "most_expensive_gateway": most_expensive["gateway"],
        "most_expensive_cost": round(most_expensive["total_cost"], 4),
    }


def _extract_price_comparison(models_data: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract price comparison data"""
    prices = {}
    for item in models_data:
        gateway = item["gateway"]
        model = item["data"]
        pricing = model.get("pricing", {})
        prices[gateway] = {"prompt": pricing.get("prompt"), "completion": pricing.get("completion")}
    return prices


def _extract_context_comparison(models_data: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract context length comparison data"""
    contexts = {}
    for item in models_data:
        gateway = item["gateway"]
        model = item["data"]
        contexts[gateway] = model.get("context_length", 0)
    return contexts


def _extract_availability_comparison(
    models_data: list[dict[str, Any]], all_gateways: list[str]
) -> dict[str, bool]:
    """Extract availability comparison data"""
    availability = dict.fromkeys(all_gateways, False)
    for item in models_data:
        availability[item["gateway"]] = True
    return availability


# ============================================================================
# MODELZ INTEGRATION ENDPOINTS
# ============================================================================


@router.get("/modelz/models")
async def get_modelz_models(
    is_graduated: bool | None = Query(
        None,
        description="Filter for graduated (singularity) models: true=graduated only, false=non-graduated only, null=all models",
    )
):
    """
    Get models that exist on Modelz with optional graduation filter.

    This endpoint bridges Gatewayz with Modelz by fetching model token data
    from the Modelz API and applying the same filters as the original Modelz API.

    Query Parameters:
    - is_graduated: Filter for graduated models
      - true: Only graduated/singularity models
      - false: Only non-graduated models
      - null: All models (default)

    Returns:
    - List of models with their token data from Modelz
    - Includes model IDs, graduation status, and other metadata
    """
    try:
        logger.info("Fetching Modelz models with is_graduated=%s", is_graduated)

        # Fetch token data from Modelz API
        tokens = await fetch_modelz_tokens(is_graduated)

        # Transform the data to a consistent format
        models = []
        for token in tokens:
            model_data = {
                "model_id": (
                    token.get("Token")
                    or token.get("model_id")
                    or token.get("modelId")
                    or token.get("id")
                    or token.get("name")
                    or token.get("model")
                ),
                "is_graduated": token.get("isGraduated") or token.get("is_graduated"),
                "token_data": token,
                "source": "modelz",
                "has_token": True,
            }

            # Only include models with valid model IDs
            if model_data["model_id"]:
                models.append(model_data)

        logger.info(f"Successfully processed {len(models)} models from Modelz")

        return {
            "models": models,
            "total_count": len(models),
            "filter": {
                "is_graduated": is_graduated,
                "description": (
                    DESC_ALL_MODELS
                    if is_graduated is None
                    else (
                        DESC_GRADUATED_MODELS_ONLY
                        if is_graduated
                        else DESC_NON_GRADUATED_MODELS_ONLY
                    )
                ),
            },
            "source": "modelz",
            "api_reference": "https://backend.alpacanetwork.ai/api/tokens",
        }

    except HTTPException:
        # Re-raise HTTP exceptions from the client
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_modelz_models: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch models from Modelz: {str(e)}")


@router.get("/modelz/ids")
async def get_modelz_model_ids_endpoint(
    is_graduated: bool | None = Query(
        None,
        description="Filter for graduated models: true=graduated only, false=non-graduated only, null=all models",
    )
):
    """
    Get a list of model IDs that exist on Modelz.

    This is a lightweight endpoint that returns only the model IDs,
    useful for checking which models have tokens on Modelz.

    Query Parameters:
    - is_graduated: Filter for graduated models (same as /models/modelz)

    Returns:
    - List of model IDs from Modelz
    """
    try:
        logger.info("Fetching Modelz model IDs with is_graduated=%s", is_graduated)

        model_ids = await get_modelz_model_ids(is_graduated)

        return {
            "model_ids": model_ids,
            "total_count": len(model_ids),
            "filter": {
                "is_graduated": is_graduated,
                "description": (
                    DESC_ALL_MODELS
                    if is_graduated is None
                    else (
                        DESC_GRADUATED_MODELS_ONLY
                        if is_graduated
                        else DESC_NON_GRADUATED_MODELS_ONLY
                    )
                ),
            },
            "source": "modelz",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_modelz_model_ids_endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch model IDs from Modelz: {str(e)}"
        )


@router.get("/modelz/check/{model_id}")
async def check_model_on_modelz(
    model_id: str,
    is_graduated: bool | None = Query(
        None, description="Filter for graduated models when checking"
    ),
):
    """
    Check if a specific model exists on Modelz.

    Path Parameters:
    - model_id: The model ID to check

    Query Parameters:
    - is_graduated: Filter for graduated models when checking

    Returns:
    - Boolean indicating if model exists on Modelz
    - Additional model details if found
    """
    try:
        logger.info(
            "Checking if model '%s' exists on Modelz with is_graduated=%s",
            sanitize_for_logging(model_id),
            is_graduated,
        )

        exists = await check_model_exists_on_modelz(model_id, is_graduated)

        result = {
            "model_id": model_id,
            "exists_on_modelz": exists,
            "filter": {
                "is_graduated": is_graduated,
                "description": (
                    DESC_ALL_MODELS
                    if is_graduated is None
                    else (
                        DESC_GRADUATED_MODELS_ONLY
                        if is_graduated
                        else DESC_NON_GRADUATED_MODELS_ONLY
                    )
                ),
            },
            "source": "modelz",
        }

        # If model exists, get additional details
        if exists:
            model_details = await get_modelz_model_details(model_id)
            if model_details:
                result["model_details"] = model_details

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in check_model_on_modelz: {str(e)}")


# HuggingFace Hub SDK Discovery Endpoints
@router.get("/huggingface/discovery", tags=["huggingface-discovery"])
async def discover_huggingface_models(
    task: str | None = Query(
        "text-generation",
        description="Filter by task type (e.g., 'text-generation', 'text2text-generation', 'conversational')",
    ),
    sort: str = Query("likes", description="Sort by: 'likes' or 'downloads'"),
    limit: int = Query(50, description="Number of models to return", ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
):
    """
    Discover HuggingFace models using the official Hub SDK.

    This endpoint provides advanced model discovery with filtering by task type
    and sorting by popularity metrics (likes/downloads). Great for exploring
    available models on HuggingFace Hub.

    Uses the official huggingface_hub SDK for direct API access to Hub metadata.
    """
    try:
        from src.services.huggingface_hub_service import list_huggingface_models

        logger.info(
            f"Discovering HuggingFace models: task={task}, sort={sort}, limit={limit}, offset={offset}"
        )

        # Fetch enough models to support offset pagination
        models = list_huggingface_models(
            task=task,
            sort=sort,
            limit=limit + offset,
        )

        if not models:
            logger.warning(f"No HuggingFace models found for task={task}")
            return {
                "models": [],
                "count": 0,
                "source": "huggingface-hub",
                "task": task,
                "sort": sort,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": 0,
                    "has_more": False,
                },
            }

        # Apply offset pagination
        total_count = len(models)
        paginated_models = models[offset : offset + limit]

        return {
            "models": paginated_models,
            "count": len(paginated_models),
            "source": "huggingface-hub",
            "task": task,
            "sort": sort,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "has_more": offset + limit < total_count,
            },
        }

    except Exception as e:
        logger.error(f"Error discovering HuggingFace models: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to discover HuggingFace models: {str(e)}",
        )


@router.get("/huggingface/search", tags=["huggingface-discovery"])
async def search_huggingface_models_endpoint(
    q: str = Query(..., description="Search query (model name, description, etc.)", min_length=1),
    task: str | None = Query(None, description="Optional task filter"),
    limit: int = Query(20, description="Number of results to return", ge=1, le=100),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
):
    """
    Search for HuggingFace models by query.

    Searches across model names, descriptions, and other metadata.
    Uses the official huggingface_hub SDK.
    """
    try:
        from src.services.huggingface_hub_service import search_models_by_query

        logger.info(
            f"Searching HuggingFace models: q='{q}', task={task}, limit={limit}, offset={offset}"
        )

        # Fetch enough models to support offset pagination
        models = search_models_by_query(
            query=q,
            task=task,
            limit=limit + offset,
        )

        # Apply offset pagination
        total_count = len(models)
        paginated_models = models[offset : offset + limit]

        return {
            "query": q,
            "models": paginated_models,
            "count": len(paginated_models),
            "source": "huggingface-hub",
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "has_more": offset + limit < total_count,
            },
        }

    except Exception as e:
        logger.error(f"Error searching HuggingFace models: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search HuggingFace models: {str(e)}",
        )


@router.get("/huggingface/models/{model_id:path}/details", tags=["huggingface-discovery"])
async def get_huggingface_model_details_endpoint(
    model_id: str,
):
    """
    Get detailed information about a specific HuggingFace model.

    Returns comprehensive metadata including model card, library info, and metrics.
    Uses the official huggingface_hub SDK for direct Hub access.
    """
    try:
        from src.services.huggingface_hub_service import get_model_details

        logger.info(f"Fetching details for HuggingFace model: {model_id}")

        model_info = get_model_details(model_id)

        if not model_info:
            raise HTTPException(
                status_code=404,
                detail=f"Model not found: {model_id}",
            )

        return {
            "model": model_info,
            "source": "huggingface-hub",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching model details: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch model details: {str(e)}",
        )


@router.get("/huggingface/models/{model_id:path}/card", tags=["huggingface-discovery"])
async def get_huggingface_model_card_endpoint(
    model_id: str,
):
    """
    Retrieve the model card (README) for a HuggingFace model.

    The model card contains documentation, usage instructions, and metadata
    about the model in Markdown format.
    """
    try:
        from src.services.huggingface_hub_service import get_model_card

        logger.info(f"Fetching model card for: {model_id}")

        card_content = get_model_card(model_id)

        if not card_content:
            raise HTTPException(
                status_code=404,
                detail=f"Model card not found for: {model_id}",
            )

        return {
            "model_id": model_id,
            "card": card_content,
            "source": "huggingface-hub",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching model card: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch model card: {str(e)}",
        )


@router.get("/huggingface/author/{author}/models", tags=["huggingface-discovery"])
async def list_author_models_endpoint(
    author: str,
    limit: int = Query(50, description="Number of models to return", ge=1, le=500),
):
    """
    List all models from a specific HuggingFace author or organization.

    Returns all public models published by the specified author/org.
    """
    try:
        from src.services.huggingface_hub_service import list_models_by_author

        logger.info(f"Listing models from author: {author}, limit={limit}")

        models = list_models_by_author(author=author, limit=limit)

        return {
            "author": author,
            "models": models,
            "count": len(models),
            "source": "huggingface-hub",
        }

    except Exception as e:
        logger.error(f"Error listing author models: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list author models: {str(e)}",
        )


@router.get("/huggingface/models/{model_id:path}/files", tags=["huggingface-discovery"])
async def get_model_files_endpoint(
    model_id: str,
):
    """
    Get information about all files in a HuggingFace model repository.

    Returns a list of files with sizes and metadata, useful for understanding
    what's available in the model repository.
    """
    try:
        from src.services.huggingface_hub_service import get_model_files

        logger.info(f"Fetching files for model: {model_id}")

        files = get_model_files(model_id)

        if files is None:
            raise HTTPException(
                status_code=404,
                detail=f"Model not found: {model_id}",
            )

        return {
            "model_id": model_id,
            "files": files,
            "count": len(files) if files else 0,
            "source": "huggingface-hub",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching model files: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch model files: {str(e)}",
        )
