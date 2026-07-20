import csv
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures import as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from src.cache import (  # Cache helper functions
    get_gateway_error_message,
    is_gateway_in_error_state,
)
from src.config import Config
from src.config.redis_config import get_redis_manager
from src.services.google_models_config import register_google_models_in_canonical_registry
from src.services.model_transformations import detect_provider_from_model_id
from src.services.multi_provider_registry import (
    CanonicalModelProvider,
    get_registry,
)
from src.services.pricing_lookup import enrich_model_with_pricing
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

# Constants
FAL_CACHE_INIT_DEFERRED = "FAL cache initialization deferred"
MAX_MODEL_DESCRIPTION_LENGTH = 500


def get_fallback_models_from_db(provider_slug: str) -> list[dict] | None:
    """
    Get fallback models from the database for a provider.

    This is used when the provider's API is unavailable. Instead of using
    hardcoded static fallback lists, we use the most recent successfully
    synced models from the database.

    Args:
        provider_slug: The provider slug (e.g., 'deepinfra', 'cerebras', 'openai')

    Returns:
        List of model dictionaries in raw format (ready for normalization),
        or None if no models found in database
    """
    try:
        from src.db.models_catalog_db import get_models_by_provider_slug

        db_models = get_models_by_provider_slug(provider_slug, is_active_only=True)

        if not db_models:
            logger.info(f"No fallback models found in database for provider: {provider_slug}")
            return None

        # Convert database models to raw format expected by normalize functions
        raw_models = []
        for db_model in db_models:
            raw_model = _convert_db_model_to_raw(db_model, provider_slug)
            if raw_model:
                raw_models.append(raw_model)

        if raw_models:
            logger.info(
                f"Using {len(raw_models)} fallback models from database for provider: {provider_slug}"
            )
            return raw_models

        return None
    except Exception as e:
        logger.warning(f"Failed to get fallback models from database for {provider_slug}: {e}")
        return None


def _convert_db_model_to_raw(db_model: dict, provider_slug: str) -> dict | None:
    """
    Convert a database model to the raw format expected by provider normalize functions.

    Different providers expect different raw formats, so this function handles
    the conversion based on the provider slug.

    Args:
        db_model: Model dictionary from the database
        provider_slug: The provider slug

    Returns:
        Raw model dictionary ready for normalization, or None if conversion fails
    """
    try:
        provider_model_id = db_model.get("provider_model_id") or db_model.get("model_id")
        if not provider_model_id:
            return None

        # Extract pricing from database (stored as per-token pricing)
        pricing_prompt = db_model.get("pricing_prompt")
        pricing_completion = db_model.get("pricing_completion")

        # Build base raw model that works for most providers
        raw_model = {
            "id": provider_model_id,
            "modelId": provider_model_id,
            "model_id": provider_model_id,
            "name": db_model.get("model_name") or provider_model_id,
            "description": db_model.get("description"),
            "context_length": db_model.get("context_length"),
            "owned_by": provider_slug,
            "metadata": db_model.get("metadata") or {},
        }

        # Add context length to metadata for providers that expect it there
        if raw_model["context_length"]:
            raw_model["metadata"]["contextLength"] = raw_model["context_length"]
            raw_model["metadata"]["context_length"] = raw_model["context_length"]

        # Most providers use simple pricing dict or per-token values
        raw_model["pricing"] = {}
        if pricing_prompt is not None:
            raw_model["pricing"]["prompt"] = str(pricing_prompt)
        if pricing_completion is not None:
            raw_model["pricing"]["completion"] = str(pricing_completion)

        # Add provider-specific fields
        if provider_slug in ["openai", "anthropic", "xai"]:
            raw_model["object"] = "model"

        return raw_model
    except Exception as e:
        logger.warning(f"Failed to convert database model: {e}")
        return None


def apply_database_fallback(
    provider_slug: str, normalize_function, original_error: Exception | None = None
) -> list[dict] | None:
    """
    Apply database fallback when provider API fails.

    This is a standardized helper that providers can use in their exception handlers
    to fallback to previously synced models from the database.

    Args:
        provider_slug: Provider slug (e.g., 'openrouter', 'deepinfra', 'featherless')
        normalize_function: Provider's normalize function (e.g., normalize_deepinfra_model)
        original_error: The original exception that triggered the fallback

    Returns:
        List of normalized model dictionaries ready for caching, or None if fallback fails

    Example:
        ```python
        except httpx.HTTPStatusError as e:
            logger.warning(f"API failed, attempting database fallback")
            return apply_database_fallback("deepinfra", normalize_deepinfra_model, e)
        ```
    """
    from src.utils.provider_error_logging import log_provider_fetch_warning

    try:
        # Log that we're attempting fallback
        error_context = {"trigger": type(original_error).__name__} if original_error else {}
        log_provider_fetch_warning(
            provider_slug=provider_slug,
            message="API fetch failed, attempting database fallback",
            context=error_context,
        )

        # Get raw models from database
        raw_models = get_fallback_models_from_db(provider_slug)

        if not raw_models:
            log_provider_fetch_warning(
                provider_slug=provider_slug,
                message="No fallback models found in database",
                context={"db_models_count": 0},
            )
            return None

        # Normalize the raw models using provider's normalize function
        normalized_models = []
        for raw_model in raw_models:
            try:
                normalized = normalize_function(raw_model)
                if normalized:
                    normalized_models.append(normalized)
            except Exception as norm_error:
                logger.warning(
                    f"[{provider_slug.upper()}] Failed to normalize fallback model: {norm_error}"
                )
                continue

        if normalized_models:
            log_provider_fetch_warning(
                provider_slug=provider_slug,
                message="Database fallback successful",
                context={
                    "raw_count": len(raw_models),
                    "normalized_count": len(normalized_models),
                    "source": "database",
                },
            )
            return normalized_models

        log_provider_fetch_warning(
            provider_slug=provider_slug,
            message="Database fallback failed - no models could be normalized",
            context={"raw_count": len(raw_models), "normalized_count": 0},
        )
        return None

    except Exception as e:
        log_provider_fetch_warning(
            provider_slug=provider_slug,
            message=f"Database fallback exception: {type(e).__name__}",
            context={"error": str(e)},
        )
        return None


# Thread-local storage for the catalog building flag.
# Using threading.local() prevents the global boolean from being shared
# (and corrupted) across concurrent provider builds running in different
# threads spawned by ThreadPoolExecutor.  Each thread sees its own
# independent copy of the flag, so one provider's build lifecycle cannot
# accidentally suppress cache lookups in another provider's thread.
_building_catalog_local = threading.local()


def _is_building_catalog() -> bool:
    """Check if the current thread is building the model catalog."""
    return getattr(_building_catalog_local, "building", False)


def _set_building_catalog(value: bool):
    """Set the building catalog flag for the current thread."""
    _building_catalog_local.building = value


# Modality constants to reduce duplication
MODALITY_TEXT_TO_TEXT = "text->text"
MODALITY_TEXT_TO_IMAGE = "text->image"
MODALITY_TEXT_TO_AUDIO = "text->audio"


class AggregatedCatalog(list):
    """List-like wrapper that also exposes canonical model metadata."""

    def __init__(self, models: list | None, canonical_models: list | None):
        super().__init__(models or [])
        self.canonical_models = canonical_models or []

    def as_dict(self) -> dict[str, Any]:
        return {"models": list(self), "canonical_models": self.canonical_models}


def _extract_modalities(record: dict) -> list[str]:
    modalities = record.get("modalities")
    if isinstance(modalities, list) and modalities:
        return modalities

    architecture = record.get("architecture") or {}
    if isinstance(architecture, dict):
        if isinstance(architecture.get("input_modalities"), list):
            return architecture["input_modalities"]
        modality = architecture.get("modality")
        if modality:
            if isinstance(modality, list):
                return modality
            return [modality]

    if record.get("modality"):
        value = record["modality"]
        if isinstance(value, list):
            return value
        return [value]

    return ["text"]


