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

        # Extract pricing from model_pricing relationship if available
        # (model_pricing is a LEFT JOIN, so it may be None)
        pricing_data = db_model.get("model_pricing")
        pricing_prompt = None
        pricing_completion = None

        if pricing_data:
            pricing_prompt = pricing_data.get("price_per_input_token")
            pricing_completion = pricing_data.get("price_per_output_token")

        # Build base raw model that works for most providers
        raw_model = {
            "id": provider_model_id,
            "modelId": provider_model_id,
            "model_id": provider_model_id,
            "name": db_model.get("model_name") or provider_model_id,
            "description": db_model.get("description"),
            "context_length": db_model.get("context_length"),
            "owned_by": db_model.get("top_provider") or provider_slug,
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
                        "top_provider": None,
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
            # Overall timeout of 60s ensures we don't wait indefinitely.
            try:
                for future in as_completed(futures, timeout=60):
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
                    "Overall timeout (60s) reached for parallel model fetching; "
                    "returning %d models collected so far",
                    len(all_models),
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


def get_cached_models(gateway: str = "openrouter"):
    """Get cached models or fetch from the requested gateway if cache is expired"""
    try:
        gateway = (gateway or "openrouter").lower()

        if gateway == "featherless":
            cached = _get_fresh_or_stale_cached_models(_featherless_models_cache, "featherless")
            if cached is not None:
                return cached
            result = fetch_models_from_featherless()
            _register_canonical_records("featherless", result)
            return result if result is not None else []

        if gateway == "chutes":
            cached = _get_fresh_or_stale_cached_models(_chutes_models_cache, "chutes")
            if cached is not None:
                return cached
            result = fetch_models_from_chutes()
            _register_canonical_records("chutes", result)
            return result if result is not None else []

        if gateway == "groq":
            cached = _get_fresh_or_stale_cached_models(_groq_models_cache, "groq")
            if cached is not None:
                return cached
            result = fetch_models_from_groq()
            _register_canonical_records("groq", result)
            return result if result is not None else []

        if gateway == "fireworks":
            cached = _get_fresh_or_stale_cached_models(_fireworks_models_cache, "fireworks")
            if cached is not None:
                return cached

            # Check if gateway is in error state (exponential backoff)
            if is_gateway_in_error_state("fireworks"):
                error_msg = get_gateway_error_message("fireworks")
                logger.warning(
                    "Skipping Fireworks fetch - gateway in error state: %s",
                    sanitize_for_logging(error_msg or "unknown error")
                )
                return []

            result = fetch_models_from_fireworks()
            _register_canonical_records("fireworks", result)
            return result if result is not None else []

        if gateway == "together":
            cached = _get_fresh_or_stale_cached_models(_together_models_cache, "together")
            if cached is not None:
                return cached
            result = fetch_models_from_together()
            _register_canonical_records("together", result)
            return result if result is not None else []

        if gateway == "deepinfra":
            cached = _get_fresh_or_stale_cached_models(_deepinfra_models_cache, "deepinfra")
            if cached is not None:
                return cached
            result = fetch_models_from_deepinfra()
            _register_canonical_records("deepinfra", result)
            return result if result is not None else []

        if gateway == "google-vertex":
            cached = _get_fresh_or_stale_cached_models(_google_vertex_models_cache, "google-vertex")
            if cached is not None:
                return cached
            result = fetch_models_from_google_vertex()
            _register_canonical_records("google-vertex", result)
            return result if result is not None else []

        if gateway == "cerebras":
            cached = _get_fresh_or_stale_cached_models(_cerebras_models_cache, "cerebras")
            if cached is not None:
                return cached
            result = fetch_models_from_cerebras()
            _register_canonical_records("cerebras", result)
            return result if result is not None else []

        if gateway == "nebius":
            cached = _get_fresh_or_stale_cached_models(_nebius_models_cache, "nebius")
            if cached is not None:
                return cached
            result = fetch_models_from_nebius()
            _register_canonical_records("nebius", result)
            return result if result is not None else []

        if gateway == "xai":
            cached = _get_fresh_or_stale_cached_models(_xai_models_cache, "xai")
            if cached is not None:
                return cached
            result = fetch_models_from_xai()
            _register_canonical_records("xai", result)
            return result if result is not None else []

        if gateway == "novita":
            cached = _get_fresh_or_stale_cached_models(_novita_models_cache, "novita")
            if cached is not None:
                return cached
            result = fetch_models_from_novita()
            _register_canonical_records("novita", result)
            return result if result is not None else []

        if gateway == "onerouter":
            cached = _get_fresh_or_stale_cached_models(_onerouter_models_cache, "onerouter")
            if cached is not None:
                return cached
            result = fetch_models_from_onerouter()
            _register_canonical_records("onerouter", result)
            return result if result is not None else []

        if gateway == "hug" or gateway == "huggingface":
            from src.services.huggingface_models import ESSENTIAL_MODELS

            cache = _huggingface_models_cache
            if cache["data"] and cache["timestamp"]:
                cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
                if cache_age < cache["ttl"]:
                    # Validate cache has reasonable number of models (should be 500+, not just 9)
                    cache_size = len(cache["data"])
                    if cache_size < 100:
                        logger.warning(
                            f"⚠️  Hugging Face cache is suspiciously small ({cache_size} models). This might indicate a failed fetch or incomplete data. Refetching..."
                        )
                    else:
                        cached_ids = {model.get("id", "").lower() for model in cache["data"]}
                        essential_missing = any(
                            model_id.lower() not in cached_ids for model_id in ESSENTIAL_MODELS
                        )
                        if not essential_missing:
                            logger.debug(
                                f"Using cached Hugging Face models ({cache_size} models, age: {cache_age:.0f}s)"
                            )
                            _register_canonical_records("huggingface", cache["data"])
                            return cache["data"]
                        logger.info(
                            "Hugging Face cache missing essential models; refetching catalog"
                        )

            logger.info("Fetching fresh Hugging Face models catalog...")
            result = fetch_models_from_hug()

            # Validate result
            if result:
                logger.info(f"✅ Successfully loaded {len(result)} Hugging Face models")
            else:
                logger.error(
                    "❌ Failed to fetch Hugging Face models - API may be unavailable or rate limited"
                )

            # WORKAROUND: Explicitly update cache in case of module import issues
            if result and not cache["data"]:
                logger.info("Manually updating HuggingFace cache after fetch")
                _huggingface_models_cache["data"] = result
                _huggingface_models_cache["timestamp"] = datetime.now(timezone.utc)

            _register_canonical_records("huggingface", result)
            return result

        if gateway == "aimo":
            # Try fresh cache first
            cached = _fresh_cached_models(_aimo_models_cache, "aimo")
            if cached is not None:
                return cached

            # Fetch fresh data (with resilience features)
            result = fetch_models_from_aimo()

            # If fetch succeeded, use fresh data
            if result:
                return result

            # Fetch failed - fall back to stale-while-revalidate cache if available
            stale_cached = _get_fresh_or_stale_cached_models(_aimo_models_cache, "aimo")
            if stale_cached is not None:
                logger.warning("AIMO fetch failed, returning stale cache")
                return stale_cached

            return []

        if gateway == "near":
            cached = _get_fresh_or_stale_cached_models(_near_models_cache, "near")
            if cached is not None:
                return cached
            result = fetch_models_from_near()
            _register_canonical_records("near", result)
            return result if result is not None else []

        if gateway == "fal":
            cached = _get_fresh_or_stale_cached_models(_fal_models_cache, "fal")
            if cached is not None:
                return cached
            result = fetch_models_from_fal()
            _register_canonical_records("fal", result)
            return result if result is not None else []

        if gateway == "vercel-ai-gateway":
            cached = _get_fresh_or_stale_cached_models(_vercel_ai_gateway_models_cache, "vercel-ai-gateway")
            if cached is not None:
                return cached
            result = fetch_models_from_vercel_ai_gateway()
            _register_canonical_records("vercel-ai-gateway", result)
            return result if result is not None else []

        if gateway == "helicone":
            cached = _get_fresh_or_stale_cached_models(_helicone_models_cache, "helicone")
            if cached is not None:
                return cached
            result = fetch_models_from_helicone()
            _register_canonical_records("helicone", result)
            return result if result is not None else []

        if gateway == "anannas":
            cached = _get_fresh_or_stale_cached_models(_anannas_models_cache, "anannas")
            if cached is not None:
                return cached
            result = fetch_models_from_anannas()
            _register_canonical_records("anannas", result)
            return result if result is not None else []

        if gateway == "aihubmix":
            cached = _get_fresh_or_stale_cached_models(_aihubmix_models_cache, "aihubmix")
            if cached is not None:
                return cached
            result = fetch_models_from_aihubmix()
            _register_canonical_records("aihubmix", result)
            return result if result is not None else []

        if gateway == "alibaba":
            cached = _get_fresh_or_stale_cached_models(_alibaba_models_cache, "alibaba")
            if cached is not None:
                return cached
            result = fetch_models_from_alibaba()
            _register_canonical_records("alibaba", result)
            return result if result is not None else []

        if gateway == "cloudflare-workers-ai":
            cached = _get_fresh_or_stale_cached_models(
                _cloudflare_workers_ai_models_cache, "cloudflare-workers-ai"
            )
            if cached is not None:
                return cached
            result = fetch_models_from_cloudflare_workers_ai()
            _register_canonical_records("cloudflare-workers-ai", result)
            return result if result is not None else []

        if gateway == "clarifai":
            cached = _get_fresh_or_stale_cached_models(_clarifai_models_cache, "clarifai")
            if cached is not None:
                return cached
            result = fetch_models_from_clarifai()
            _register_canonical_records("clarifai", result)
            return result if result is not None else []

        if gateway == "openai":
            from src.cache import _openai_models_cache
            cached = _get_fresh_or_stale_cached_models(_openai_models_cache, "openai")
            if cached is not None:
                return cached
            result = fetch_models_from_openai()
            _register_canonical_records("openai", result)
            return result if result is not None else []

        if gateway == "anthropic":
            from src.cache import _anthropic_models_cache
            cached = _get_fresh_or_stale_cached_models(_anthropic_models_cache, "anthropic")
            if cached is not None:
                return cached
            result = fetch_models_from_anthropic()
            _register_canonical_records("anthropic", result)
            return result if result is not None else []

        if gateway == "simplismart":
            cached = _get_fresh_or_stale_cached_models(_simplismart_models_cache, "simplismart")
            if cached is not None:
                return cached
            result = fetch_models_from_simplismart()
            _register_canonical_records("simplismart", result)
            return result if result is not None else []

        if gateway == "sybil":
            cached = _get_fresh_or_stale_cached_models(_sybil_models_cache, "sybil")
            if cached is not None:
                return cached
            result = fetch_models_from_sybil()
            _register_canonical_records("sybil", result)
            return result if result is not None else []

        if gateway == "canopywave":
            cached = _get_fresh_or_stale_cached_models(_canopywave_models_cache, "canopywave")
            if cached is not None:
                return cached
            result = fetch_models_from_canopywave()
            _register_canonical_records("canopywave", result)
            return result if result is not None else []

        if gateway == "morpheus":
            cached = _get_fresh_or_stale_cached_models(_morpheus_models_cache, "morpheus")
            if cached is not None:
                return cached
            result = fetch_models_from_morpheus()
            _register_canonical_records("morpheus", result)
            return result if result is not None else []

        if gateway == "zai":
            cached = _get_fresh_or_stale_cached_models(_zai_models_cache, "zai")
            if cached is not None:
                return cached
            result = fetch_models_from_zai()
            _register_canonical_records("zai", result)
            return result if result is not None else []

        if gateway == "all":
            cache = _multi_provider_catalog_cache
            # Check timestamp only - empty list [] is a valid cached value
            if cache.get("timestamp") is not None:
                cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
                if cache_age < cache["ttl"]:
                    return cache["data"]
                if cache_age < cache.get("stale_ttl", cache["ttl"]):
                    revalidate_cache_in_background(
                        "multi-provider-catalog", _refresh_multi_provider_catalog_cache
                    )
                    return cache["data"]

            return _refresh_multi_provider_catalog_cache()

        # Default to OpenRouter with stale-while-revalidate
        if is_cache_fresh(_models_cache):
            data = _models_cache["data"]
            _register_canonical_records("openrouter", data)
            return data

        # Check if we can serve stale cache while revalidating
        if should_revalidate_in_background(_models_cache):
            logger.info("Serving stale OpenRouter cache while revalidating in background")
            cached = _models_cache["data"]
            if cached:
                _register_canonical_records("openrouter", cached)
            revalidate_cache_in_background("openrouter", fetch_models_from_openrouter)
            return cached

        # Cache expired or empty, fetch fresh data synchronously
        result = fetch_models_from_openrouter()
        _register_canonical_records("openrouter", result)
        return result if result is not None else []
    except Exception as e:
        logger.error(
            "Error getting cached models for gateway '%s': %s",
            sanitize_for_logging(gateway),
            sanitize_for_logging(str(e)),
        )
        return []


def fetch_models_from_openrouter():
    """Fetch models from OpenRouter API"""
    try:
        if not Config.OPENROUTER_API_KEY:
            logger.error("OpenRouter API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get("https://openrouter.ai/api/v1/models", headers=headers)
        response.raise_for_status()

        try:
            models_data = response.json()
        except json.JSONDecodeError as json_err:
            logger.error(
                f"Failed to parse JSON response from OpenRouter models API: {json_err}. "
                f"Response status: {response.status_code}, Content-Type: {response.headers.get('content-type')}"
            )
            return []

        raw_models = models_data.get("data", [])

        # Process and filter models
        filtered_models = []
        for model in raw_models:
            model.setdefault("source_gateway", "openrouter")
            # Sanitize pricing - returns None for models with dynamic pricing
            if "pricing" in model:
                sanitized_pricing = sanitize_pricing(model["pricing"])
                if sanitized_pricing is None:
                    # Filter out models with dynamic/indeterminate pricing
                    logger.debug(
                        "Filtering out model %s with dynamic pricing",
                        sanitize_for_logging(model.get("id", "unknown")),
                    )
                    continue
                model["pricing"] = sanitized_pricing

            # Mark OpenRouter free models (those with :free suffix)
            # Only OpenRouter has legitimately free models
            # Use `or ""` to handle both missing keys and null values
            model_id = model.get("id") or ""
            model["is_free"] = model_id.endswith(":free")

            filtered_models.append(model)

        logger.info(
            f"Filtered {len(raw_models) - len(filtered_models)} models with dynamic pricing"
        )
        _models_cache["data"] = filtered_models
        _models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("openrouter")

        return _models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("OpenRouter HTTP error: %s", error_msg)
        set_gateway_error("openrouter", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from OpenRouter: %s", error_msg)
        set_gateway_error("openrouter", error_msg)
        return None


def fetch_models_from_deepinfra():
    """Fetch models from DeepInfra API and normalize to the catalog schema"""
    try:
        if not Config.DEEPINFRA_API_KEY:
            logger.error(
                "DeepInfra API key not configured - please set DEEPINFRA_API_KEY environment variable"
            )
            return None

        # Log that we're attempting to fetch
        api_key_preview = (
            Config.DEEPINFRA_API_KEY[:5] + "***" if Config.DEEPINFRA_API_KEY else "NONE"
        )
        logger.info(f"DeepInfra API key found (preview: {api_key_preview})")

        headers = {
            "Authorization": f"Bearer {Config.DEEPINFRA_API_KEY}",
            "Content-Type": "application/json",
        }

        # DeepInfra API - use /models/list endpoint which has better model data
        url = "https://api.deepinfra.com/models/list"
        logger.info("Fetching DeepInfra models from %s", sanitize_for_logging(str(url)))

        response = httpx.get(url, headers=headers, timeout=20.0)

        logger.info(f"DeepInfra API response status: {response.status_code}")
        response.raise_for_status()

        payload = response.json()

        # DeepInfra /models/list returns array directly, not wrapped in an object
        if isinstance(payload, list):
            raw_models = payload
        else:
            raw_models = payload.get("data", [])

        logger.info(f"Fetched {len(raw_models)} models from DeepInfra")

        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_deepinfra_model(model)]
            if norm_model is not None
        ]

        _deepinfra_models_cache["data"] = normalized_models
        _deepinfra_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("deepinfra")

        logger.info(f"Successfully cached {len(normalized_models)} DeepInfra models")
        return _deepinfra_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("DeepInfra HTTP error: %s", error_msg)
        set_gateway_error("deepinfra", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from DeepInfra: %s", error_msg, exc_info=True)
        set_gateway_error("deepinfra", error_msg)
        return None


def fetch_models_from_featherless():
    """Fetch models from Featherless API and normalize to the catalog schema

    Note: Featherless API ignores the 'limit' and 'offset' parameters and returns
    ALL models (~6,452) in a single request. We only need one API call.
    """
    try:
        if not Config.FEATHERLESS_API_KEY:
            logger.error("Featherless API key not configured")
            return None

        headers = {"Authorization": f"Bearer {Config.FEATHERLESS_API_KEY}"}

        # Featherless API returns all models in a single request (ignores pagination params)
        url = "https://api.featherless.ai/v1/models"

        logger.info("Fetching all models from Featherless API (single request)")

        response = httpx.get(url, headers=headers, params={"limit": 10000}, timeout=30.0)
        response.raise_for_status()

        payload = response.json()
        all_models = payload.get("data", [])

        if not all_models:
            logger.warning("No models returned from Featherless API")
            return None

        logger.info(f"Fetched {len(all_models)} total models from Featherless")

        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in all_models
            if model
            for norm_model in [normalize_featherless_model(model)]
            if norm_model is not None
        ]

        if len(normalized_models) < 6000:
            logger.warning(
                f"Featherless API returned {len(normalized_models)} models; loading extended catalog export for completeness"
            )
            export_models = load_featherless_catalog_export()
            if export_models:
                # Filter models that have a valid id (normalize functions may return models without id)
                combined = {model["id"]: model for model in normalized_models if model.get("id")}
                for export_model in export_models:
                    # Run export models through pricing enrichment to filter those without valid pricing.
                    # Note: During catalog build (_is_building_catalog=True), models are kept even without
                    # pricing to bootstrap the catalog. During regular operation, only models with valid
                    # pricing (from manual_pricing.json or cross-reference) are kept. This intentionally
                    # filters out export models without pricing to prevent them appearing as "free".
                    enriched = enrich_model_with_pricing(export_model, "featherless")
                    if enriched and enriched.get("id"):
                        combined[enriched["id"]] = enriched
                normalized_models = list(combined.values())
                logger.info(
                    f"Combined Featherless catalog now includes {len(normalized_models)} models from API + export"
                )

        _featherless_models_cache["data"] = normalized_models
        _featherless_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("featherless")

        logger.info(f"Normalized and cached {len(normalized_models)} Featherless models")
        return _featherless_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Featherless HTTP error: %s", error_msg)
        set_gateway_error("featherless", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Featherless: %s", error_msg)
        set_gateway_error("featherless", error_msg)
        return None


def normalize_featherless_model(featherless_model: dict) -> dict:
    """Normalize Featherless catalog entries to resemble OpenRouter model shape"""
    model_id = featherless_model.get("id", "")
    if not model_id:
        return {"source_gateway": "featherless", "raw_featherless": featherless_model or {}}

    # Extract provider slug (everything before the last slash)
    provider_slug = model_id.split("/")[0] if "/" in model_id else "featherless"

    # Model handle is the full ID
    display_name = model_id.replace("-", " ").replace("_", " ").title()

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
        "top_provider": None,
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


def fetch_models_from_chutes():
    """Fetch models from Chutes static catalog or API"""
    try:
        # First, try to load from static catalog file
        catalog_path = Path(__file__).parent.parent / "data" / "chutes_catalog.json"

        if catalog_path.exists():
            logger.info(f"Loading Chutes models from static catalog: {catalog_path}")
            with open(catalog_path) as f:
                raw_models = json.load(f)

            normalized_models = [normalize_chutes_model(model) for model in raw_models if model]

            _chutes_models_cache["data"] = normalized_models
            _chutes_models_cache["timestamp"] = datetime.now(timezone.utc)

            logger.info(f"Loaded {len(normalized_models)} models from Chutes static catalog")
            return _chutes_models_cache["data"]

        # If static catalog doesn't exist, try API (if key is configured)
        if Config.CHUTES_API_KEY:
            logger.info("Attempting to fetch Chutes models from API")
            return fetch_models_from_chutes_api()

        logger.warning("Chutes catalog file not found and no API key configured")
        return None

    except Exception as e:
        logger.error("Failed to fetch models from Chutes: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_chutes_api():
    """Fetch models from Chutes API (if available)"""
    try:
        if not Config.CHUTES_API_KEY:
            logger.error("Chutes API key not configured")
            return None

        # This is a placeholder for future API integration
        # For now, we're using the static catalog
        logger.warning("Chutes API integration not yet implemented, using static catalog")
        return None

    except Exception as e:
        logger.error("Failed to fetch models from Chutes API: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_groq():
    """Fetch models from Groq API and normalize to the catalog schema"""
    try:
        if not Config.GROQ_API_KEY:
            logger.error("Groq API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.groq.com/openai/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])
        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_groq_model(model)]
            if norm_model is not None
        ]

        _groq_models_cache["data"] = normalized_models
        _groq_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("groq")

        logger.info(f"Fetched {len(normalized_models)} Groq models")
        return _groq_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Groq HTTP error: %s", error_msg)
        set_gateway_error("groq", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Groq: %s", error_msg)
        set_gateway_error("groq", error_msg)
        return None


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

    display_name = chutes_model.get("name", model_id.replace("-", " ").replace("_", " ").title())

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
        "top_provider": None,
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

    display_name = (
        groq_model.get("display_name") or model_id.replace("-", " ").replace("_", " ").title()
    )
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
        "top_provider": None,
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


def fetch_models_from_zai():
    """Fetch models from Z.AI API and normalize to the catalog schema.

    Z.AI (Zhipu AI) provides the GLM model family with OpenAI-compatible API.
    """
    try:
        if not Config.ZAI_API_KEY:
            logger.error("Z.AI API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.ZAI_API_KEY}",
            "Content-Type": "application/json",
        }

        # Z.AI uses OpenAI-compatible /models endpoint
        url = "https://api.z.ai/api/paas/v4/models"
        logger.info("Fetching models from Z.AI API")

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        logger.info(f"Fetched {len(raw_models)} models from Z.AI")

        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_zai_model(model)]
            if norm_model is not None
        ]

        _zai_models_cache["data"] = normalized_models
        _zai_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("zai")

        logger.info(f"Successfully cached {len(normalized_models)} Z.AI models")
        return _zai_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Z.AI HTTP error: %s", error_msg)
        set_gateway_error("zai", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Z.AI: %s", error_msg)
        set_gateway_error("zai", error_msg)
        return None


def normalize_zai_model(zai_model: dict) -> dict | None:
    """Normalize Z.AI catalog entries to resemble OpenRouter model shape.

    Z.AI provides GLM models (GLM-4.7, GLM-4.5-Air, etc.) with OpenAI-compatible format.
    """
    model_id = zai_model.get("id")
    if not model_id:
        return {"source_gateway": "zai", "raw_zai": zai_model or {}}

    slug = f"zai/{model_id}"
    provider_slug = "zai"

    display_name = (
        zai_model.get("display_name")
        or zai_model.get("name")
        or model_id.replace("-", " ").replace("_", " ").title()
    )
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
        "top_provider": None,
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


def fetch_models_from_fireworks():
    """Fetch models from Fireworks API and normalize to the catalog schema"""
    try:
        if not Config.FIREWORKS_API_KEY:
            logger.error("Fireworks API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.FIREWORKS_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.fireworks.ai/inference/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])
        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_fireworks_model(model)]
            if norm_model is not None
        ]

        _fireworks_models_cache["data"] = normalized_models
        _fireworks_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("fireworks")

        logger.info(f"Fetched {len(normalized_models)} Fireworks models")
        return _fireworks_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Fireworks HTTP error: %s", error_msg)

        # Cache error state to prevent continuous retries
        set_gateway_error("fireworks", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Fireworks: %s", error_msg)

        # Cache error state to prevent continuous retries
        set_gateway_error("fireworks", error_msg)
        return None


def normalize_fireworks_model(fireworks_model: dict) -> dict:
    """Normalize Fireworks catalog entries to resemble OpenRouter model shape"""
    model_id = fireworks_model.get("id")
    if not model_id:
        return {"source_gateway": "fireworks", "raw_fireworks": fireworks_model or {}}

    # Fireworks uses format like "accounts/fireworks/models/deepseek-v3p1"
    # We'll keep the full ID as-is
    slug = model_id
    provider_slug = "fireworks"

    display_name = (
        fireworks_model.get("display_name")
        or model_id.split("/")[-1].replace("-", " ").replace("_", " ").title()
    )
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
        "top_provider": None,
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


def fetch_models_from_together():
    """Fetch models from Together.ai API and normalize to the catalog schema"""
    try:
        if not Config.TOGETHER_API_KEY:
            logger.error("Together API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.TOGETHER_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.together.xyz/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        # Together API returns a list directly, not wrapped in {"data": [...]}
        raw_models = payload if isinstance(payload, list) else payload.get("data", [])
        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_together_model(model)]
            if norm_model is not None
        ]

        _together_models_cache["data"] = normalized_models
        _together_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("together")

        logger.info(f"Fetched {len(normalized_models)} Together models")
        return _together_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Together HTTP error: %s", error_msg)
        set_gateway_error("together", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Together: %s", error_msg)
        set_gateway_error("together", error_msg)
        return None


def normalize_together_model(together_model: dict) -> dict:
    """Normalize Together catalog entries to resemble OpenRouter model shape"""
    model_id = together_model.get("id")
    if not model_id:
        return {"source_gateway": "together", "raw_together": together_model or {}}

    slug = model_id
    provider_slug = "together"

    display_name = (
        together_model.get("display_name")
        or model_id.replace("/", " / ").replace("-", " ").replace("_", " ").title()
    )
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
        "top_provider": None,
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


def fetch_models_from_aimo():
    """Fetch models from AIMO Network API with resilience features

    Hardening measures:
    - Configurable, shorter timeouts (5s fetch, 3s connect) to prevent thread pool blocking
    - Retry logic with exponential backoff across multiple base URLs
    - Optional HTTP fallback for HTTPS failures
    - Stale-while-revalidate caching to serve cached data on failure
    - Non-blocking: failures don't block the thread pool during parallel catalog builds
    - Circuit breaker: skip fetch attempts when gateway is in error state

    Note: AIMO is a decentralized AI marketplace with OpenAI-compatible API.
    Models are fetched from the marketplace endpoint if available.
    """
    if not Config.AIMO_API_KEY:
        logger.debug("AIMO API key not configured, using cached data if available")
        return _aimo_models_cache.get("data", [])

    # Circuit breaker: skip fetch if gateway is in error state (exponential backoff)
    if is_gateway_in_error_state("aimo"):
        cached_data = _aimo_models_cache.get("data", [])
        if cached_data:
            logger.debug("AIMO gateway in error state, returning cached data (%d models)", len(cached_data))
            return cached_data
        logger.debug("AIMO gateway in error state, no cached data available")
        return []

    headers = {
        "Authorization": f"Bearer {Config.AIMO_API_KEY}",
        "Content-Type": "application/json",
    }

    # Build list of URLs to try, starting with HTTPS (primary and fallback)
    urls_to_try = [f"{base_url}/models" for base_url in Config.AIMO_BASE_URLS]

    # Add HTTP fallback URLs if enabled
    if Config.AIMO_ENABLE_HTTP_FALLBACK:
        http_urls = [
            url.replace("https://", "http://") for url in urls_to_try if url.startswith("https://")
        ]
        urls_to_try.extend(http_urls)

    # Create timeout with separate connect and read timeouts
    timeout_config = httpx.Timeout(
        timeout=Config.AIMO_FETCH_TIMEOUT,
        connect=Config.AIMO_CONNECT_TIMEOUT,
    )

    last_error = None
    for attempt in range(Config.AIMO_MAX_RETRIES + 1):
        for url_idx, url in enumerate(urls_to_try):
            try:
                logger.debug(
                    f"AIMO fetch attempt {attempt + 1}/{Config.AIMO_MAX_RETRIES + 1}, "
                    f"URL {url_idx + 1}/{len(urls_to_try)}: {url}"
                )

                response = httpx.get(
                    url, headers=headers, timeout=timeout_config, follow_redirects=True
                )
                response.raise_for_status()

                payload = response.json()
                raw_models = payload.get("data", [])

                if not raw_models:
                    logger.warning("No models returned from AIMO API at %s", url)
                    continue

                # Normalize models and filter out None values (models without providers)
                normalized_models = [
                    normalized
                    for model in raw_models
                    if model and (normalized := normalize_aimo_model(model)) is not None
                ]

                # Deduplicate models by canonical_slug (same model from different AIMO providers)
                # Keep only the first occurrence of each unique model
                seen_models = {}
                deduplicated_models = []
                for model in normalized_models:
                    canonical_slug = model.get("canonical_slug")
                    if canonical_slug and canonical_slug not in seen_models:
                        seen_models[canonical_slug] = True
                        deduplicated_models.append(model)
                    elif not canonical_slug:
                        # If no canonical slug, keep it (shouldn't happen but be safe)
                        deduplicated_models.append(model)

                logger.info(
                    f"Fetched {len(normalized_models)} AIMO models from {url}, "
                    f"deduplicated to {len(deduplicated_models)} unique models"
                )

                _aimo_models_cache["data"] = deduplicated_models
                _aimo_models_cache["timestamp"] = datetime.now(timezone.utc)

                # Clear error state on successful fetch
                clear_gateway_error("aimo")

                return _aimo_models_cache["data"]

            except httpx.TimeoutException:
                last_error = f"Timeout at {url} after {Config.AIMO_FETCH_TIMEOUT}s"
                # Use debug for intermediate retries, warning only on final attempt
                if attempt == Config.AIMO_MAX_RETRIES:
                    logger.warning("AIMO timeout: %s (final attempt)", last_error)
                else:
                    logger.debug("AIMO timeout: %s (attempt %d)", last_error, attempt + 1)
                continue

            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code} at {url}"
                # Use debug for intermediate retries, warning only on final attempt
                if attempt == Config.AIMO_MAX_RETRIES:
                    logger.warning(
                        "AIMO HTTP error: %s - %s (final attempt)",
                        last_error,
                        sanitize_for_logging(e.response.text[:200]),
                    )
                else:
                    logger.debug(
                        "AIMO HTTP error: %s - %s (attempt %d)",
                        last_error,
                        sanitize_for_logging(e.response.text[:200]),
                        attempt + 1,
                    )
                continue

            except Exception as e:
                last_error = f"Error at {url}: {sanitize_for_logging(str(e))}"
                # Use debug for intermediate retries, warning only on final attempt
                if attempt == Config.AIMO_MAX_RETRIES:
                    logger.warning("AIMO fetch error: %s (final attempt)", last_error)
                else:
                    logger.debug("AIMO fetch error: %s (attempt %d)", last_error, attempt + 1)
                continue

    # All retries exhausted - use stale cache if available (stale-while-revalidate)
    if _aimo_models_cache.get("data"):
        logger.warning(
            "AIMO fetch failed after all retries, returning stale cached data. Last error: %s",
            last_error,
        )
        set_gateway_error("aimo", f"Using stale cache: {last_error}")
        return _aimo_models_cache["data"]

    # No cached data available
    error_msg = last_error or "Unknown error during AIMO fetch"
    logger.error("Failed to fetch models from AIMO after all retries: %s", error_msg)
    set_gateway_error("aimo", error_msg)
    return []


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

    display_name = aimo_model.get("display_name") or model_name_normalized.replace("-", " ").title()
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
        "top_provider": None,
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


def fetch_models_from_near():
    """Fetch models from Near AI API

    Note: Near AI is a decentralized AI infrastructure providing private, verifiable, and user-owned AI services.
    Models are fetched from the OpenAI-compatible /models endpoint.
    If the API doesn't return models, fallback to known Near AI models.
    """
    try:
        if not Config.NEAR_API_KEY:
            logger.error("Near AI API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.NEAR_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            # Try to fetch models from Near AI
            # Note: Using Near AI's model list endpoint which includes pricing
            response = httpx.get(
                "https://cloud-api.near.ai/v1/model/list",
                headers=headers,
                timeout=20.0,
            )
            response.raise_for_status()

            payload = response.json()
            raw_models = payload.get("models", [])

            if raw_models:
                # Normalize models
                normalized_models = [normalize_near_model(model) for model in raw_models if model]

                _near_models_cache["data"] = normalized_models
                _near_models_cache["timestamp"] = datetime.now(timezone.utc)

                logger.info(f"Fetched {len(normalized_models)} Near AI models from API")
                return _near_models_cache["data"]
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(
                "Near AI API request failed: %s. Using fallback model list.",
                sanitize_for_logging(str(e)),
            )

        # Fallback strategy:
        # 1. First, try to get models from database (most recent successful sync)
        # 2. If database is empty, use hardcoded static fallback as last resort
        logger.info("Using fallback Near AI model list")

        # Try database fallback first (dynamic, from last successful sync)
        db_fallback_models = get_fallback_models_from_db("near")
        if db_fallback_models:
            normalized_models = [normalize_near_model(model) for model in db_fallback_models if model]
            normalized_models = [m for m in normalized_models if m]  # Filter out None

            if normalized_models:
                _near_models_cache["data"] = normalized_models
                _near_models_cache["timestamp"] = datetime.now(timezone.utc)
                logger.info(f"Using {len(normalized_models)} Near AI models from database fallback")
                return _near_models_cache["data"]

        # Static fallback as last resort (if database is empty)
        # Reference: https://cloud.near.ai/models for current available models
        # Pricing from https://cloud-api.near.ai/v1/model/list (as of 2026-01)
        logger.warning("Database fallback empty, using static fallback for Near AI")
        fallback_models = [
            {
                "id": "deepseek-ai/DeepSeek-V3.1",
                "modelId": "deepseek-ai/DeepSeek-V3.1",
                "owned_by": "DeepSeek",
                "inputCostPerToken": {"amount": 1.05, "scale": -6},  # $1.05 per million tokens
                "outputCostPerToken": {"amount": 3.10, "scale": -6},  # $3.10 per million tokens
                "metadata": {"contextLength": 128000},
            },
            {
                "id": "openai/gpt-oss-120b",
                "modelId": "openai/gpt-oss-120b",
                "owned_by": "OpenAI",
                "inputCostPerToken": {"amount": 0.15, "scale": -6},  # $0.15 per million tokens
                "outputCostPerToken": {"amount": 0.55, "scale": -6},  # $0.55 per million tokens
                "metadata": {"contextLength": 131000},
            },
            {
                "id": "Qwen/Qwen3-30B-A3B-Instruct-2507",
                "modelId": "Qwen/Qwen3-30B-A3B-Instruct-2507",
                "owned_by": "Qwen",
                "inputCostPerToken": {"amount": 0.15, "scale": -6},  # $0.15 per million tokens
                "outputCostPerToken": {"amount": 0.55, "scale": -6},  # $0.55 per million tokens
                "metadata": {"contextLength": 262144},
            },
            {
                "id": "zai-org/GLM-4.6",
                "modelId": "zai-org/GLM-4.6",
                "owned_by": "Zhipu AI",
                "inputCostPerToken": {"amount": 0.85, "scale": -6},  # $0.85 per million tokens
                "outputCostPerToken": {"amount": 3.30, "scale": -6},  # $3.30 per million tokens
                "metadata": {"contextLength": 200000},
            },
            {
                "id": "zai-org/GLM-4.7",
                "modelId": "zai-org/GLM-4.7",
                "owned_by": "Zhipu AI",
                "inputCostPerToken": {"amount": 0.85, "scale": -6},  # $0.85 per million tokens
                "outputCostPerToken": {"amount": 3.30, "scale": -6},  # $3.30 per million tokens
                "metadata": {"contextLength": 131072},
            },
        ]

        normalized_models = [normalize_near_model(model) for model in fallback_models if model]

        _near_models_cache["data"] = normalized_models
        _near_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Using {len(normalized_models)} static fallback Near AI models")
        return _near_models_cache["data"]
    except Exception as e:
        logger.error(f"Failed to fetch models from Near AI: {e}")
        return []


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
    display_name = (
        metadata.get("displayName")
        or near_model.get("display_name")
        or model_id.replace("-", " ").replace("_", " ").title()
    )
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
        "top_provider": None,
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


def fetch_models_from_fal():
    """Fetch models from Fal.ai catalog

    Loads models from the static Fal.ai catalog JSON file which contains
    curated models from the 839+ available on fal.ai
    """
    try:
        from src.services.fal_image_client import get_fal_models

        # Get models from catalog
        raw_models = get_fal_models()

        if not raw_models:
            logger.warning("No Fal.ai models found in catalog")
            return []

        # Normalize models
        normalized_models = [normalize_fal_model(model) for model in raw_models if model]

        _fal_models_cache["data"] = normalized_models
        _fal_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} Fal.ai models from catalog")
        return _fal_models_cache["data"]
    except Exception as e:
        logger.error(f"Failed to fetch models from Fal.ai catalog: {e}")
        return []


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
    display_name = fal_model.get("title") or fal_model.get("name") or model_id.split("/")[-1]

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
        "top_provider": None,
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


def fetch_models_from_vercel_ai_gateway():
    """Fetch models from Vercel AI Gateway via OpenAI-compatible API

    Vercel AI Gateway provides access to models from multiple providers
    through a unified OpenAI-compatible endpoint.
    """
    try:
        # Check if API key is configured
        if not Config.VERCEL_AI_GATEWAY_API_KEY:
            logger.warning("Vercel AI Gateway API key not configured - skipping model fetch")
            return []

        from src.services.vercel_ai_gateway_client import get_vercel_ai_gateway_client

        client = get_vercel_ai_gateway_client()
        response = client.models.list()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Vercel AI Gateway")
            return []

        # Normalize models and filter out None (models without pricing)
        normalized_models = [
            m for m in (normalize_vercel_model(model) for model in response.data if model) if m
        ]

        _vercel_ai_gateway_models_cache["data"] = normalized_models
        _vercel_ai_gateway_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} models from Vercel AI Gateway")
        return _vercel_ai_gateway_models_cache["data"]
    except Exception as e:
        logger.error(
            "Failed to fetch models from Vercel AI Gateway: %s", sanitize_for_logging(str(e))
        )
        return []


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
        display_name = model_id.split("/")[1]
    else:
        provider_slug = "vercel"
        display_name = model_id

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
        "top_provider": None,
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
    display_name = model_id.replace("-", " ").replace("_", " ").title()

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
        "top_provider": None,
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


def fetch_models_from_aihubmix():
    """Fetch models from AiHubMix via their public API

    AiHubMix provides access to models through a unified OpenAI-compatible endpoint.
    The API at https://aihubmix.com/api/v1/models includes pricing information.
    """
    try:
        import requests

        # Fetch from AiHubMix public API which includes pricing
        response = requests.get(
            "https://aihubmix.com/api/v1/models",
            timeout=30,
            headers={"Accept": "application/json"},
        )

        if response.status_code != 200:
            error_msg = f"AiHubMix API returned status {response.status_code}"
            logger.warning(error_msg)
            set_gateway_error("aihubmix", error_msg)
            return []

        data = response.json()
        models_data = data.get("data", [])

        if not models_data:
            logger.warning("No models returned from AiHubMix")
            return []

        # Normalize models and filter out None (models without valid pricing)
        normalized_models = [
            m
            for m in (normalize_aihubmix_model_with_pricing(model) for model in models_data if model)
            if m
        ]

        _aihubmix_models_cache["data"] = normalized_models
        _aihubmix_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on success
        clear_gateway_error("aihubmix")

        logger.info(f"Fetched {len(normalized_models)} models from AiHubMix")
        return _aihubmix_models_cache["data"]
    except requests.exceptions.Timeout as e:
        error_msg = f"AiHubMix API timeout: {sanitize_for_logging(str(e))}"
        logger.error(error_msg)
        set_gateway_error("aihubmix", error_msg)
        return []
    except Exception as e:
        error_msg = f"Failed to fetch models from AiHubMix: {sanitize_for_logging(str(e))}"
        logger.error(error_msg)
        set_gateway_error("aihubmix", error_msg)
        return []


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
            "top_provider": None,
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
        model_name = model.get("name") or model_id
        created_at = model.get("created_at")
        description = model.get("description") or model.get("desc") or "Model from AiHubMix"
        context_length = model.get("context_length") or 4096
    else:
        model_id = getattr(model, "id", None) or getattr(model, "model_id", None)
        model_name = getattr(model, "name", model_id)
        created_at = getattr(model, "created_at", None)
        description = getattr(model, "description", None) or getattr(model, "desc", None) or "Model from AiHubMix"
        context_length = getattr(model, "context_length", 4096)

    if not model_id:
        # Use debug level to avoid excessive logging during catalog refresh
        logger.debug("AiHubMix model missing both 'id' and 'model_id' fields: %s", sanitize_for_logging(str(model)))
        return None

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
            "top_provider": None,
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


def fetch_models_from_helicone():
    """Fetch models from Helicone AI Gateway via OpenAI-compatible API

    Helicone AI Gateway provides access to models from multiple providers
    through a unified OpenAI-compatible endpoint with observability features.
    """
    try:
        # Check if API key is configured
        if not Config.HELICONE_API_KEY:
            logger.warning("Helicone API key not configured - skipping model fetch")
            return []

        from src.services.helicone_client import get_helicone_client

        client = get_helicone_client()
        response = client.models.list()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Helicone AI Gateway")
            return []

        # Normalize models and filter out None (models without pricing)
        normalized_models = [
            m for m in (normalize_helicone_model(model) for model in response.data if model) if m
        ]

        _helicone_models_cache["data"] = normalized_models
        _helicone_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} models from Helicone AI Gateway")
        return _helicone_models_cache["data"]
    except Exception as e:
        logger.error(
            "Failed to fetch models from Helicone AI Gateway: %s", sanitize_for_logging(str(e))
        )
        return []


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
    display_name = model_id

    # Try to detect provider from model name
    if "/" in model_id:
        provider_slug = model_id.split("/")[0]
        display_name = model_id.split("/")[1]
    elif "gpt" in model_id.lower() or "o1" in model_id.lower():
        provider_slug = "openai"
    elif "claude" in model_id.lower():
        provider_slug = "anthropic"
    elif "gemini" in model_id.lower():
        provider_slug = "google"

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
        "top_provider": None,
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


