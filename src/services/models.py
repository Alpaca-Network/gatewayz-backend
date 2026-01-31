import csv
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from src.cache import (
    _FAL_CACHE_INIT_DEFERRED,
    _aihubmix_models_cache,
    _aimo_models_cache,
    _alibaba_models_cache,
    _anannas_models_cache,
    _canopywave_models_cache,
    _cerebras_models_cache,
    _chutes_models_cache,
    _clarifai_models_cache,
    _cloudflare_workers_ai_models_cache,
    _deepinfra_models_cache,
    _fal_models_cache,
    _featherless_models_cache,
    _fireworks_models_cache,
    _google_vertex_models_cache,
    _groq_models_cache,
    _helicone_models_cache,
    _huggingface_cache,
    _huggingface_models_cache,
    _models_cache,
    _morpheus_models_cache,
    _multi_provider_catalog_cache,
    _near_models_cache,
    _nebius_models_cache,
    _novita_models_cache,
    _onerouter_models_cache,
    _simplismart_models_cache,
    _sybil_models_cache,
    _together_models_cache,
    _vercel_ai_gateway_models_cache,
    _xai_models_cache,
    _zai_models_cache,
    clear_gateway_error,
    get_gateway_error_message,
    is_cache_fresh,
    is_gateway_in_error_state,
    set_gateway_error,
    should_revalidate_in_background,
)
from src.config import Config
from src.services.cerebras_client import fetch_models_from_cerebras
from src.services.clarifai_client import fetch_models_from_clarifai
from src.services.cloudflare_workers_ai_client import fetch_models_from_cloudflare_workers_ai
from src.services.google_models_config import register_google_models_in_canonical_registry
from src.services.google_vertex_client import fetch_models_from_google_vertex
from src.services.huggingface_models import fetch_models_from_hug, get_huggingface_model_info
from src.services.model_transformations import detect_provider_from_model_id
from src.services.multi_provider_registry import (
    CanonicalModelProvider,
    get_registry,
)
from src.services.morpheus_client import fetch_models_from_morpheus
from src.services.nebius_client import fetch_models_from_nebius
from src.services.novita_client import fetch_models_from_novita
from src.services.onerouter_client import fetch_models_from_onerouter
from src.services.pricing_lookup import enrich_model_with_pricing
from src.services.canopywave_client import fetch_models_from_canopywave
from src.services.simplismart_client import fetch_models_from_simplismart
from src.services.sybil_client import fetch_models_from_sybil
from src.services.xai_client import fetch_models_from_xai
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)