def _register_canonical_records(provider_slug: str, models: list | None) -> None:
    if not models:
        return

    try:
        registry = get_registry()
        normalized_provider = _normalize_provider_slug(provider_slug)

        for record in models:
            if not isinstance(record, dict):
                continue

            canonical_id = record.get("canonical_slug") or record.get("slug") or record.get("id")

            if not canonical_id:
                continue

            display_metadata = {
                "name": record.get("name") or record.get("display_name"),
                "description": record.get("description"),
                "context_length": record.get("context_length") or record.get("max_context_length"),
                "modalities": _extract_modalities(record),
                "slug": record.get("slug"),
                "canonical_slug": record.get("canonical_slug"),
            }

            if record.get("aliases"):
                display_metadata["aliases"] = record.get("aliases")

            pricing = record.get("pricing") or {}
            capabilities = {
                "context_length": record.get("context_length") or record.get("max_context_length"),
                "max_output_tokens": record.get("max_tokens") or record.get("max_output_tokens"),
                "modalities": _extract_modalities(record),
                "supported_parameters": record.get("supported_parameters"),
                "default_parameters": record.get("default_parameters"),
                "features": record.get("features"),
            }

            metadata = {
                "slug": record.get("slug"),
                "canonical_slug": record.get("canonical_slug"),
                "provider_slug": record.get("provider_slug"),
                "source_gateway": record.get("source_gateway"),
            }

            provider = CanonicalModelProvider(
                provider_slug=normalized_provider,
                native_model_id=record.get("id") or canonical_id,
                capabilities={k: v for k, v in capabilities.items() if v not in (None, [], {})},
                pricing=pricing,
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

            registry.register_canonical_provider(canonical_id, display_metadata, provider)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.debug("Canonical registration failed for %s: %s", provider_slug, exc)


def _fresh_cached_models(cache: dict, provider_slug: str):
    if cache.get("data") and cache.get("timestamp"):
        cache_age = (datetime.now(UTC) - cache["timestamp"]).total_seconds()
        if cache_age < cache.get("ttl", 0):
            _register_canonical_records(provider_slug, cache["data"])
            return cache["data"]
    return None


def _get_fresh_or_stale_cached_models(cache: dict, provider_slug: str):
    """Get fresh cache if available, or stale cache within stale_ttl window.

    Implements stale-while-revalidate pattern for improved resilience:
    - Returns fresh cache if within normal TTL
    - Returns stale cache if still within stale_ttl (2x TTL) and no fresh data available
    - Allows serving stale data while background processes revalidate
    - Prevents timeout failures from blocking catalog assembly

    Args:
        cache: Cache dictionary with data, timestamp, ttl, stale_ttl keys
        provider_slug: Provider identifier for canonical registration

    Returns:
        Cached data if available and valid (fresh or stale), None otherwise
    """
    # Explicitly check for None to allow empty lists to be cached
    if cache.get("data") is None or not cache.get("timestamp"):
        return None

    cache_age = (datetime.now(UTC) - cache["timestamp"]).total_seconds()
    ttl = cache.get("ttl", 3600)
    stale_ttl = cache.get("stale_ttl", 7200)

    if cache_age < ttl:
        # Fresh cache
        _register_canonical_records(provider_slug, cache["data"])
        return cache["data"]
    elif cache_age < stale_ttl:
        # Stale but still usable (stale-while-revalidate)
        logger.debug(
            f"{provider_slug} serving stale cache (age: {cache_age:.1f}s, stale_ttl: {stale_ttl}s)"
        )
        _register_canonical_records(provider_slug, cache["data"])
        return cache["data"]

    return None


def sanitize_pricing(pricing: dict) -> dict | None:
    """
    Sanitize pricing data by handling negative values.

    OpenRouter uses -1 to indicate dynamic pricing (e.g., for auto-routing models).
    Since we can't determine the actual cost for dynamic pricing models, we return
    None to indicate this model should be filtered out.

    Args:
        pricing: Pricing dictionary from API

    Returns:
        Sanitized pricing dictionary, or None if pricing is dynamic/indeterminate
    """
    if not pricing or not isinstance(pricing, dict):
        return pricing

    sanitized = pricing.copy()
    has_dynamic_pricing = False

    for key in ["prompt", "completion", "request", "image", "web_search", "internal_reasoning"]:
        if key in sanitized:
            try:
                value = sanitized[key]
                if value is not None:
                    # Convert to float and check if negative
                    float_value = float(value)
                    if float_value < 0:
                        # Mark as dynamic pricing - we can't determine actual cost
                        has_dynamic_pricing = True
                        logger.debug(
                            "Found dynamic pricing %s=%s, model will be filtered",
                            sanitize_for_logging(key),
                            sanitize_for_logging(str(value)),
                        )
                        break
            except (ValueError, TypeError):
                # Keep the original value if conversion fails
                pass

    # If model has dynamic pricing, return None to filter it out
    if has_dynamic_pricing:
        return None

    return sanitized


# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()


# NOTE: FAL and Featherless cache initialization has been migrated to Redis.
# The old cache.py initialization functions are no longer needed.
# Cache initialization now happens automatically when models are first fetched.
logger.debug("FAL and Featherless caches now use Redis - initialization on-demand")


def load_featherless_catalog_export() -> list:
    """
    Load Featherless models from a static export CSV if available.
    Returns a list of normalized model records or None.
    """
    try:
        repo_root = Path(__file__).resolve().parents[2]
        export_candidates = [
            repo_root / "models_export_2025-10-16_202520.csv",
            repo_root / "models_export_2025-10-16_202501.csv",
        ]

        for csv_path in export_candidates:
            if not csv_path.exists():
                continue

            logger.info(f"Loading Featherless catalog export from {csv_path}")
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [
                    row for row in reader if (row.get("gateway") or "").lower() == "featherless"
                ]

            if not rows:
                logger.warning(f"No Featherless rows found in export {csv_path}")
                continue

            normalized = []
            for row in rows:
                provider_model_id = row.get("id")
                if not provider_model_id:
                    continue

                try:
                    context_length = int(float(row.get("context_length", 0) or 0))
                except (TypeError, ValueError):
                    context_length = 0

                def parse_price(value: str) -> str:
                    try:
                        if value is None or value == "":
                            return "0"
                        return str(float(value))
                    except (TypeError, ValueError):
                        return "0"

                prompt_price = parse_price(row.get("prompt_price"))
                completion_price = parse_price(row.get("completion_price"))

                normalized.append(
                    {
                        "id": provider_model_id,
                        "slug": provider_model_id,
                        "canonical_slug": provider_model_id,
                        "hugging_face_id": None,
                        "name": row.get("name") or provider_model_id,
                        "created": None,
                        "description": row.get("description")
                        or f"Featherless catalog entry for {provider_model_id}.",
                        "context_length": context_length,
                        "architecture": {
                            "modality": row.get("modality") or MODALITY_TEXT_TO_TEXT,
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                            "tokenizer": None,
                            "instruct_type": None,
                        },
                        "pricing": {
                            "prompt": prompt_price,
                            "completion": completion_price,
                            "request": "0",
                            "image": "0",
                            "web_search": "0",
                            "internal_reasoning": "0",
                        },
                        "per_request_limits": None,
                        "supported_parameters": [],
                        "default_parameters": {},
                        "provider_slug": row.get("provider_slug")
                        or (
                            provider_model_id.split("/")[0]
                            if "/" in provider_model_id
                            else "featherless"
                        ),
                        "provider_site_url": None,
                        "model_logo_url": None,
                        "source_gateway": "featherless",
                        "raw_featherless": row,
                    }
                )

            logger.info(f"Loaded {len(normalized)} Featherless models from export {csv_path}")
            return normalized
        return None
    except Exception as exc:
        logger.error(
            "Failed to load Featherless catalog export: %s",
            sanitize_for_logging(str(exc)),
            exc_info=True,
        )
        return None


# Thread pool for background cache revalidation
_revalidation_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cache-revalidate")


def revalidate_cache_in_background(gateway: str, fetch_function):
    """Trigger background revalidation of cache"""

    def _revalidate():
        try:
            logger.info("Background revalidation started for %s", sanitize_for_logging(gateway))
            fetch_function()
            logger.info("Background revalidation completed for %s", sanitize_for_logging(gateway))
        except Exception as e:
            logger.warning(
                "Background revalidation failed for %s: %s",
                sanitize_for_logging(gateway),
                sanitize_for_logging(str(e)),
            )

    _revalidation_executor.submit(_revalidate)


def get_all_models_parallel():
    """Fetch models from all gateways in parallel for improved performance.

    Optimization: First tries the full catalog cache (single Redis read).
    Only falls back to per-gateway parallel fetching on cache miss.
    This prevents 30+ concurrent DB queries when the health page is loaded.
    """
    # Fast path: try full catalog cache first (1 Redis read vs 30)
    try:
        from src.services.model_catalog_cache import get_cached_full_catalog

        full_catalog = get_cached_full_catalog()
        if full_catalog:
            logger.info(
                "get_all_models_parallel: Full catalog cache HIT (%d models), "
                "skipping per-gateway fetching",
                len(full_catalog),
            )
            return full_catalog
        logger.info(
            "get_all_models_parallel: Full catalog cache MISS, "
            "falling back to per-gateway parallel fetching"
        )
    except Exception as e:
        logger.warning("get_all_models_parallel: Full catalog cache check failed: %s", e)

    # Slow path: per-gateway parallel fetching (original implementation)
    try:
        from src.db.providers_db import get_active_provider_slugs

        gateways = get_active_provider_slugs()

        # Filter out gateways that are currently in error state (circuit breaker pattern)
        active_gateways = []
        for gw in gateways:
            if is_gateway_in_error_state(gw):
                error_msg = get_gateway_error_message(gw)
                logger.info(
                    "Skipping %s in parallel fetch - gateway in error state: %s",
                    sanitize_for_logging(gw),
                    sanitize_for_logging(error_msg or "unknown error")[:100],
                )
            else:
                active_gateways.append(gw)

        logger.info(
            "Fetching from %d/%d active gateways (%d in error state)",
            len(active_gateways),
            len(gateways),
            len(gateways) - len(active_gateways),
        )

        # Use ThreadPoolExecutor to fetch all gateways in parallel
        # Since get_cached_models uses synchronous httpx, we use threads
        # Increased max_workers from 8 to 12 for better parallelism with 30 gateways
        # (allows ~3 rounds instead of ~4 rounds of execution)
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(get_cached_models, gw): gw for gw in active_gateways}
            all_models = []

            # Use as_completed() to process results as they arrive instead of
            # waiting for each future in submission order. This prevents slow
            # gateways from blocking processing of faster ones.
            # Overall timeout of 45s ensures we stay well under Cloudflare's 100s limit
            try:
                for future in as_completed(futures, timeout=45):
                    gateway_name = futures[future]
                    try:
                        models = future.result(
                            timeout=5
                        )  # Short timeout since future is already complete
                        if models:
                            all_models.extend(models)
                            logger.debug(
                                "Fetched %d models from %s",
                                len(models),
                                sanitize_for_logging(gateway_name),
                            )
                    except TimeoutError:
                        logger.warning(
                            "Timeout fetching models from %s",
                            sanitize_for_logging(gateway_name),
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to fetch models from %s: %s",
                            sanitize_for_logging(gateway_name),
                            sanitize_for_logging(str(e)),
                        )
            except FuturesTimeoutError:
                logger.warning(
                    "Overall timeout (45s) reached for parallel model fetching; "
                    "returning %d models collected so far from %d gateways",
                    len(all_models),
                    len([f for f in futures if f.done()]),
                )

            return all_models
    except Exception as e:
        logger.error("Error in parallel model fetching: %s", sanitize_for_logging(str(e)))
        # Fallback to sequential fetching
        return get_all_models_sequential()