def fetch_models_from_anannas():
    """Fetch models from Anannas via OpenAI-compatible API

    Anannas provides access to various models through a unified OpenAI-compatible endpoint.
    """
    try:
        # Check if API key is configured
        if not Config.ANANNAS_API_KEY:
            logger.warning("Anannas API key not configured - skipping model fetch")
            return []

        from src.services.anannas_client import get_anannas_client

        client = get_anannas_client()
        response = client.models.list()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Anannas")
            return []

        # Normalize models and filter out None (models without pricing)
        normalized_models = [
            m for m in (normalize_anannas_model(model) for model in response.data if model) if m
        ]

        _anannas_models_cache["data"] = normalized_models
        _anannas_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} models from Anannas")
        return _anannas_models_cache["data"]
    except Exception as e:
        logger.error("Failed to fetch models from Anannas: %s", sanitize_for_logging(str(e)))
        return []


def normalize_anannas_model(model) -> dict | None:
    """Normalize Anannas model to catalog schema

    Anannas models use OpenAI-compatible naming conventions.
    """
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Anannas model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    try:
        normalized = {
            "id": model_id,
            "slug": f"anannas/{model_id}",
            "canonical_slug": f"anannas/{model_id}",
            "hugging_face_id": None,
            "name": getattr(model, "name", model_id),
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
            "top_provider": None,
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


def fetch_models_from_alibaba():
    """Fetch models from Alibaba Cloud (DashScope) via OpenAI-compatible API

    Alibaba Cloud provides access to Qwen models through a unified OpenAI-compatible endpoint.
    """
    try:
        # Check if API key is configured
        if not (
            Config.ALIBABA_CLOUD_API_KEY
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None)
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None)
        ):
            logger.debug("Alibaba Cloud API key not configured - skipping model fetch")
            # Cache empty result to avoid repeated warnings
            _alibaba_models_cache["data"] = []
            _alibaba_models_cache["timestamp"] = datetime.now(timezone.utc)
            return []

        # Check if we're in quota error backoff period
        if _is_alibaba_quota_error_cached():
            logger.debug(
                "Alibaba Cloud quota error in backoff period - skipping API call. "
                "Will retry after backoff expires."
            )
            return _alibaba_models_cache.get("data", [])

        from src.services.alibaba_cloud_client import QuotaExceededError, list_alibaba_models

        response = list_alibaba_models()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Alibaba Cloud")
            return []

        # Normalize models (filter out None values from normalization failures)
        normalized_models = [
            m for m in (normalize_alibaba_model(model) for model in response.data if model) if m
        ]

        _alibaba_models_cache["data"] = normalized_models
        _alibaba_models_cache["timestamp"] = datetime.now(timezone.utc)
        # Clear any previous quota error state on success
        _clear_alibaba_quota_error()

        logger.info(f"Fetched {len(normalized_models)} models from Alibaba Cloud")
        return _alibaba_models_cache["data"]
    except QuotaExceededError:
        # Quota exceeded - cache the failure state to prevent repeated API calls
        _set_alibaba_quota_error()
        logger.warning(
            "Alibaba Cloud quota exceeded. Caching empty result for %d seconds. "
            "Please check your plan and billing details.",
            _alibaba_models_cache.get("quota_error_backoff", 900),
        )
        return []
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        # Check if it's a 401 authentication error
        if "401" in error_msg or "Incorrect API key" in error_msg or "invalid_api_key" in error_msg:
            # Get the actual region being used
            region = getattr(Config, 'ALIBABA_CLOUD_REGION', 'international').lower()
            if region == 'china':
                current_endpoint = "dashscope.aliyuncs.com (China/Beijing)"
                suggestion = (
                    "If your key only works for the International endpoint, set "
                    "ALIBABA_CLOUD_REGION='international' or provide "
                    "ALIBABA_CLOUD_API_KEY_INTERNATIONAL."
                )
            else:
                current_endpoint = "dashscope-intl.aliyuncs.com (International/Singapore)"
                suggestion = (
                    "If your key only works for the China endpoint, set "
                    "ALIBABA_CLOUD_REGION='china' or provide ALIBABA_CLOUD_API_KEY_CHINA."
                )

            logger.error(
                "Alibaba Cloud authentication failed (401): %s. "
                "Action required: Verify API key is valid and matches endpoint region. "
                "Currently using: %s. %s",
                error_msg,
                current_endpoint,
                suggestion
            )
        elif (
            Config.ALIBABA_CLOUD_API_KEY
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None)
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None)
        ):
            logger.error("Failed to fetch models from Alibaba Cloud: %s", error_msg)
        else:
            logger.debug("Alibaba Cloud not available (no API key configured)")
        return []