def get_fallback_models_from_db(provider_slug: str) -> list[dict] | None:
    """
    Get fallback models from the database for a provider.

    This is used when the provider's API is unavailable. Instead of using
    hardcoded static fallback lists, we use the most recent successfully
    synced models from the database.

    Args:
        provider_slug: The provider slug (e.g., 'near', 'cerebras', 'openai')

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
        logger.warning(
            f"Failed to get fallback models from database for {provider_slug}: {e}"
        )
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

        # Handle pricing based on provider expectations
        if provider_slug == "near":
            # Near AI expects inputCostPerToken/outputCostPerToken with amount and scale
            if pricing_prompt is not None:
                # Convert per-token price back to amount with scale -6 (per million tokens)
                raw_model["inputCostPerToken"] = {
                    "amount": float(pricing_prompt) * 1_000_000,
                    "scale": -6,
                }
            if pricing_completion is not None:
                raw_model["outputCostPerToken"] = {
                    "amount": float(pricing_completion) * 1_000_000,
                    "scale": -6,
                }
        else:
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

# Global lock and flag to prevent circular dependencies during catalog building
# Using a global lock instead of threading.local() to ensure the flag is visible
# across all threads spawned by ThreadPoolExecutor during parallel model fetching
_building_catalog_lock = threading.Lock()
_building_catalog_flag = False


def _is_building_catalog() -> bool:
    """Check if we're currently building the model catalog

    Uses a global lock to ensure thread-safety and visibility across
    all threads spawned by ThreadPoolExecutor.
    """
    with _building_catalog_lock:
        return _building_catalog_flag


def _set_building_catalog(active: bool):
    """Set the building catalog flag

    Uses a global lock to ensure thread-safety and visibility across
    all threads spawned by ThreadPoolExecutor.
    """
    global _building_catalog_flag
    with _building_catalog_lock:
        _building_catalog_flag = active


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


def _normalize_provider_slug(provider_slug: str) -> str:
    mapping = {
        "hug": "huggingface",
        "huggingface": "huggingface",
        "google-vertex": "google-vertex",
    }
    return mapping.get(provider_slug.lower(), provider_slug.lower())


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
        cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
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

    cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
    ttl = cache.get("ttl", 3600)
    stale_ttl = cache.get("stale_ttl", 7200)

    if cache_age < ttl:
        # Fresh cache
        _register_canonical_records(provider_slug, cache["data"])
        return cache["data"]
    elif cache_age < stale_ttl:
        # Stale but still usable (stale-while-revalidate)
        logger.debug(f"{provider_slug} serving stale cache (age: {cache_age:.1f}s, stale_ttl: {stale_ttl}s)")
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


# Initialize FAL models cache on module import for better performance
# This ensures FAL models are available immediately without lazy loading
try:
    from src.cache import initialize_fal_cache_from_catalog

    initialize_fal_cache_from_catalog()
except ImportError:
    # Initialization will be deferred to first request if import fails
    logger.debug(f"{_FAL_CACHE_INIT_DEFERRED} on import")

# Initialize Featherless models cache on module import for better performance
# This ensures Featherless cache structure is ready even if no static catalog exists
try:
    from src.cache import initialize_featherless_cache_from_catalog

    initialize_featherless_cache_from_catalog()
except ImportError:
    # Initialization will be deferred to first request if import fails
    logger.debug("Featherless cache initialization deferred on import")


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
                model_id = row.get("id")
                if not model_id:
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
                        "id": model_id,
                        "slug": model_id,
                        "canonical_slug": model_id,
                        "hugging_face_id": None,
                        "name": row.get("name") or model_id,
                        "created": None,
                        "description": row.get("description")
                        or f"Featherless catalog entry for {model_id}.",
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
                        or (model_id.split("/")[0] if "/" in model_id else "featherless"),
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
    """Fetch models from all gateways in parallel for improved performance"""
    try:
        gateways = [
            "openrouter",
            "featherless",
            "deepinfra",
            "cerebras",
            "nebius",
            "xai",
            "novita",
            "hug",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "aimo",
            "near",
            "fal",
            "helicone",
            "anannas",
            "aihubmix",
            "alibaba",
            "onerouter",
            "google-vertex",
            "cloudflare-workers-ai",
            "clarifai",
            "openai",
            "anthropic",
            "simplismart",
            "sybil",
            "canopywave",
            "morpheus",
            "vercel-ai-gateway",
        ]

        # Filter out gateways that are currently in error state (circuit breaker pattern)
        active_gateways = []
        for gw in gateways:
            if is_gateway_in_error_state(gw):
                error_msg = get_gateway_error_message(gw)
                logger.info(
                    "Skipping %s in parallel fetch - gateway in error state: %s",
                    sanitize_for_logging(gw),
                    sanitize_for_logging(error_msg or "unknown error")[:100]
                )
            else:
                active_gateways.append(gw)

        logger.info(
            "Fetching from %d/%d active gateways (%d in error state)",
            len(active_gateways),
            len(gateways),
            len(gateways) - len(active_gateways)
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
                        models = future.result(timeout=5)  # Short timeout since future is already complete
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
    openrouter_models = get_cached_models("openrouter") or []
    featherless_models = get_cached_models("featherless") or []
    deepinfra_models = get_cached_models("deepinfra") or []
    cerebras_models = get_cached_models("cerebras") or []
    nebius_models = get_cached_models("nebius") or []
    xai_models = get_cached_models("xai") or []
    novita_models = get_cached_models("novita") or []
    hug_models = get_cached_models("hug") or []
    chutes_models = get_cached_models("chutes") or []
    groq_models = get_cached_models("groq") or []
    fireworks_models = get_cached_models("fireworks") or []
    together_models = get_cached_models("together") or []
    aimo_models = get_cached_models("aimo") or []
    near_models = get_cached_models("near") or []
    fal_models = get_cached_models("fal") or []
    helicone_models = get_cached_models("helicone") or []
    anannas_models = get_cached_models("anannas") or []
    aihubmix_models = get_cached_models("aihubmix") or []
    alibaba_models = get_cached_models("alibaba") or []
    onerouter_models = get_cached_models("onerouter") or []
    google_vertex_models = get_cached_models("google-vertex") or []
    cloudflare_workers_ai_models = get_cached_models("cloudflare-workers-ai") or []
    clarifai_models = get_cached_models("clarifai") or []
    openai_models = get_cached_models("openai") or []
    anthropic_models = get_cached_models("anthropic") or []
    simplismart_models = get_cached_models("simplismart") or []
    morpheus_models = get_cached_models("morpheus") or []
    sybil_models = get_cached_models("sybil") or []
    canopywave_models = get_cached_models("canopywave") or []
    vercel_ai_gateway_models = get_cached_models("vercel-ai-gateway") or []
    return (
        openrouter_models
        + featherless_models
        + deepinfra_models
        + cerebras_models
        + nebius_models
        + xai_models
        + novita_models
        + hug_models
        + chutes_models
        + groq_models
        + fireworks_models
        + together_models
        + aimo_models
        + near_models
        + fal_models
        + helicone_models
        + anannas_models
        + aihubmix_models
        + alibaba_models
        + onerouter_models
        + google_vertex_models
        + cloudflare_workers_ai_models
        + clarifai_models
        + openai_models
        + anthropic_models
        + simplismart_models
        + morpheus_models
        + sybil_models
        + canopywave_models
        + vercel_ai_gateway_models
    )


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
    _multi_provider_catalog_cache["data"] = catalog
    _multi_provider_catalog_cache["timestamp"] = datetime.now(timezone.utc)
    return catalog


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

    logger.info(f"get_cached_models: gateway={gateway}, use_unique_models={use_unique_models}")

    try:
        if use_unique_models and gateway == "all":
            # New: Deduplicated unique models with provider arrays
            logger.info("Fetching unique models catalog (deduplicated)")
            models = get_cached_unique_models_catalog()
            if models is not None:
                logger.info(f"Returning {len(models)} unique models")
                return models
            logger.warning("Failed to get unique models catalog")
            return []
        elif gateway == "all":
            # Current: Full aggregated flat catalog
            models = get_cached_full_catalog()
            if models is not None:
                logger.info(f"Returning {len(models)} models for 'all'")
                return models
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
                logger.info(f"Returning {len(models)} models for '{gateway}'")
                return models
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
    from src.services.model_catalog_cache import get_model_catalog_cache
    from src.db.models_catalog_db import (
        get_all_unique_models_for_catalog,
        transform_unique_models_batch,
    )
    import time

    cache = get_model_catalog_cache()

    try:
        # Try cache first
        cached = cache.get_full_catalog()
        if cached is not None:
            # Check if cached data has the unique models structure
            if cached and isinstance(cached, list) and len(cached) > 0:
                # If first item has 'providers' array, it's unique models format
                if 'providers' in cached[0] and isinstance(cached[0]['providers'], list):
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
            logger.warning(
                f"Slow unique models fetch: {query_time:.2f}s "
                f"(threshold: 1.0s)"
            )

        # Cache result (TTL: 900 seconds = 15 minutes)
        cache.set_full_catalog(api_models, ttl=900)

        return api_models

    except Exception as e:
        logger.error(f"Error getting unique models catalog: {e}")
        return []




def normalize_featherless_model(featherless_model: dict) -> dict:
    """Normalize Featherless catalog entries to resemble OpenRouter model shape"""
    model_id = featherless_model.get("id", "")
    if not model_id:
        return {"source_gateway": "featherless", "raw_featherless": featherless_model or {}}

    # Extract provider slug (everything before the last slash)
    provider_slug = model_id.split("/")[0] if "/" in model_id else "featherless"

    # Model handle is the full ID
    raw_display_name = model_id.replace("-", " ").replace("_", " ").title()
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    description = (
        featherless_model.get("description")
        or f"Featherless catalog entry for {model_id}. Pricing data not available from Featherless API."
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
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
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


def normalize_chutes_model(chutes_model: dict) -> dict:
    """Normalize Chutes catalog entries to resemble OpenRouter model shape"""
    model_id = chutes_model.get("id", "")
    if not model_id:
        return {"source_gateway": "chutes", "raw_chutes": chutes_model or {}}

    provider_slug = chutes_model.get("provider", "chutes")
    model_type = chutes_model.get("type", "LLM")
    pricing_per_hour = chutes_model.get("pricing_per_hour", 0.0)

    # FIXED: Convert hourly pricing to per-token pricing (rough estimate)
    # Assume ~1M tokens per hour at average speed
    # pricing_per_hour / 1,000,000 = per-token price
    prompt_price = str(pricing_per_hour / 1000000) if pricing_per_hour > 0 else "0"

    raw_display_name = chutes_model.get("name", model_id.replace("-", " ").replace("_", " ").title())
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    description = (
        f"Chutes.ai hosted {model_type} model: {model_id}. Pricing: ${pricing_per_hour}/hr."
    )

    # Determine modality based on type
    modality_map = {
        "LLM": MODALITY_TEXT_TO_TEXT,
        "Image Generation": MODALITY_TEXT_TO_IMAGE,
        "Text to Speech": MODALITY_TEXT_TO_AUDIO,
        "Speech to Text": "audio->text",
        "Video": "text->video",
        "Music Generation": MODALITY_TEXT_TO_AUDIO,
        "Embeddings": "text->embedding",
        "Content Moderation": MODALITY_TEXT_TO_TEXT,
        "Other": "multimodal",
    }

    modality = modality_map.get(model_type, MODALITY_TEXT_TO_TEXT)

    pricing = {
        "prompt": prompt_price,
        "completion": prompt_price,
        "request": "0",
        "image": str(pricing_per_hour) if model_type == "Image Generation" else "0",
        "web_search": "0",
        "internal_reasoning": "0",
        "hourly_rate": str(pricing_per_hour),
    }

    architecture = {
        "modality": modality,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": None,
        "instruct_type": None,
    }

    tags = chutes_model.get("tags", [])

    normalized = {
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
        "hugging_face_id": None,
        "name": display_name,
        "created": None,
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
        "source_gateway": "chutes",
        "model_type": model_type,
        "tags": tags,
        "raw_chutes": chutes_model,
    }

    # Enrich with manual pricing if available (overrides hourly pricing)
    return enrich_model_with_pricing(normalized, "chutes")


def normalize_groq_model(groq_model: dict) -> dict:
    """Normalize Groq catalog entries to resemble OpenRouter model shape"""
    model_id = groq_model.get("id")
    if not model_id:
        return {"source_gateway": "groq", "raw_groq": groq_model or {}}

    slug = f"groq/{model_id}"
    provider_slug = "groq"

    raw_display_name = (
        groq_model.get("display_name") or model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = groq_model.get("owned_by")
    base_description = groq_model.get("description") or f"Groq hosted model {model_id}."
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


def normalize_zai_model(zai_model: dict) -> dict | None:
    """Normalize Z.AI catalog entries to resemble OpenRouter model shape.

    Z.AI provides GLM models (GLM-4.7, GLM-4.5-Air, etc.) with OpenAI-compatible format.
    """
    model_id = zai_model.get("id")
    if not model_id:
        return {"source_gateway": "zai", "raw_zai": zai_model or {}}

    slug = f"zai/{model_id}"
    provider_slug = "zai"

    raw_display_name = (
        zai_model.get("display_name")
        or zai_model.get("name")
        or model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = zai_model.get("owned_by", "zai")
    base_description = zai_model.get("description") or f"Z.AI GLM model {model_id}."
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Provided by Z.AI."
    else:
        description = base_description

    metadata = zai_model.get("metadata") or {}

    # Z.AI models typically have large context windows
    context_length = (
        metadata.get("context_length")
        or zai_model.get("context_length")
        or zai_model.get("context_window")
        or 128000  # Default for GLM models
    )

    # Z.AI pricing - check for various formats
    pricing_info = zai_model.get("pricing") or {}
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Check for direct dollar-based pricing
    if "input" in pricing_info or "output" in pricing_info:
        if pricing_info.get("input"):
            pricing["prompt"] = str(pricing_info["input"])
        if pricing_info.get("output"):
            pricing["completion"] = str(pricing_info["output"])
    elif "prompt" in pricing_info or "completion" in pricing_info:
        if pricing_info.get("prompt"):
            pricing["prompt"] = str(pricing_info["prompt"])
        if pricing_info.get("completion"):
            pricing["completion"] = str(pricing_info["completion"])

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
        "created": zai_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://z.ai",
        "model_logo_url": metadata.get("model_logo_url"),
        "source_gateway": "zai",
        "raw_zai": zai_model,
    }

    return enrich_model_with_pricing(normalized, "zai")


def normalize_fireworks_model(fireworks_model: dict) -> dict:
    """Normalize Fireworks catalog entries to resemble OpenRouter model shape"""
    model_id = fireworks_model.get("id")
    if not model_id:
        return {"source_gateway": "fireworks", "raw_fireworks": fireworks_model or {}}

    # Fireworks uses format like "accounts/fireworks/models/deepseek-v3p1"
    # We'll keep the full ID as-is
    slug = model_id
    provider_slug = "fireworks"

    raw_display_name = (
        fireworks_model.get("display_name")
        or model_id.split("/")[-1].replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = fireworks_model.get("owned_by")
    base_description = fireworks_model.get("description") or f"Fireworks hosted model {model_id}."
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
        model_id = f"{provider_name}/{model_name}"
        model_id_lower = model_id.lower()

        # First check cache
        openrouter_models = get_cached_models("openrouter")
        if openrouter_models:
            for model in openrouter_models:
                if model.get("id", "").lower() == model_id_lower:
                    return model

        # If not in cache, try to fetch fresh data
        fresh_models = fetch_models_from_openrouter()
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == model_id_lower:
                    return model

        logger.warning("Model %s not found in OpenRouter catalog", sanitize_for_logging(model_id))
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
    model_id = together_model.get("id")
    if not model_id:
        return {"source_gateway": "together", "raw_together": together_model or {}}

    slug = model_id
    provider_slug = "together"

    # Get display name from API or generate from model ID
    raw_display_name = (
        together_model.get("display_name")
        or model_id.replace("/", " / ").replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove parentheses with size info, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = together_model.get("owned_by") or together_model.get("organization")
    base_description = together_model.get("description") or f"Together hosted model {model_id}."
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


def normalize_aimo_model(aimo_model: dict) -> dict:
    """Normalize AIMO catalog entries to resemble OpenRouter model shape

    AIMO models use format: provider_pubkey:model_name
    Model data structure:
    - name: base model name (e.g., "DeepSeek-V3-1")
    - display_name: human-readable name
    - providers: list of provider objects with id, name, and pricing
    """
    model_name = aimo_model.get("name")
    if not model_name:
        logger.warning("AIMO model missing 'name' field: %s", sanitize_for_logging(str(aimo_model)))
        return None

    # Normalize model name by stripping common provider prefixes
    # AIMO may return model names like "google/gemini-2.5-pro" or just "gemini-2.5-pro"
    model_name_normalized = model_name
    provider_prefixes = ["google/", "openai/", "anthropic/", "meta/", "meta-llama/", "mistralai/"]
    for prefix in provider_prefixes:
        if model_name.lower().startswith(prefix):
            model_name_normalized = model_name[len(prefix) :]
            break

    # Get provider information (use first provider if multiple)
    providers = aimo_model.get("providers", [])
    if not providers:
        logger.warning("AIMO model '%s' has no providers", sanitize_for_logging(model_name))
        return None

    # For now, use the first provider
    provider = providers[0]
    provider_id = provider.get("id")
    provider_name = provider.get("name", "unknown")

    # Create user-friendly model ID in format: aimo/model_name
    # Use the normalized model name (without provider prefix) for consistency
    # Store the original AIMO format (provider_pubkey:model_name) in raw metadata
    original_aimo_id = f"{provider_id}:{model_name}"
    model_id = f"aimo/{model_name_normalized}"

    slug = model_id
    # Always use "aimo" as the provider slug for AIMO Network models
    provider_slug = "aimo"

    # Create canonical slug from the base model name (without the provider prefix)
    # This allows the model to be grouped with same models from other providers
    canonical_slug = model_name_normalized.lower()

    # Get display name from API or generate from model name
    raw_display_name = aimo_model.get("display_name") or model_name_normalized.replace("-", " ").title()
    # Clean malformed model names (remove company prefix with colon, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    base_description = (
        f"AIMO Network decentralized model {model_name_normalized} provided by {provider_name}."
    )
    description = base_description

    context_length = aimo_model.get("context_length", 0)

    # Extract pricing from provider object
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # AIMO provider pricing
    provider_pricing = provider.get("pricing", {})
    if provider_pricing:
        prompt_price = provider_pricing.get("prompt")
        completion_price = provider_pricing.get("completion")
        # Convert to string if not None
        pricing["prompt"] = str(prompt_price) if prompt_price is not None else None
        pricing["completion"] = str(completion_price) if completion_price is not None else None

    # Extract architecture from AIMO model
    aimo_arch = aimo_model.get("architecture", {})
    input_modalities = aimo_arch.get("input_modalities", ["text"])
    output_modalities = aimo_arch.get("output_modalities", ["text"])

    # Determine modality string
    if input_modalities == ["text"] and output_modalities == ["text"]:
        modality = MODALITY_TEXT_TO_TEXT
    else:
        modality = "multimodal"

    architecture = {
        "modality": modality,
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": canonical_slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": aimo_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://aimo.network",
        "model_logo_url": None,
        "source_gateway": "aimo",
        "raw_aimo": aimo_model,
        "aimo_native_id": original_aimo_id,  # Store original AIMO format for routing
    }

    return enrich_model_with_pricing(normalized, "aimo")


def normalize_near_model(near_model: dict) -> dict:
    """Normalize Near AI catalog entries to resemble OpenRouter model shape

    Near AI features:
    - Private, verifiable AI infrastructure
    - Decentralized execution
    - User-owned AI services
    - Cryptographic verification and on-chain auditing
    """
    model_id = near_model.get("modelId")
    if not model_id:
        # Fallback to 'id' for backward compatibility
        model_id = near_model.get("id")
        if not model_id:
            logger.warning(
                "Near AI model missing 'modelId' field: %s", sanitize_for_logging(str(near_model))
            )
            return None

    slug = f"near/{model_id}"
    provider_slug = "near"

    # Extract metadata from Near AI API response
    metadata = near_model.get("metadata") or {}
    raw_display_name = (
        metadata.get("displayName")
        or near_model.get("display_name")
        or model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    near_model.get("owned_by", "Near Protocol")

    # Highlight security features in description
    base_description = (
        metadata.get("description")
        or near_model.get("description")
        or f"Near AI hosted model {model_id}."
    )
    security_features = " Security: Private AI inference with decentralized execution, cryptographic verification, and on-chain auditing."
    description = f"{base_description}{security_features}"

    context_length = (
        metadata.get("contextLength")
        or metadata.get("context_length")
        or near_model.get("context_length")
        or 0
    )

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract pricing from Near AI API response
    # FIXED: Near AI provides pricing as inputCostPerToken and outputCostPerToken with amount and scale
    # Scale is in powers of 10 (e.g., -9 means 10^-9 = per token)
    # Database stores per-token pricing, so just use amount × 10^scale
    input_cost = near_model.get("inputCostPerToken", {})
    output_cost = near_model.get("outputCostPerToken", {})

    if input_cost and isinstance(input_cost, dict):
        input_amount = input_cost.get("amount", 0)
        input_scale = input_cost.get("scale", -9)  # Default scale is -9 (per token)
        # Per-token price = amount × 10^scale
        if input_amount > 0:
            pricing["prompt"] = str(input_amount * (10 ** input_scale))

    if output_cost and isinstance(output_cost, dict):
        output_amount = output_cost.get("amount", 0)
        output_scale = output_cost.get("scale", -9)  # Default scale is -9 (per token)
        # Per-token price = amount × 10^scale
        if output_amount > 0:
            pricing["completion"] = str(output_amount * (10 ** output_scale))

    # Fallback to old pricing format for backward compatibility
    if not pricing["prompt"] and not pricing["completion"]:
        pricing_info = near_model.get("pricing", {})
        if pricing_info:
            pricing["prompt"] = (
                str(pricing_info.get("prompt")) if pricing_info.get("prompt") is not None else None
            )
            pricing["completion"] = (
                str(pricing_info.get("completion"))
                if pricing_info.get("completion") is not None
                else None
            )

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
        "hugging_face_id": metadata.get("huggingface_repo"),
        "name": display_name,
        "created": near_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://near.ai",
        "model_logo_url": None,
        "source_gateway": "near",
        "raw_near": near_model,
        # Mark all Near AI models as private
        "is_private": True,  # NEAR models support private inference
        "tags": ["Private"],
        # Highlight security features as metadata
        "security_features": {
            "private_inference": True,
            "decentralized": True,
            "verifiable": True,
            "on_chain_auditing": True,
            "user_owned": True,
        },
    }

    return enrich_model_with_pricing(normalized, "near")


def normalize_fal_model(fal_model: dict) -> dict | None:
    """Normalize Fal.ai catalog entries to resemble OpenRouter model shape

    Fal.ai features:
    - 839+ models across text-to-image, text-to-video, image-to-video, etc.
    - Models include FLUX, Stable Diffusion, Veo, Sora, and many more
    - Supports image, video, audio, and 3D generation

    Handles both static catalog format (uses "id") and API format (uses "endpoint_id")
    """
    # API returns "endpoint_id", static catalog uses "id"
    model_id = fal_model.get("endpoint_id") or fal_model.get("id")
    if not model_id:
        logger.warning("Fal.ai model missing 'id'/'endpoint_id' field: %s", sanitize_for_logging(str(fal_model)))
        return None

    # Extract provider from model ID (e.g., "fal-ai/flux-pro" -> "fal-ai")
    provider_slug = model_id.split("/")[0] if "/" in model_id else "fal-ai"

    # Use title (API) or name (catalog) or derive from ID
    raw_display_name = fal_model.get("title") or fal_model.get("name") or model_id.split("/")[-1]
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get description
    description = fal_model.get("description", f"Fal.ai {display_name} model")

    # Determine modality based on type or category (API uses "category")
    model_type = fal_model.get("type") or fal_model.get("category", "text-to-image")
    modality_map = {
        "text-to-image": MODALITY_TEXT_TO_IMAGE,
        "text-to-video": "text->video",
        "image-to-image": "image->image",
        "image-to-video": "image->video",
        "video-to-video": "video->video",
        "text-to-audio": MODALITY_TEXT_TO_AUDIO,
        "text-to-speech": MODALITY_TEXT_TO_AUDIO,
        "audio-to-audio": "audio->audio",
        "image-to-3d": "image->3d",
        "vision": "image->text",
    }
    modality = modality_map.get(model_type, MODALITY_TEXT_TO_IMAGE)

    # Parse input/output modalities
    input_mod, output_mod = modality.split("->") if "->" in modality else ("text", "image")

    architecture = {
        "modality": modality,
        "input_modalities": [input_mod],
        "output_modalities": [output_mod],
        "model_type": model_type,
        "tags": fal_model.get("tags", []),
    }

    # Fal.ai doesn't expose pricing in catalog, set to null
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
    }

    slug = model_id
    canonical_slug = model_id

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": canonical_slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": None,
        "description": description,
        "context_length": None,  # Not applicable for image/video models
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://fal.ai",
        "model_logo_url": None,
        "source_gateway": "fal",
        "raw_fal": fal_model,
    }

    return enrich_model_with_pricing(normalized, "fal")


def normalize_vercel_model(model) -> dict | None:
    """Normalize Vercel AI Gateway model to catalog schema

    Vercel models can originate from various providers (OpenAI, Google, Anthropic, etc.)
    The gateway automatically routes requests to the appropriate provider.
    Pricing is dynamically fetched from the underlying provider's pricing data.
    """
    # Extract model ID
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Vercel model missing 'id' field: %s", sanitize_for_logging(str(model)))
        return None

    # Determine provider from model ID
    # Models come in formats like "openai/gpt-4", "google/gemini-pro", etc.
    if "/" in model_id:
        provider_slug = model_id.split("/")[0]
        raw_display_name = model_id.split("/")[1]
    else:
        provider_slug = "vercel"
        raw_display_name = model_id
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get description - Vercel doesn't provide this, so we create one
    description = getattr(model, "description", None) or "Model available through Vercel AI Gateway"

    # Get context length if available
    context_length = getattr(model, "context_length", 4096)

    # Get created date if available
    created = getattr(model, "created_at", None)

    # Fetch pricing dynamically from Vercel or underlying provider
    pricing = get_vercel_model_pricing(model_id)

    normalized = {
        "id": model_id,
        "slug": f"vercel/{model_id}",
        "canonical_slug": f"vercel/{model_id}",
        "hugging_face_id": None,
        "name": display_name,
        "created": created,
        "description": description,
        "context_length": context_length,
        "architecture": {
            "modality": MODALITY_TEXT_TO_TEXT,
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "instruct_type": "chat",
        },
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://vercel.com/ai-gateway",
        "model_logo_url": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/vercel.svg",
        "source_gateway": "vercel-ai-gateway",
    }

    return enrich_model_with_pricing(normalized, "vercel-ai-gateway")


def get_vercel_model_pricing(model_id: str) -> dict:
    """Get pricing for a Vercel AI Gateway model

    Fetches pricing from Vercel or the underlying provider.
    Falls back to default zero pricing if unavailable.

    Args:
        model_id: Model identifier (e.g., "openai/gpt-4")

    Returns:
        dict with 'prompt', 'completion', 'request', and 'image' pricing fields
    """
    try:
        from src.services.vercel_ai_gateway_client import fetch_model_pricing_from_vercel

        # Attempt to fetch pricing from Vercel or underlying provider
        pricing_data = fetch_model_pricing_from_vercel(model_id)

        if pricing_data:
            # Normalize to standard schema with default zeros for missing fields
            return {
                "prompt": str(pricing_data.get("prompt", "0")),
                "completion": str(pricing_data.get("completion", "0")),
                "request": str(pricing_data.get("request", "0")),
                "image": str(pricing_data.get("image", "0")),
            }
    except Exception as e:
        logger.debug(
            "Failed to fetch Vercel pricing for %s: %s",
            sanitize_for_logging(model_id),
            sanitize_for_logging(str(e)),
        )

    # Fallback: return default zero pricing
    return {
        "prompt": "0",
        "completion": "0",
        "request": "0",
        "image": "0",
    }


def fetch_specific_model_from_together(provider_name: str, model_name: str):
    """Fetch specific model data from Together by searching cached models"""
    try:
        model_id = f"{provider_name}/{model_name}"

        together_models = get_cached_models("together")
        if together_models:
            for model in together_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        fresh_models = fetch_models_from_together()
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        logger.warning("Model %s not found in Together catalog", sanitize_for_logging(model_id))
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
        model_id = f"{provider_name}/{model_name}"

        # First check cache
        featherless_models = get_cached_models("featherless")
        if featherless_models:
            for model in featherless_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        # If not in cache, try to fetch fresh data
        fresh_models = fetch_models_from_featherless()
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        logger.warning("Model %s not found in Featherless catalog", sanitize_for_logging(model_id))
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
        model_id = f"{provider_name}/{model_name}"

        # DeepInfra uses standard /v1/models endpoint
        response = httpx.get(
            "https://api.deepinfra.com/v1/openai/models", headers=headers, timeout=20.0
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        # Search for the specific model
        for model in models:
            if model.get("id", "").lower() == model_id.lower():
                # Normalize to our schema
                return normalize_deepinfra_model(model)

        logger.warning("Model %s not found in DeepInfra catalog", sanitize_for_logging(model_id))
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
    model_id = deepinfra_model.get("model_name") or deepinfra_model.get("id", "")
    if not model_id:
        return {"source_gateway": "deepinfra", "raw_deepinfra": deepinfra_model or {}}

    provider_slug = model_id.split("/")[0] if "/" in model_id else "deepinfra"
    raw_display_name = model_id.replace("-", " ").replace("_", " ").title()
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get model type to determine modality
    model_type = deepinfra_model.get("type") or deepinfra_model.get("reported_type") or "text"

    # Build description with deprecation notice if applicable
    base_description = deepinfra_model.get("description") or f"DeepInfra hosted model: {model_id}."
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
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
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


def fetch_specific_model_from_chutes(provider_name: str, model_name: str):
    """Fetch specific model data from Chutes by searching cached models"""
    try:
        # Construct the model ID
        model_id = f"{provider_name}/{model_name}"

        # First check cache
        chutes_models = get_cached_models("chutes")
        if chutes_models:
            for model in chutes_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        # If not in cache, try to fetch fresh data
        fresh_models = fetch_models_from_chutes()
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        logger.warning("Model %s not found in Chutes catalog", sanitize_for_logging(model_id))
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Chutes: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_groq(provider_name: str, model_name: str):
    """Fetch specific model data from Groq by searching cached models"""
    try:
        model_id = f"{provider_name}/{model_name}"

        groq_models = get_cached_models("groq")
        if groq_models:
            for model in groq_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        fresh_models = fetch_models_from_groq()
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        logger.warning("Model %s not found in Groq catalog", sanitize_for_logging(model_id))
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
        model_id = f"{provider_name}/{model_name}"

        fireworks_models = get_cached_models("fireworks")
        if fireworks_models:
            for model in fireworks_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        fresh_models = fetch_models_from_fireworks()
        if fresh_models:
            for model in fresh_models:
                if model.get("id", "").lower() == model_id.lower():
                    return model

        logger.warning("Model %s not found in Fireworks catalog", sanitize_for_logging(model_id))
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Fireworks: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_huggingface(provider_name: str, model_name: str):
    """Fetch specific model data from Hugging Face by using direct lookup or cached models"""
    try:
        model_id = f"{provider_name}/{model_name}"
        model_id_lower = model_id.lower()

        # Try lightweight direct lookup first
        model_data = get_huggingface_model_info(model_id)
        if model_data:
            model_data.setdefault("source_gateway", "hug")
            return model_data

        # Fall back to cached catalog (may trigger a full fetch on first call)
        huggingface_models = get_cached_models("huggingface") or get_cached_models("hug")
        if huggingface_models:
            for model in huggingface_models:
                if model.get("id", "").lower() == model_id_lower:
                    return model

        logger.warning("Model %s not found in Hugging Face catalog", sanitize_for_logging(model_id))
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Hugging Face: %s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(str(e)),
        )
        return None


def fetch_specific_model_from_fal(provider_name: str, model_name: str):
    """Fetch specific model data from Fal.ai by using cached catalog"""
    try:
        model_id = f"{provider_name}/{model_name}"
        model_id_lower = model_id.lower()

        # Fall back to cached Fal catalog
        fal_models = get_cached_models("fal")
        if fal_models:
            for model in fal_models:
                if model.get("id", "").lower() == model_id_lower:
                    return model

        logger.warning("Model %s not found in Fal.ai catalog", sanitize_for_logging(model_id))
        return None
    except Exception as e:
        logger.error(
            "Failed to fetch specific model %s/%s from Fal.ai: %s",
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
        model_id = f"{provider_name}/{model_name}"
        model_id_lower = model_id.lower()
        # Also check for simple model name without provider prefix
        simple_name = model_name.lower()

        google_models = get_cached_models("google-vertex")
        if google_models:
            for model in google_models:
                cached_id = model.get("id", "").lower()
                # Match full model_id or just the model name
                if cached_id == model_id_lower or cached_id == simple_name:
                    return model

        logger.warning(
            "Model %s not found in Google Vertex AI catalog",
            sanitize_for_logging(model_id)
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
        Gateway name: 'openrouter', 'featherless', 'deepinfra', 'chutes', 'groq', 'fireworks', 'together', 'google-vertex', 'cerebras', 'nebius', 'xai', 'novita', 'huggingface', 'fal', 'helicone', 'vercel-ai-gateway', 'aihubmix', 'anannas', 'near', 'aimo', or 'openrouter' (default)
    """
    try:
        model_id = f"{provider_name}/{model_name}".lower()

        # Check each gateway's cache
        gateways = [
            "openrouter",
            "featherless",
            "deepinfra",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "google-vertex",
            "cerebras",
            "nebius",
            "xai",
            "novita",
            "huggingface",
            "fal",
            "helicone",
            "vercel-ai-gateway",
            "aihubmix",
            "anannas",
            "near",
            "aimo",
        ]

        for gateway in gateways:
            models = get_cached_models(gateway)
            if models:
                for model in models:
                    if model.get("id", "").lower() == model_id:
                        return "huggingface" if gateway in ("hug", "huggingface") else gateway

        # Default to onerouter if not found
        return "onerouter"
    except Exception as e:
        logger.error(f"Error detecting gateway for model {provider_name}/{model_name}: {e}")
        return "onerouter"


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
        model_id = f"{provider_name}/{model_name}"
        explicit_gateway = gateway is not None

        detected_gateway = (
            gateway or detect_model_gateway(provider_name, model_name) or "openrouter"
        )
        detected_gateway = detected_gateway.lower()

        override_gateway = detect_provider_from_model_id(model_id)
        override_gateway = override_gateway.lower() if override_gateway else None

        def normalize_gateway(value: str) -> str:
            if not value:
                return None
            value = value.lower()
            if value == "hug":
                return "huggingface"
            return value

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

            if "openrouter" not in candidate_gateways:
                candidate_gateways.append("openrouter")

            if "huggingface" not in candidate_gateways:
                candidate_gateways.append("huggingface")

        fetchers = {
            "openrouter": fetch_specific_model_from_openrouter,
            "featherless": fetch_specific_model_from_featherless,
            "deepinfra": fetch_specific_model_from_deepinfra,
            "chutes": fetch_specific_model_from_chutes,
            "groq": fetch_specific_model_from_groq,
            "fireworks": fetch_specific_model_from_fireworks,
            "together": fetch_specific_model_from_together,
            "google-vertex": fetch_specific_model_from_google_vertex,
            "huggingface": fetch_specific_model_from_huggingface,
            "fal": fetch_specific_model_from_fal,
        }

        for candidate in candidate_gateways:
            if not candidate:
                continue

            fetcher = fetchers.get(candidate, fetch_specific_model_from_openrouter)
            model_data = fetcher(provider_name, model_name)
            if model_data:
                if candidate == "huggingface":
                    model_data.setdefault("source_gateway", "hug")
                return model_data

        logger.warning(
            "Model %s not found after checking gateways: %s",
            sanitize_for_logging(model_id),
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
        # Check if we have cached data for this specific model
        if hugging_face_id in _huggingface_cache["data"]:
            return _huggingface_cache["data"][hugging_face_id]

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

        # Cache the result
        _huggingface_cache["data"][hugging_face_id] = model_data
        _huggingface_cache["timestamp"] = datetime.now(timezone.utc)

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

    model_id = model.get("id", "")
    if isinstance(model_id, str) and "/" in model_id:
        provider_slug = model_id.split("/")[0].lower().lstrip("@")
        if provider_slug:
            return provider_slug

    source_gateway = model.get("source_gateway")
    if isinstance(source_gateway, str):
        provider_slug = source_gateway.lower().lstrip("@")
        if provider_slug:
            return provider_slug

    return None


def _normalize_provider_slug(provider: Any) -> str | None:
    """Extract provider slug from a provider record."""
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
        model_id = openrouter_model.get("id", "")

        # Extract provider slug from model id (e.g., "openai/gpt-4" -> "openai")
        provider_slug = None
        if "/" in model_id:
            provider_slug = model_id.split("/")[0]

        # Get provider information
        # Preserve existing provider_site_url if already set (e.g., from HuggingFace normalization)
        provider_site_url = openrouter_model.get("provider_site_url")
        if not provider_site_url and providers_data and provider_slug:
            for provider in providers_data:
                if provider.get("slug") == provider_slug:
                    provider_site_url = provider.get("site_url")
                    break

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

        # Add provider information to model
        enhanced_model = {
            **openrouter_model,
            "provider_slug": (
                provider_slug if provider_slug else openrouter_model.get("provider_slug")
            ),
            "provider_site_url": provider_site_url,
            "model_logo_url": model_logo_url,
        }

        return enhanced_model
    except Exception as e:
        logger.error(f"Error enhancing model with provider info: {e}")
        return openrouter_model


def normalize_aihubmix_model_with_pricing(model: dict) -> dict | None:
    """Normalize AiHubMix model with pricing data from their API

    AiHubMix API returns pricing in USD per 1K tokens:
    - input: cost per 1K input tokens
    - output: cost per 1K output tokens

    We convert to per-token pricing (divide by 1000) to match the format used by
    all other gateways (OpenRouter, DeepInfra, etc.) and expected by calculate_cost().

    Example: $1.25/1K tokens -> $0.00125/token (same as OpenRouter format)

    Note: AiHubMix API may return 'id' or 'model_id' depending on the endpoint version.
    """
    # Support both 'id' and 'model_id' field names for API compatibility
    model_id = model.get("id") or model.get("model_id")
    if not model_id:
        # Use debug level to avoid excessive logging during catalog refresh
        logger.debug("AiHubMix model missing both 'id' and 'model_id' fields: %s", sanitize_for_logging(str(model)))
        return None

    try:
        # Extract pricing from the API response
        # AiHubMix returns pricing per 1K tokens
        # Use pricing_normalization to convert to per-token format
        from src.services.pricing_normalization import normalize_pricing_dict, PricingFormat

        pricing_data = model.get("pricing", {})

        # Normalize pricing from per-1K to per-token format
        normalized_pricing = normalize_pricing_dict(pricing_data, PricingFormat.PER_1K_TOKENS)

        # Filter out models with zero pricing (free models can drain credits)
        if float(normalized_pricing.get("prompt", 0)) == 0 and float(normalized_pricing.get("completion", 0)) == 0:
            logger.debug(f"Filtering out AiHubMix model {model_id} with zero pricing")
            return None

        # Get model name, falling back to model_id
        model_name = model.get("name") or model_id

        # Get description from 'desc' or 'description' field
        description = model.get("description") or model.get("desc") or "Model from AiHubMix"

        # Determine input modalities from model data
        input_modalities_str = model.get("input_modalities", "")
        if input_modalities_str and "image" in input_modalities_str.lower():
            input_modalities = ["text", "image"]
        else:
            input_modalities = ["text"]

        normalized = {
            "id": model_id,
            "slug": f"aihubmix/{model_id}",
            "canonical_slug": f"aihubmix/{model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": model.get("created_at"),
            "description": description,
            "context_length": model.get("context_length") or 4096,
            "architecture": {
                "modality": MODALITY_TEXT_TO_TEXT,
                "input_modalities": input_modalities,
                "output_modalities": ["text"],
                "instruct_type": "chat",
            },
            "pricing": normalized_pricing,
            "per_request_limits": None,
            "supported_parameters": [],
            "default_parameters": {},
            "provider_slug": "aihubmix",
            "provider_site_url": "https://aihubmix.com",
            "model_logo_url": None,
            "source_gateway": "aihubmix",
            "pricing_source": "aihubmix-api",
        }
        return normalized
    except Exception as e:
        logger.error("Failed to normalize AiHubMix model: %s", sanitize_for_logging(str(e)))
        return None


def normalize_aihubmix_model(model) -> dict | None:
    """Normalize AiHubMix model to catalog schema

    AiHubMix models use OpenAI-compatible naming conventions.
    Supports both object-style (attributes) and dict-style models.
    """
    # Support both attribute and dict access, and both 'id' and 'model_id' field names
    if isinstance(model, dict):
        model_id = model.get("id") or model.get("model_id")
        raw_model_name = model.get("name") or model_id
        created_at = model.get("created_at")
        description = model.get("description") or model.get("desc") or "Model from AiHubMix"
        context_length = model.get("context_length") or 4096
    else:
        model_id = getattr(model, "id", None) or getattr(model, "model_id", None)
        raw_model_name = getattr(model, "name", model_id)
        created_at = getattr(model, "created_at", None)
        description = getattr(model, "description", None) or getattr(model, "desc", None) or "Model from AiHubMix"
        context_length = getattr(model, "context_length", 4096)

    if not model_id:
        # Use debug level to avoid excessive logging during catalog refresh
        logger.debug("AiHubMix model missing both 'id' and 'model_id' fields: %s", sanitize_for_logging(str(model)))
        return None

    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": model_id,
            "slug": f"aihubmix/{model_id}",
            "canonical_slug": f"aihubmix/{model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": created_at,
            "description": description,
            "context_length": context_length,
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
            "provider_slug": "aihubmix",
            "provider_site_url": "https://aihubmix.com",
            "model_logo_url": None,
            "source_gateway": "aihubmix",
        }
        return enrich_model_with_pricing(normalized, "aihubmix")
    except Exception as e:
        logger.error("Failed to normalize AiHubMix model: %s", sanitize_for_logging(str(e)))
        return None