def get_all_models_sequential():
    """Fallback sequential fetching (original implementation)"""
    from src.db.providers_db import get_active_provider_slugs

    all_models = []
    for slug in get_active_provider_slugs():
        models = get_cached_models(slug) or []
        all_models.extend(models)
    return all_models


def _build_multi_provider_catalog() -> AggregatedCatalog:
    registry = get_registry()
    registry.reset_canonical_models()

    try:
        register_google_models_in_canonical_registry()
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.debug("Failed to register Google canonical models: %s", exc)

    # Set flag to prevent circular dependencies during catalog building
    _set_building_catalog(True)
    try:
        models = get_all_models_parallel()
        canonical_snapshot = registry.get_canonical_catalog_snapshot()
        return AggregatedCatalog(models, canonical_snapshot)
    finally:
        _set_building_catalog(False)


def _refresh_multi_provider_catalog_cache() -> AggregatedCatalog:
    catalog = _build_multi_provider_catalog()
    try:
        redis_manager = get_redis_manager()
        # Store the catalog in Redis with 1-hour TTL
        redis_manager.set_json(
            "multi_provider_catalog:data",
            catalog.to_dict() if hasattr(catalog, "to_dict") else catalog.__dict__,
            ttl=3600,
        )
        redis_manager.set(
            "multi_provider_catalog:timestamp", datetime.now(UTC).isoformat(), ttl=3600
        )
    except Exception as e:
        logger.warning(f"Failed to cache multi-provider catalog in Redis: {e}")
    return catalog


def filter_valid_enhanced_models(models: list) -> list:
    """Filter out models missing required fields before returning to callers.

    Required fields:
    - "id": must be a non-empty string

    Logs a warning if any models are filtered out.
    """
    valid = [
        m
        for m in models
        if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"].strip()
    ]
    count = len(models) - len(valid)
    if count:
        logger.warning(f"Filtered {count} models missing required fields")
    return valid


def get_cached_models(gateway: str = "openrouter", use_unique_models: bool = False):
    """
    Get cached models from database-first architecture.

    Flow:
    1. Check Redis cache
    2. On miss, fetch from database (not provider API)
    3. Cache result

    The database is kept fresh by scheduled background sync (see scheduled_sync.py).

    Args:
        gateway: Gateway/provider name (e.g., "openrouter", "all")
        use_unique_models: If True and gateway='all', returns deduplicated models
                          with provider arrays. If False, returns flat catalog
                          (current behavior). Default: False for backward compatibility.

    Returns:
        List of models from cache or database
    """
    from src.services.model_catalog_cache import (
        get_cached_full_catalog,
        get_cached_provider_catalog,
    )

    gateway = (gateway or "openrouter").lower()

    logger.debug(f"get_cached_models: gateway={gateway}, use_unique_models={use_unique_models}")

    try:
        if use_unique_models and gateway == "all":
            # New: Deduplicated unique models with provider arrays
            logger.debug("Fetching unique models catalog (deduplicated)")
            models = get_cached_unique_models_catalog()
            if models is not None:
                logger.debug(f"Returning {len(models)} unique models")
                return filter_valid_enhanced_models(models)
            logger.warning("Failed to get unique models catalog")
            return []
        elif gateway == "all":
            # Current: Full aggregated flat catalog
            models = get_cached_full_catalog()
            if models is not None:
                logger.debug(f"Returning {len(models)} models for 'all'")
                return filter_valid_enhanced_models(models)
            # get_cached_full_catalog already fetched from DB on miss
            logger.warning("Failed to get catalog from cache or database")
            return []
        else:
            # Single provider catalog (use_unique_models has no effect here)
            if use_unique_models:
                logger.debug(
                    f"use_unique_models=True ignored for provider-specific gateway '{gateway}' "
                    "(only applies to gateway='all')"
                )
            models = get_cached_provider_catalog(gateway)
            if models is not None:
                logger.debug(f"Returning {len(models)} models for '{gateway}'")
                return filter_valid_enhanced_models(models)
            # get_cached_provider_catalog already fetched from DB on miss
            logger.warning(f"Failed to get {gateway} catalog from cache or database")
            return []

    except Exception as e:
        logger.error(f"Error getting catalog for gateway '{gateway}': {e}")
        return []