def normalize_alibaba_model(model) -> dict | None:
    """Normalize Alibaba Cloud model to catalog schema

    Alibaba models use OpenAI-compatible naming conventions.
    """
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Alibaba Cloud model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    try:
        normalized = {
            "id": model_id,
            "slug": f"alibaba/{model_id}",
            "canonical_slug": f"alibaba/{model_id}",
            "hugging_face_id": None,
            "name": getattr(model, "name", model_id),
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
            "top_provider": None,
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


def fetch_models_from_openai():
    """Fetch models from OpenAI API and normalize to the catalog schema"""
    try:
        if not Config.OPENAI_API_KEY:
            logger.debug("OpenAI API key not configured - skipping OpenAI models")
            return None

        headers = {
            "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.openai.com/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        # Filter to only include chat models (GPT models)
        chat_models = [
            model for model in raw_models
            if model and model.get("id", "").startswith(("gpt-", "o1-", "o3-", "chatgpt-"))
        ]

        normalized_models = [
            norm_model
            for model in chat_models
            if model
            for norm_model in [normalize_openai_model(model)]
            if norm_model is not None
        ]

        from src.cache import _openai_models_cache
        _openai_models_cache["data"] = normalized_models
        _openai_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("openai")

        logger.info(f"Fetched {len(normalized_models)} OpenAI models")
        return _openai_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("OpenAI HTTP error: %s", error_msg)
        set_gateway_error("openai", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from OpenAI: %s", error_msg)
        set_gateway_error("openai", error_msg)
        return None


def normalize_openai_model(openai_model: dict) -> dict | None:
    """Normalize OpenAI model entries to resemble OpenRouter model shape"""
    try:
        model_id = openai_model.get("id")
        if not model_id:
            return None

        slug = f"openai/{model_id}"
        provider_slug = "openai"

        # Generate display name
        display_name = model_id.replace("-", " ").replace("_", " ").title()
        # Clean up common patterns
        display_name = display_name.replace("Gpt ", "GPT-")
        display_name = display_name.replace("O1 ", "o1-")
        display_name = display_name.replace("O3 ", "o3-")

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
            "top_provider": None,
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


def fetch_models_from_anthropic():
    """Fetch models from Anthropic API and normalize to the catalog schema

    Uses the Anthropic Models API: https://docs.anthropic.com/en/api/models-list
    """
    try:
        if not Config.ANTHROPIC_API_KEY:
            logger.debug("Anthropic API key not configured - skipping Anthropic models")
            return None

        headers = {
            "x-api-key": Config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        all_models = []
        after_id = None

        # Paginate through all models
        while True:
            url = "https://api.anthropic.com/v1/models"
            params = {"limit": 100}
            if after_id:
                params["after_id"] = after_id

            response = httpx.get(
                url,
                headers=headers,
                params=params,
                timeout=20.0,
            )
            response.raise_for_status()

            payload = response.json()
            models_data = payload.get("data", [])
            all_models.extend(models_data)

            # Check if there are more pages
            if not payload.get("has_more", False):
                break
            after_id = payload.get("last_id")

        # Filter to only include Claude models (exclude any non-chat models)
        chat_models = [
            model for model in all_models
            if model and model.get("id", "").startswith("claude-")
        ]

        normalized_models = [
            norm_model
            for model in chat_models
            if model
            for norm_model in [normalize_anthropic_model(model)]
            if norm_model is not None
        ]

        from src.cache import _anthropic_models_cache
        _anthropic_models_cache["data"] = normalized_models
        _anthropic_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("anthropic")

        logger.info(f"Fetched {len(normalized_models)} Anthropic models from API")
        return _anthropic_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Anthropic HTTP error: %s", error_msg)
        set_gateway_error("anthropic", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Anthropic: %s", error_msg)
        set_gateway_error("anthropic", error_msg)
        return None


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
        display_name = anthropic_model.get("display_name") or anthropic_model.get("name", model_id)
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
            "top_provider": None,
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