def normalize_helicone_model(model) -> dict | None:
    """Normalize Helicone AI Gateway model to catalog schema

    Helicone models can originate from various providers (OpenAI, Anthropic, etc.)
    The gateway provides observability and monitoring on top of provider routing.
    Pricing is dynamically fetched from the underlying provider's pricing data.
    """
    # Extract model ID
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Helicone model missing 'id' field: %s", sanitize_for_logging(str(model)))
        return None

    # Determine provider from model ID
    # Models typically come in standard formats like "gpt-4o-mini", "claude-3-sonnet", etc.
    provider_slug = "helicone"
    raw_display_name = model_id

    # Try to detect provider from model name
    if "/" in model_id:
        provider_slug = model_id.split("/")[0]
        raw_display_name = model_id.split("/")[1]
    elif "gpt" in model_id.lower() or "o1" in model_id.lower():
        provider_slug = "openai"
    elif "claude" in model_id.lower():
        provider_slug = "anthropic"
    elif "gemini" in model_id.lower():
        provider_slug = "google"

    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get description - Helicone doesn't provide this, so we create one
    description = (
        getattr(model, "description", None) or "Model available through Helicone AI Gateway"
    )

    # Get context length if available
    context_length = getattr(model, "context_length", 4096)

    # Get created date if available
    created = getattr(model, "created_at", None)

    # Fetch pricing dynamically from Helicone or underlying provider
    pricing = get_helicone_model_pricing(model_id)

    normalized = {
        "id": model_id,
        "slug": f"helicone/{model_id}",
        "canonical_slug": f"helicone/{model_id}",
        "hugging_face_id": None,
        "name": display_name,
        "created": created,
        "description": description,
        "context_length": context_length,
        "architecture": {
            "modality": MODALITY_TEXT_TO_TEXT,
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "instruct_type": "chat",
        },
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://www.helicone.ai",
        "model_logo_url": "https://www.helicone.ai/favicon.ico",
        "source_gateway": "helicone",
    }

    return enrich_model_with_pricing(normalized, "helicone")