def get_cached_unique_models_catalog():
    """
    Get cached unique models with provider grouping.

    This function:
    1. Checks Redis cache first (key: "models:catalog:full")
    2. If miss, queries database using get_all_unique_models_for_catalog()
    3. Transforms results to API format
    4. Caches for 15 minutes
    5. Returns deduplicated models with provider arrays

    Returns:
        List of unique models with provider information:
        [
            {
                'id': 'gpt-4',
                'name': 'GPT-4',
                'providers': [
                    {'slug': 'openrouter', 'pricing': {...}},
                    {'slug': 'groq', 'pricing': {...}}
                ],
                'provider_count': 2,
                'cheapest_provider': 'groq',
                'fastest_provider': 'openrouter'
            }
        ]

    Cache behavior:
    - Uses same cache key as get_cached_full_catalog() for simplicity
    - TTL: 900 seconds (15 minutes)
    - Cache hit rate expected: >95%
    """
    import time

    from src.db.models_catalog_db import (
        get_all_unique_models_for_catalog,
        transform_unique_models_batch,
    )
    from src.services.model_catalog_cache import get_model_catalog_cache

    cache = get_model_catalog_cache()

    try:
        # Try cache first (using distinctive unique models key)
        cached = cache.get_unique_models()
        if cached is not None:
            # Check if cached data has the unique models structure
            if cached and isinstance(cached, list) and len(cached) > 0:
                # If first item has 'providers' array, it's unique models format
                if "providers" in cached[0] and isinstance(cached[0]["providers"], list):
                    logger.info("Cache hit for unique models catalog")
                    return cached

        # Cache miss or wrong format - fetch from database
        logger.info("Cache miss - fetching unique models from database")
        start_time = time.time()

        db_models = get_all_unique_models_for_catalog(include_inactive=False)
        api_models = transform_unique_models_batch(db_models)

        query_time = time.time() - start_time
        logger.info(f"Fetched {len(api_models)} unique models in {query_time:.2f}s")

        if query_time > 1.0:
            logger.warning(f"Slow unique models fetch: {query_time:.2f}s (threshold: 1.0s)")

        # Cache result (TTL: 900 seconds = 15 minutes) using distinctive unique models key
        cache.set_unique_models(api_models, ttl=900)

        return api_models

    except Exception as e:
        logger.error(f"Error getting unique models catalog: {e}")
        return []


def normalize_featherless_model(featherless_model: dict) -> dict:
    """Normalize Featherless catalog entries to resemble OpenRouter model shape"""
    provider_model_id = featherless_model.get("id", "")
    if not provider_model_id:
        return {"source_gateway": "featherless", "raw_featherless": featherless_model or {}}

    # Extract provider slug (everything before the last slash)
    provider_slug = provider_model_id.split("/")[0] if "/" in provider_model_id else "featherless"

    # Model handle is the full ID
    raw_display_name = provider_model_id.replace("-", " ").replace("_", " ").title()
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    description = (
        featherless_model.get("description")
        or f"Featherless catalog entry for {provider_model_id}. Pricing data not available from Featherless API."
    )

    # Use null for unknown pricing (Featherless API doesn't provide pricing)
    pricing = {
        "prompt": featherless_model.get("prompt_price"),
        "completion": featherless_model.get("completion_price"),
        "request": featherless_model.get("request_price"),
        "image": featherless_model.get("image_price"),
        "web_search": featherless_model.get("web_search_price"),
        "internal_reasoning": featherless_model.get("internal_reasoning_price"),
    }

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": provider_model_id,
        "slug": provider_model_id,
        "canonical_slug": provider_model_id,
        "hugging_face_id": None,
        "name": display_name,
        "created": featherless_model.get("created"),
        "description": description,
        "context_length": 0,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": None,
        "model_logo_url": None,
        "source_gateway": "featherless",
        "raw_featherless": featherless_model,
    }

    # Enrich with manual pricing if available
    return enrich_model_with_pricing(normalized, "featherless")