def get_helicone_model_pricing(model_id: str) -> dict:
    """Get pricing for a Helicone AI Gateway model

    Fetches pricing from Helicone's public API or the underlying provider.
    Falls back to default zero pricing if unavailable.

    Args:
        model_id: Model identifier (e.g., "gpt-4o-mini")

    Returns:
        dict with 'prompt', 'completion', 'request', and 'image' pricing fields
    """
    try:
        from src.services.helicone_client import fetch_helicone_pricing_from_public_api

        # Fetch pricing from Helicone's public API (no circular dependency)
        pricing_map = fetch_helicone_pricing_from_public_api()

        if pricing_map:
            # Try exact match first
            if model_id in pricing_map:
                return {
                    "prompt": str(pricing_map[model_id].get("prompt", "0")),
                    "completion": str(pricing_map[model_id].get("completion", "0")),
                    "request": str(pricing_map[model_id].get("request", "0")),
                    "image": str(pricing_map[model_id].get("image", "0")),
                }

            # Try without provider prefix
            model_name = model_id.split("/")[-1] if "/" in model_id else model_id
            if model_name in pricing_map:
                return {
                    "prompt": str(pricing_map[model_name].get("prompt", "0")),
                    "completion": str(pricing_map[model_name].get("completion", "0")),
                    "request": str(pricing_map[model_name].get("request", "0")),
                    "image": str(pricing_map[model_name].get("image", "0")),
                }

            # Try with common provider prefixes
            for prefix in ["anthropic", "openai", "google", "meta-llama"]:
                prefixed_id = f"{prefix}/{model_name}"
                if prefixed_id in pricing_map:
                    return {
                        "prompt": str(pricing_map[prefixed_id].get("prompt", "0")),
                        "completion": str(pricing_map[prefixed_id].get("completion", "0")),
                        "request": str(pricing_map[prefixed_id].get("request", "0")),
                        "image": str(pricing_map[prefixed_id].get("image", "0")),
                    }

    except Exception as e:
        logger.debug(
            "Failed to fetch Helicone pricing for %s: %s",
            sanitize_for_logging(model_id),
            sanitize_for_logging(str(e)),
        )

    # Fallback: return default zero pricing
    return {
        "prompt": "0",
        "completion": "0",
        "request": "0",
        "image": "0",
    }


def normalize_anannas_model(model) -> dict | None:
    """Normalize Anannas model to catalog schema

    Anannas models use OpenAI-compatible naming conventions.
    """
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Anannas model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    raw_model_name = getattr(model, "name", model_id)
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": model_id,
            "slug": f"anannas/{model_id}",
            "canonical_slug": f"anannas/{model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": getattr(model, "created_at", None),
            "description": getattr(model, "description", "Model from Anannas"),
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
            "provider_slug": "anannas",
            "provider_site_url": "https://api.anannas.ai",
            "model_logo_url": None,
            "source_gateway": "anannas",
        }
        return enrich_model_with_pricing(normalized, "anannas")
    except Exception as e:
        logger.error("Failed to normalize Anannas model: %s", sanitize_for_logging(str(e)))
        return None


def _is_alibaba_quota_error_cached() -> bool:
    """Check if we're in a quota error backoff period.

    Returns True if a quota error was recently recorded and we should skip
    making API calls to avoid log spam.
    """
    if not _alibaba_models_cache.get("quota_error"):
        return False

    timestamp = _alibaba_models_cache.get("quota_error_timestamp")
    if not timestamp:
        return False

    backoff = _alibaba_models_cache.get("quota_error_backoff", 900)  # Default 15 min
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    return age < backoff