def normalize_groq_model(groq_model: dict) -> dict:
    """Normalize Groq catalog entries to resemble OpenRouter model shape"""
    provider_model_id = groq_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "groq", "raw_groq": groq_model or {}}

    slug = f"groq/{provider_model_id}"
    provider_slug = "groq"

    raw_display_name = (
        groq_model.get("display_name")
        or provider_model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = groq_model.get("owned_by")
    base_description = groq_model.get("description") or f"Groq hosted model {provider_model_id}."
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    metadata = groq_model.get("metadata") or {}
    hugging_face_id = metadata.get("huggingface_repo")

    context_length = metadata.get("context_length") or groq_model.get("context_length") or 0

    # Extract pricing information from API response
    pricing_info = groq_model.get("pricing") or {}
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Groq may return pricing in various formats
    # Check for token-based pricing (cents per token)
    if "cents_per_input_token" in pricing_info or "cents_per_output_token" in pricing_info:
        cents_input = pricing_info.get("cents_per_input_token", 0)
        cents_output = pricing_info.get("cents_per_output_token", 0)

        # Convert cents to dollars per token
        if cents_input:
            pricing["prompt"] = str(cents_input / 100)
        if cents_output:
            pricing["completion"] = str(cents_output / 100)

    # Check for direct dollar-based pricing
    elif "input" in pricing_info or "output" in pricing_info:
        if pricing_info.get("input"):
            pricing["prompt"] = str(pricing_info["input"])
        if pricing_info.get("output"):
            pricing["completion"] = str(pricing_info["output"])

    architecture = {
        "modality": metadata.get("modality", MODALITY_TEXT_TO_TEXT),
        "input_modalities": metadata.get("input_modalities") or ["text"],
        "output_modalities": metadata.get("output_modalities") or ["text"],
        "tokenizer": metadata.get("tokenizer"),
        "instruct_type": metadata.get("instruct_type"),
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": hugging_face_id,
        "name": display_name,
        "created": groq_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://groq.com",
        "model_logo_url": metadata.get("model_logo_url"),
        "source_gateway": "groq",
        "raw_groq": groq_model,
    }

    return enrich_model_with_pricing(normalized, "groq")


def normalize_fireworks_model(fireworks_model: dict) -> dict:
    """Normalize Fireworks catalog entries to resemble OpenRouter model shape"""
    provider_model_id = fireworks_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "fireworks", "raw_fireworks": fireworks_model or {}}

    # Fireworks uses format like "accounts/fireworks/models/deepseek-v3p1"
    # We'll keep the full ID as-is
    slug = provider_model_id
    provider_slug = "fireworks"

    raw_display_name = (
        fireworks_model.get("display_name")
        or provider_model_id.split("/")[-1].replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = fireworks_model.get("owned_by")
    base_description = (
        fireworks_model.get("description") or f"Fireworks hosted model {provider_model_id}."
    )
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    metadata = fireworks_model.get("metadata") or {}
    context_length = metadata.get("context_length") or fireworks_model.get("context_length") or 0

    # Extract pricing information from API response
    pricing_info = fireworks_model.get("pricing") or {}
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Fireworks may return pricing in various formats
    # Check for token-based pricing (cents per token)
    if "cents_per_input_token" in pricing_info or "cents_per_output_token" in pricing_info:
        cents_input = pricing_info.get("cents_per_input_token", 0)
        cents_output = pricing_info.get("cents_per_output_token", 0)

        # Convert cents to dollars per token
        if cents_input:
            pricing["prompt"] = str(cents_input / 100)
        if cents_output:
            pricing["completion"] = str(cents_output / 100)

    # Check for direct dollar-based pricing
    elif "input" in pricing_info or "output" in pricing_info:
        if pricing_info.get("input"):
            pricing["prompt"] = str(pricing_info["input"])
        if pricing_info.get("output"):
            pricing["completion"] = str(pricing_info["output"])

    architecture = {
        "modality": metadata.get("modality", MODALITY_TEXT_TO_TEXT),
        "input_modalities": metadata.get("input_modalities") or ["text"],
        "output_modalities": metadata.get("output_modalities") or ["text"],
        "tokenizer": metadata.get("tokenizer"),
        "instruct_type": metadata.get("instruct_type"),
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": fireworks_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://fireworks.ai",
        "model_logo_url": None,
        "source_gateway": "fireworks",
        "raw_fireworks": fireworks_model,
    }

    return enrich_model_with_pricing(normalized, "fireworks")


def fetch_specific_model_from_openrouter(provider_name: str, model_name: str):
    """Fetch specific model data from OpenRouter by searching cached models"""
    try:
        # Construct the model ID
        provider_model_id = f"{provider_name}/{model_name}"
        provider_model_id_lower = provider_model_id.lower()

        # First check cache
        openrouter_models = get_cached_models("openrouter")
        if openrouter_models:
            for model in openrouter_models:
                if model.get("id", "").lower() == provider_model_id_lower:
                    return model

        # If not in cache, try to fetch fresh data
        fresh_models = fetch_models_from_openrouter()  # noqa: F821
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == provider_model_id_lower:
                    return model

        logger.warning(
            "Model %s not found in OpenRouter catalog", sanitize_for_logging(provider_model_id)
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from OpenRouter: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def normalize_together_model(together_model: dict) -> dict:
    """Normalize Together catalog entries to resemble OpenRouter model shape"""
    provider_model_id = together_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "together", "raw_together": together_model or {}}

    slug = provider_model_id
    provider_slug = "together"

    # Get display name from API or generate from model ID
    raw_display_name = (
        together_model.get("display_name")
        or provider_model_id.replace("/", " / ").replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove parentheses with size info, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = together_model.get("owned_by") or together_model.get("organization")
    base_description = (
        together_model.get("description") or f"Together hosted model {provider_model_id}."
    )
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    context_length = together_model.get("context_length", 0)

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract pricing if available
    pricing_info = together_model.get("pricing", {})
    if pricing_info:
        pricing["prompt"] = pricing_info.get("input")
        pricing["completion"] = pricing_info.get("output")

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": together_model.get("config", {}).get("tokenizer"),
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": together_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://together.ai",
        "model_logo_url": None,
        "source_gateway": "together",
        "raw_together": together_model,
    }

    return enrich_model_with_pricing(normalized, "together")


def fetch_specific_model_from_together(provider_name: str, model_name: str):
    """Fetch specific model data from Together by searching cached models"""
    try:
        provider_model_id = f"{provider_name}/{model_name}"

        together_models = get_cached_models("together")
        if together_models:
            for model in together_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        fresh_models = fetch_models_from_together()  # noqa: F821
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        logger.warning(
            "Model %s not found in Together catalog", sanitize_for_logging(provider_model_id)
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Together: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_featherless(provider_name: str, model_name: str):
    """Fetch specific model data from Featherless by searching cached models"""
    try:
        # Construct the model ID
        provider_model_id = f"{provider_name}/{model_name}"

        # First check cache
        featherless_models = get_cached_models("featherless")
        if featherless_models:
            for model in featherless_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        # If not in cache, try to fetch fresh data
        fresh_models = fetch_models_from_featherless()  # noqa: F821
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        logger.warning(
            "Model %s not found in Featherless catalog", sanitize_for_logging(provider_model_id)
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Featherless: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_deepinfra(provider_name: str, model_name: str):
    """Fetch specific model data from DeepInfra API"""
    try:
        if not Config.DEEPINFRA_API_KEY:
            logger.error("DeepInfra API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.DEEPINFRA_API_KEY}",
            "Content-Type": "application/json",
        }

        # Construct the model ID
        provider_model_id = f"{provider_name}/{model_name}"

        # DeepInfra uses standard /v1/models endpoint
        response = httpx.get(
            "https://api.deepinfra.com/v1/openai/models", headers=headers, timeout=20.0
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        # Search for the specific model
        for model in models:
            if model.get("id", "").lower() == provider_model_id.lower():
                # Normalize to our schema
                return normalize_deepinfra_model(model)

        logger.warning(
            "Model %s not found in DeepInfra catalog", sanitize_for_logging(provider_model_id)
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from DeepInfra: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def normalize_deepinfra_model(deepinfra_model: dict) -> dict:
    """Normalize DeepInfra model to our schema"""
    # DeepInfra /models/list uses 'model_name' instead of 'id'
    provider_model_id = deepinfra_model.get("model_name") or deepinfra_model.get("id", "")
    if not provider_model_id:
        return {"source_gateway": "deepinfra", "raw_deepinfra": deepinfra_model or {}}

    provider_slug = provider_model_id.split("/")[0] if "/" in provider_model_id else "deepinfra"
    raw_display_name = provider_model_id.replace("-", " ").replace("_", " ").title()
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get model type to determine modality
    model_type = deepinfra_model.get("type") or deepinfra_model.get("reported_type") or "text"

    # Build description with deprecation notice if applicable
    base_description = (
        deepinfra_model.get("description") or f"DeepInfra hosted model: {provider_model_id}."
    )
    if deepinfra_model.get("deprecated"):
        replaced_by = deepinfra_model.get("replaced_by")
        if replaced_by:
            base_description = f"{base_description} Note: This model is deprecated and has been replaced by {replaced_by}."
        else:
            base_description = f"{base_description} Note: This model is deprecated."
    description = f"{base_description} Pricing data may vary by region and usage."

    # Extract pricing information
    pricing_info = deepinfra_model.get("pricing", {})
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract token-based pricing (text-generation, embeddings, etc.)
    # DeepInfra returns pricing in cents per token, convert to dollars per token
    if "cents_per_input_token" in pricing_info or "cents_per_output_token" in pricing_info:
        cents_input = pricing_info.get("cents_per_input_token", 0)
        cents_output = pricing_info.get("cents_per_output_token", 0)

        # Convert cents to dollars per token
        if cents_input:
            pricing["prompt"] = str(cents_input / 100)
        if cents_output:
            pricing["completion"] = str(cents_output / 100)

    # Extract image unit pricing (text-to-image models)
    elif pricing_info.get("type") == "image_units" or "cents_per_image_unit" in pricing_info:
        cents_per_image = pricing_info.get("cents_per_image_unit", 0)
        # Convert cents to dollars per image
        if cents_per_image:
            pricing["image"] = str(cents_per_image / 100)

    # If pricing is time-based (legacy image generation), convert to image pricing
    elif pricing_info.get("type") == "time" and model_type in ("text-to-image", "image"):
        cents_per_sec = pricing_info.get("cents_per_sec", 0)
        # Convert cents per second to dollars per image (assume ~5 seconds per image)
        pricing["image"] = str(cents_per_sec * 5 / 100) if cents_per_sec else None

    # Determine modality based on model type
    modality = MODALITY_TEXT_TO_TEXT
    input_modalities = ["text"]
    output_modalities = ["text"]

    if model_type in ("text-to-image", "image"):
        modality = MODALITY_TEXT_TO_IMAGE
        input_modalities = ["text"]
        output_modalities = ["image"]
    elif model_type in ("text-to-speech", "tts"):
        modality = MODALITY_TEXT_TO_AUDIO
        input_modalities = ["text"]
        output_modalities = ["audio"]
    elif model_type in ("speech-to-text", "stt"):
        modality = "audio->text"
        input_modalities = ["audio"]
        output_modalities = ["text"]
    elif model_type == "multimodal":
        modality = "multimodal"
        input_modalities = ["text", "image"]
        output_modalities = ["text"]

    architecture = {
        "modality": modality,
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": provider_model_id,
        "slug": provider_model_id,
        "canonical_slug": provider_model_id,
        "hugging_face_id": None,
        "name": display_name,
        "created": deepinfra_model.get("created"),
        "description": description,
        "context_length": 0,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": None,
        "model_logo_url": None,
        "source_gateway": "deepinfra",
        "raw_deepinfra": deepinfra_model,
    }

    # Enrich with manual pricing if available
    return enrich_model_with_pricing(normalized, "deepinfra")


def fetch_specific_model_from_groq(provider_name: str, model_name: str):
    """Fetch specific model data from Groq by searching cached models"""
    try:
        provider_model_id = f"{provider_name}/{model_name}"

        groq_models = get_cached_models("groq")
        if groq_models:
            for model in groq_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        fresh_models = fetch_models_from_groq()  # noqa: F821
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        logger.warning(
            "Model %s not found in Groq catalog", sanitize_for_logging(provider_model_id)
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Groq: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_fireworks(provider_name: str, model_name: str):
    """Fetch specific model data from Fireworks by searching cached models"""
    try:
        provider_model_id = f"{provider_name}/{model_name}"

        fireworks_models = get_cached_models("fireworks")
        if fireworks_models:
            for model in fireworks_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        fresh_models = fetch_models_from_fireworks()  # noqa: F821
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == provider_model_id.lower():
                    return model

        logger.warning(
            "Model %s not found in Fireworks catalog", sanitize_for_logging(provider_model_id)
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Fireworks: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_google_vertex(provider_name: str, model_name: str):
    """Fetch specific model data from Google Vertex AI by searching cached models

    Google Vertex models use a static catalog, so we search the cached models.
    Model IDs can be in formats like:
    - gemini-3-flash (simple name)
    - google/gemini-3-flash (with provider prefix)
    """
    try:
        provider_model_id = f"{provider_name}/{model_name}"
        provider_model_id_lower = provider_model_id.lower()
        # Also check for simple model name without provider prefix
        simple_name = model_name.lower()

        google_models = get_cached_models("google-vertex")
        if google_models:
            for model in google_models:
                cached_id = model.get("id", "").lower()
                # Match full model_id or just the model name
                if cached_id == provider_model_id_lower or cached_id == simple_name:
                    return model

        logger.warning(
            "Model %s not found in Google Vertex AI catalog",
            sanitize_for_logging(provider_model_id),
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Google Vertex AI: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def detect_model_gateway(provider_name: str, model_name: str) -> str:
    """Detect which gateway a model belongs to by searching all caches

    Returns:
        Gateway name: 'openrouter', 'featherless', 'deepinfra', 'groq', 'fireworks', 'together', 'google-vertex', 'cerebras', 'xai', 'novita', or 'openrouter' (default)
    """
    try:
        provider_model_id = f"{provider_name}/{model_name}".lower()

        # Check each gateway's cache
        from src.db.providers_db import get_active_provider_slugs

        gateways = get_active_provider_slugs()

        for gateway in gateways:
            models = get_cached_models(gateway)
            if models:
                for model in models:
                    if model.get("id", "").lower() == provider_model_id:
                        return gateway

        # Default to openrouter if not found
        return "openrouter"
    except Exception as e:
        logger.error(f"Error detecting gateway for model {provider_name}/{model_name}: {e}")
        return "openrouter"


def fetch_specific_model(provider_name: str, model_name: str, gateway: str = None):
    """Fetch specific model from the appropriate gateway

    Args:
        provider_name: Provider name (e.g., 'openai', 'anthropic')
        model_name: Model name (e.g., 'gpt-4', 'claude-3')
        gateway: Optional gateway override. If not provided, auto-detects

    Returns:
        Model data dict or None if not found
    """
    try:
        provider_model_id = f"{provider_name}/{model_name}"
        explicit_gateway = gateway is not None

        detected_gateway = (
            gateway or detect_model_gateway(provider_name, model_name) or "openrouter"
        )
        detected_gateway = detected_gateway.lower()

        override_gateway = detect_provider_from_model_id(provider_model_id)
        override_gateway = override_gateway.lower() if override_gateway else None

        def normalize_gateway(value: str) -> str:
            if not value:
                return None
            return value.lower()

        candidate_gateways = []

        if explicit_gateway:
            normalized_override = normalize_gateway(override_gateway)
            if normalized_override:
                candidate_gateways.append(normalized_override)
            normalized_requested = normalize_gateway(detected_gateway)
            if normalized_requested and normalized_requested not in candidate_gateways:
                candidate_gateways.append(normalized_requested)
        else:
            primary = normalize_gateway(override_gateway) or normalize_gateway(detected_gateway)
            if primary:
                candidate_gateways.append(primary)

            fallback_detected = normalize_gateway(detected_gateway)
            if fallback_detected and fallback_detected not in candidate_gateways:
                candidate_gateways.append(fallback_detected)

        # Add last-resort search targets only when no explicit gateway was requested
        if not explicit_gateway:
            if "openrouter" not in candidate_gateways:
                candidate_gateways.append("openrouter")

        # fetch_specific_model_from_* functions live in this file (models.py),
        # not in individual client modules, so dynamic import doesn't apply here.
        fetchers = {
            "openrouter": fetch_specific_model_from_openrouter,
            "featherless": fetch_specific_model_from_featherless,
            "deepinfra": fetch_specific_model_from_deepinfra,
            "groq": fetch_specific_model_from_groq,
            "fireworks": fetch_specific_model_from_fireworks,
            "together": fetch_specific_model_from_together,
            "google-vertex": fetch_specific_model_from_google_vertex,
        }

        for candidate in candidate_gateways:
            if not candidate:
                continue

            fetcher = fetchers.get(candidate, fetch_specific_model_from_openrouter)
            model_data = fetcher(provider_name, model_name)
            if model_data:
                return model_data

        logger.warning(
            "Model %s not found after checking gateways: %s",
            sanitize_for_logging(provider_model_id),
            sanitize_for_logging(str(candidate_gateways)),
        )
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s (gateways tried: %s): %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(gateway)),
            sanitize_for_logging(str(e)),
        )
        return None


def get_cached_huggingface_model(hugging_face_id: str):
    """Get cached Hugging Face model data or fetch if not cached"""
    try:
        # Check if we have cached data for this specific model in Redis
        redis_manager = get_redis_manager()
        cache_key = f"huggingface:model:{hugging_face_id}"
        cached_data = redis_manager.get_json(cache_key)
        if cached_data:
            return cached_data

        # Fetch from Hugging Face API
        return fetch_huggingface_model(hugging_face_id)
    except Exception as e:
        logger.error(f"Error getting cached Hugging Face model {hugging_face_id}: {e}")
        return None


def fetch_huggingface_model(hugging_face_id: str):
    """Fetch model data from Hugging Face API"""
    try:
        # Hugging Face API endpoint for model info
        url = f"https://huggingface.co/api/models/{hugging_face_id}"

        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()

        model_data = response.json()

        # Cache the result in Redis with 1-hour TTL
        try:
            redis_manager = get_redis_manager()
            cache_key = f"huggingface:model:{hugging_face_id}"
            redis_manager.set_json(cache_key, model_data, ttl=3600)
        except Exception as cache_error:
            logger.warning(f"Failed to cache HuggingFace model {hugging_face_id}: {cache_error}")

        return model_data
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"Hugging Face model {hugging_face_id} not found")
            return None
        else:
            logger.error(f"HTTP error fetching Hugging Face model {hugging_face_id}: {e}")
            return None
    except Exception as e:
        logger.error(f"Failed to fetch Hugging Face model {hugging_face_id}: {e}")
        return None


def enhance_model_with_huggingface_data(openrouter_model: dict) -> dict:
    """Enhance OpenRouter model data with Hugging Face information"""
    try:
        hugging_face_id = openrouter_model.get("hugging_face_id")
        if not hugging_face_id:
            return openrouter_model

        # Get Hugging Face data
        hf_data = get_cached_huggingface_model(hugging_face_id)
        if not hf_data:
            return openrouter_model

        # Extract author data more robustly
        author_data = None
        if hf_data.get("author_data"):
            author_data = {
                "name": hf_data["author_data"].get("name"),
                "fullname": hf_data["author_data"].get("fullname"),
                "avatar_url": hf_data["author_data"].get("avatarUrl"),
                "follower_count": hf_data["author_data"].get("followerCount", 0),
            }
        elif hf_data.get("author"):
            # Fallback: create basic author data from author field
            author_data = {
                "name": hf_data.get("author"),
                "fullname": hf_data.get("author"),
                "avatar_url": None,
                "follower_count": 0,
            }

        # Create enhanced model data
        enhanced_model = {
            **openrouter_model,
            "huggingface_metrics": {
                "downloads": hf_data.get("downloads", 0),
                "likes": hf_data.get("likes", 0),
                "pipeline_tag": hf_data.get("pipeline_tag"),
                "num_parameters": hf_data.get("numParameters"),
                "gated": hf_data.get("gated", False),
                "private": hf_data.get("private", False),
                "last_modified": hf_data.get("lastModified"),
                "author": hf_data.get("author"),
                "author_data": author_data,
                "available_inference_providers": hf_data.get("availableInferenceProviders", []),
                "widget_output_urls": hf_data.get("widgetOutputUrls", []),
                "is_liked_by_user": hf_data.get("isLikedByUser", False),
            },
        }

        return enhanced_model
    except Exception as e:
        logger.error(f"Error enhancing model with Hugging Face data: {e}")
        return openrouter_model


def _extract_model_provider_slug(model: dict) -> str | None:
    """Determine provider slug from a model payload."""
    if not model:
        return None

    provider_slug = model.get("provider_slug")
    if provider_slug:
        provider_slug = str(provider_slug).lower().lstrip("@")
        if provider_slug:
            return provider_slug

    provider_model_id = model.get("id", "")
    if isinstance(provider_model_id, str) and "/" in provider_model_id:
        provider_slug = provider_model_id.split("/")[0].lower().lstrip("@")
        if provider_slug:
            return provider_slug

    source_gateway = model.get("source_gateway")
    if isinstance(source_gateway, str):
        provider_slug = source_gateway.lower().lstrip("@")
        if provider_slug:
            return provider_slug

    return None


def _normalize_provider_slug(provider: Any) -> str | None:
    """Extract and normalize a provider slug from a string or provider record dict.

    This is the canonical slug normalization function for the codebase.
    All other normalization logic (inline or in client modules) should
    reference this function.  It:
      - Accepts a plain string slug or a dict with "slug"/"id"/"provider_slug"/"name" keys
      - Lower-cases the result
      - Strips leading "@" characters

    Importers: use the public alias ``normalize_provider_slug`` exported from
    this module.  Client modules that cannot import at module level (to avoid
    circular imports with models.py) should perform inline normalization with
    the same logic: ``str(slug).lower().lstrip("@")`` and note this location.
    """
    if provider is None:
        return None

    if isinstance(provider, str):
        slug = provider
    else:
        slug = (
            provider.get("slug")
            or provider.get("id")
            or provider.get("provider_slug")
            or provider.get("name")
        )

    if not slug:
        return None

    return str(slug).lower().lstrip("@")


# Public alias — preferred import for callers outside this module.
normalize_provider_slug = _normalize_provider_slug


def get_model_count_by_provider(
    provider_or_models: Any, models_data: list | None = None
) -> int | dict[str, int]:
    """Return model counts.

    Backwards-compatible shim that supports two call styles:
    1. get_model_count_by_provider(\"openai\", models_list) -> int
    2. get_model_count_by_provider(models_list, providers_list) -> dict
    """
    try:
        # Legacy usage: provider slug string + models list -> integer count
        if isinstance(provider_or_models, str) or provider_or_models is None:
            provider_slug = (provider_or_models or "").lower().lstrip("@")
            models = models_data or []
            if not provider_slug or not models:
                return 0

            count = 0
            for model in models:
                model_provider = _extract_model_provider_slug(model)
                if model_provider == provider_slug:
                    count += 1
            return count

        # New usage: models list + providers list -> dict mapping provider->count
        models = provider_or_models or []
        providers = models_data or []

        counts: dict[str, int] = {}

        for model in models:
            slug = _extract_model_provider_slug(model)
            if slug:
                counts[slug] = counts.get(slug, 0) + 1

        # Ensure all provided providers exist in result even if zero
        for provider in providers:
            slug = _normalize_provider_slug(provider)
            if slug and slug not in counts:
                counts[slug] = 0

        return counts
    except Exception as e:
        logger.error(f"Error counting models: {e}")
        return {} if not isinstance(provider_or_models, str) else 0


def enhance_model_with_provider_info(openrouter_model: dict, providers_data: list = None) -> dict:
    """Enhance OpenRouter model data with provider information and logo"""
    try:
        provider_model_id = openrouter_model.get("id", "")

        # Extract provider slug from model id (e.g., "openai/gpt-4" -> "openai")
        provider_slug = None
        if "/" in provider_model_id:
            provider_slug = provider_model_id.split("/")[0]

        # Get provider information
        # Preserve existing provider_site_url if already set (e.g., from HuggingFace normalization)
        provider_site_url = openrouter_model.get("provider_site_url")
        if not provider_site_url and providers_data and provider_slug:
            # Build a lookup dict for O(1) access instead of O(N) linear scan
            if not hasattr(enhance_model_with_provider_info, "_provider_cache"):
                enhance_model_with_provider_info._provider_cache = {}
                enhance_model_with_provider_info._provider_cache_id = None

            # Rebuild cache if providers_data changed (use id() as cheap identity check)
            if enhance_model_with_provider_info._provider_cache_id != id(providers_data):
                enhance_model_with_provider_info._provider_cache = {
                    p.get("slug"): p
                    for p in providers_data
                    if isinstance(p, dict) and p.get("slug")
                }
                enhance_model_with_provider_info._provider_cache_id = id(providers_data)

            matched_provider = enhance_model_with_provider_info._provider_cache.get(provider_slug)
            if matched_provider:
                provider_site_url = matched_provider.get("site_url")

        # Generate model logo URL using Google favicon service
        model_logo_url = None
        if provider_site_url:
            # Extract domain from URL for favicon service
            try:
                parsed_url = urlparse(provider_site_url)
                domain = parsed_url.netloc or parsed_url.path
                # Remove www. prefix if present
                if domain.startswith("www."):
                    domain = domain[4:]
                model_logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                # Use debug logging instead of info to avoid Railway rate limiting
                logger.debug(f"Generated model_logo_url: {model_logo_url}")
            except Exception as e:
                logger.warning(f"Failed to parse provider_site_url '{provider_site_url}': {e}")
                # Fallback to old method
                clean_url = (
                    provider_site_url.replace("https://", "").replace("http://", "").split("/")[0]
                )
                if clean_url.startswith("www."):
                    clean_url = clean_url[4:]
                model_logo_url = f"https://www.google.com/s2/favicons?domain={clean_url}&sz=128"
                logger.debug(f"Generated model_logo_url (fallback): {model_logo_url}")

        # Truncate description to a consistent length to keep response sizes predictable
        description = openrouter_model.get("description")
        if isinstance(description, str) and len(description) > MAX_MODEL_DESCRIPTION_LENGTH:
            description = description[:MAX_MODEL_DESCRIPTION_LENGTH] + "..."

        # Add provider information to model
        enhanced_model = {
            **openrouter_model,
            "description": description,
            "provider_slug": (
                provider_slug if provider_slug else openrouter_model.get("provider_slug")
            ),
            "provider_site_url": provider_site_url,
            "model_logo_url": model_logo_url,
        }

        return enhanced_model
    except Exception as e:
        logger.warning(f"Failed to enhance model {openrouter_model.get('id', 'unknown')}: {e}")
        return openrouter_model


# NOTE: Alibaba quota error tracking functions have been migrated to
# src/services/alibaba_cloud_client.py where they belong.
# They are no longer needed here.


def normalize_alibaba_model(model) -> dict | None:
    """Normalize Alibaba Cloud model to catalog schema

    Alibaba models use OpenAI-compatible naming conventions.
    """
    provider_model_id = getattr(model, "id", None)
    if not provider_model_id:
        logger.warning("Alibaba Cloud model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    raw_model_name = getattr(model, "name", provider_model_id)
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": provider_model_id,
            "slug": f"alibaba/{provider_model_id}",
            "canonical_slug": f"alibaba/{provider_model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": getattr(model, "created_at", None),
            "description": getattr(model, "description", "Model from Alibaba Cloud"),
            "context_length": getattr(model, "context_length", 4096),
            "architecture": {
                "modality": MODALITY_TEXT_TO_TEXT,
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "instruct_type": "chat",
            },
            "pricing": {
                "prompt": "0",
                "completion": "0",
                "request": "0",
                "image": "0",
            },
            "per_request_limits": None,
            "supported_parameters": [],
            "default_parameters": {},
            "provider_slug": "alibaba",
            "provider_site_url": "https://dashscope.aliyun.com",
            "model_logo_url": None,
            "source_gateway": "alibaba",
        }
        return enrich_model_with_pricing(normalized, "alibaba-cloud")
    except Exception as e:
        logger.error("Failed to normalize Alibaba Cloud model: %s", sanitize_for_logging(str(e)))
        return None


# =============================================================================
# OpenAI Direct Provider
# =============================================================================


def normalize_openai_model(openai_model: dict) -> dict | None:
    """Normalize OpenAI model entries to resemble OpenRouter model shape"""
    try:
        provider_model_id = openai_model.get("id")
        if not provider_model_id:
            return None

        slug = f"openai/{provider_model_id}"
        provider_slug = "openai"

        # Generate display name
        raw_display_name = provider_model_id.replace("-", " ").replace("_", " ").title()
        # Clean up common patterns
        raw_display_name = raw_display_name.replace("Gpt ", "GPT-")
        raw_display_name = raw_display_name.replace("O1 ", "o1-")
        raw_display_name = raw_display_name.replace("O3 ", "o3-")
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)

        description = f"OpenAI {provider_model_id} model."

        # Determine context length based on model
        # Context lengths are aligned with manual_pricing.json values
        if "gpt-3.5" in provider_model_id:
            context_length = 16385
        elif "gpt-4-32k" in provider_model_id:
            context_length = 32768
        elif "gpt-4o" in provider_model_id:
            context_length = 128000
        elif provider_model_id in ("o1", "o1-2024-12-17", "o3-mini"):
            # Latest o1 and o3-mini have 200k context
            context_length = 200000
        elif "o1" in provider_model_id or "o3" in provider_model_id:
            # o1-preview, o1-mini have 128k context
            context_length = 128000
        elif "gpt-4-turbo" in provider_model_id:
            context_length = 128000
        elif "gpt-4" in provider_model_id:
            # Base gpt-4 models have 8k context
            context_length = 8192
        else:
            # Default fallback
            context_length = 128000

        # Determine modality
        # Strategy: check metadata capabilities first, fall back to name-based detection.
        # Name-based detection is a last resort that may miss newly released vision models.
        modality = MODALITY_TEXT_TO_TEXT
        input_modalities = ["text"]
        output_modalities = ["text"]

        raw_arch = openai_model.get("architecture") or {}
        raw_input_modalities = (
            raw_arch.get("input_modalities") if isinstance(raw_arch, dict) else None
        )

        if isinstance(raw_input_modalities, list) and raw_input_modalities:
            # Metadata from API response is authoritative
            if "image" in raw_input_modalities:
                modality = "text+image->text"
                input_modalities = raw_input_modalities
        else:
            # Fall back to name-based heuristics only if metadata is absent
            if (
                "vision" in provider_model_id
                or "gpt-4o" in provider_model_id
                or "gpt-4-turbo" in provider_model_id
            ):
                modality = "text+image->text"
                input_modalities = ["text", "image"]

        # Pricing will be enriched from manual pricing data
        pricing = {
            "prompt": None,
            "completion": None,
            "request": None,
            "image": None,
            "web_search": None,
            "internal_reasoning": None,
        }

        architecture = {
            "modality": modality,
            "input_modalities": input_modalities,
            "output_modalities": output_modalities,
            "tokenizer": "tiktoken",
            "instruct_type": "chat",
        }

        normalized = {
            "id": slug,
            "slug": slug,
            "canonical_slug": slug,
            "hugging_face_id": None,
            "name": display_name,
            "created": openai_model.get("created"),
            "description": description,
            "context_length": context_length,
            "architecture": architecture,
            "pricing": pricing,
            "per_request_limits": None,
            "supported_parameters": [
                "temperature",
                "max_tokens",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "stop",
            ],
            "default_parameters": {},
            "provider_slug": provider_slug,
            "provider_site_url": "https://openai.com",
            "model_logo_url": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/openai.svg",
            "source_gateway": "openai",
            "raw_openai": openai_model,
        }

        return enrich_model_with_pricing(normalized, "openai")
    except Exception as e:
        logger.error("Failed to normalize OpenAI model: %s", sanitize_for_logging(str(e)))
        return None


# =============================================================================
# Anthropic Direct Provider
# =============================================================================


def normalize_anthropic_model(anthropic_model: dict) -> dict | None:
    """Normalize Anthropic model entries to resemble OpenRouter model shape

    API response format:
    {
        "id": "claude-3-5-sonnet-20241022",
        "display_name": "Claude 3.5 Sonnet",
        "created_at": "2024-10-22T00:00:00Z",
        "type": "model"
    }
    """
    try:
        provider_model_id = anthropic_model.get("id")
        if not provider_model_id:
            return None

        slug = f"anthropic/{provider_model_id}"
        provider_slug = "anthropic"

        # Use display_name from API, fall back to formatted provider_model_id
        raw_display_name = anthropic_model.get("display_name") or anthropic_model.get(
            "name", provider_model_id
        )
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)
        created_at = anthropic_model.get("created_at")

        # Generate description based on model
        description = f"Anthropic {display_name} model."

        # Determine context length based on model generation
        # Claude 3.x models all have 200k context
        context_length = 200000

        # Determine max output based on model
        # Claude 3.5 models have 8192 max output, older models have 4096
        if "3-5" in provider_model_id or "3.5" in provider_model_id:
            max_output = 8192
        else:
            max_output = 4096

        # Determine vision support.
        # Strategy: check metadata capabilities first, fall back to name-based detection.
        # The Anthropic models API does not yet return input_modalities, so we fall back
        # to name-based heuristics. When the API adds that field this branch will be skipped.
        raw_arch = anthropic_model.get("architecture") or {}
        raw_input_modalities = (
            raw_arch.get("input_modalities") if isinstance(raw_arch, dict) else None
        )

        if isinstance(raw_input_modalities, list) and raw_input_modalities:
            # Metadata from API response is authoritative
            has_vision = "image" in raw_input_modalities
        else:
            # Fall back to name-based heuristics only if metadata is absent.
            # All Claude 3+ models released to date support vision.
            has_vision = provider_model_id.startswith("claude-3")

        # Determine modality
        modality = "text+image->text" if has_vision else MODALITY_TEXT_TO_TEXT
        input_modalities = ["text", "image"] if has_vision else ["text"]
        output_modalities = ["text"]

        # Pricing will be enriched from manual pricing data
        pricing = {
            "prompt": None,
            "completion": None,
            "request": None,
            "image": None,
            "web_search": None,
            "internal_reasoning": None,
        }

        architecture = {
            "modality": modality,
            "input_modalities": input_modalities,
            "output_modalities": output_modalities,
            "tokenizer": "claude",
            "instruct_type": "chat",
            "max_output": max_output,
        }

        normalized = {
            "id": slug,
            "slug": slug,
            "canonical_slug": slug,
            "hugging_face_id": None,
            "name": display_name,
            "created": created_at,
            "description": description,
            "context_length": context_length,
            "architecture": architecture,
            "pricing": pricing,
            "per_request_limits": None,
            "supported_parameters": [
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "stop_sequences",
            ],
            "default_parameters": {},
            "provider_slug": provider_slug,
            "provider_site_url": "https://anthropic.com",
            "model_logo_url": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/anthropic.svg",
            "source_gateway": "anthropic",
            "raw_anthropic": anthropic_model,
        }

        return enrich_model_with_pricing(normalized, "anthropic")
    except Exception as e:
        logger.error("Failed to normalize Anthropic model: %s", sanitize_for_logging(str(e)))
        return None


# Alias for backward compatibility with startup.py and other code that imports get_all_models
get_all_models = get_all_models_parallel