def _set_alibaba_quota_error():
    """Record a quota error with backoff timing.

    Note: We intentionally do NOT set the main cache timestamp here.
    This ensures that the cache appears "stale" so that get_cached_models()
    will call fetch_models_from_alibaba(), where the quota error backoff
    check (_is_alibaba_quota_error_cached) is evaluated. If we set timestamp,
    the 1-hour cache TTL would override our 15-minute quota error backoff.
    """
    _alibaba_models_cache["quota_error"] = True
    _alibaba_models_cache["quota_error_timestamp"] = datetime.now(timezone.utc)
    _alibaba_models_cache["data"] = []
    # Don't set timestamp - let the cache appear stale so fetch_models_from_alibaba
    # is called and can check the quota_error_backoff


def _clear_alibaba_quota_error():
    """Clear quota error state after successful fetch."""
    _alibaba_models_cache["quota_error"] = False
    _alibaba_models_cache["quota_error_timestamp"] = None


def normalize_alibaba_model(model) -> dict | None:
    """Normalize Alibaba Cloud model to catalog schema

    Alibaba models use OpenAI-compatible naming conventions.
    """
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Alibaba Cloud model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    raw_model_name = getattr(model, "name", model_id)
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": model_id,
            "slug": f"alibaba/{model_id}",
            "canonical_slug": f"alibaba/{model_id}",
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
        model_id = openai_model.get("id")
        if not model_id:
            return None

        slug = f"openai/{model_id}"
        provider_slug = "openai"

        # Generate display name
        raw_display_name = model_id.replace("-", " ").replace("_", " ").title()
        # Clean up common patterns
        raw_display_name = raw_display_name.replace("Gpt ", "GPT-")
        raw_display_name = raw_display_name.replace("O1 ", "o1-")
        raw_display_name = raw_display_name.replace("O3 ", "o3-")
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)

        description = f"OpenAI {model_id} model."

        # Determine context length based on model
        # Context lengths are aligned with manual_pricing.json values
        if "gpt-3.5" in model_id:
            context_length = 16385
        elif "gpt-4-32k" in model_id:
            context_length = 32768
        elif "gpt-4o" in model_id:
            context_length = 128000
        elif model_id in ("o1", "o1-2024-12-17", "o3-mini"):
            # Latest o1 and o3-mini have 200k context
            context_length = 200000
        elif "o1" in model_id or "o3" in model_id:
            # o1-preview, o1-mini have 128k context
            context_length = 128000
        elif "gpt-4-turbo" in model_id:
            context_length = 128000
        elif "gpt-4" in model_id:
            # Base gpt-4 models have 8k context
            context_length = 8192
        else:
            # Default fallback
            context_length = 128000

        # Determine modality
        modality = MODALITY_TEXT_TO_TEXT
        input_modalities = ["text"]
        output_modalities = ["text"]
        if "vision" in model_id or "gpt-4o" in model_id or "gpt-4-turbo" in model_id:
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
            "supported_parameters": ["temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty", "stop"],
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
        model_id = anthropic_model.get("id")
        if not model_id:
            return None

        slug = f"anthropic/{model_id}"
        provider_slug = "anthropic"

        # Use display_name from API, fall back to formatted model_id
        raw_display_name = anthropic_model.get("display_name") or anthropic_model.get("name", model_id)
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
        if "3-5" in model_id or "3.5" in model_id:
            max_output = 8192
        else:
            max_output = 4096

        # All Claude 3+ models support vision
        has_vision = model_id.startswith("claude-3")

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
            "supported_parameters": ["temperature", "max_tokens", "top_p", "top_k", "stop_sequences"],
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
