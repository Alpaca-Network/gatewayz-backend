import json
import logging
from datetime import datetime, timezone
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
)
from src.services.modelz_client import (
    check_model_exists_on_modelz,
    fetch_modelz_tokens,
    get_modelz_model_details,
    get_modelz_model_ids,
)
from src.services.providers import enhance_providers_with_logos_and_sites, get_cached_providers
from src.services.gateway_aggregator import (
    fetch_models_from_gateways,
    derive_providers_from_gateway_models,
    merge_models_by_slug as merge_models_dedup,
)
from src.config.gateway_registry import (
    GATEWAY_REGISTRY,
    get_gateway_config,
    get_comparison_gateways,
    get_all_gateway_slugs,
    get_gateway_note,
    is_valid_gateway,
    get_gateway_description_text,
)
from src.routes.catalog_helpers import (
    normalize_gateway_value,
    get_graduation_filter_description,
    enhance_models_batch,
    get_timestamp,
    handle_endpoint_errors,
)
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Single router for all model catalog endpoints
router = APIRouter()

# Constants for query parameter descriptions (to avoid duplication)
DESC_INCLUDE_HUGGINGFACE = "Include Hugging Face metrics if available"
# REFACTORED: Generate gateway descriptions dynamically from registry
DESC_GATEWAY_AUTO_DETECT = get_gateway_description_text(include_all=False, include_auto_detect=True)
DESC_GATEWAY_WITH_ALL = get_gateway_description_text(include_all=True, include_auto_detect=False)
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


def derive_providers_from_models(models: list[dict], gateway_name: str) -> list[dict]:
    """
    Generic function to derive provider list from model list for any gateway.
    Used for gateways that don't have a dedicated provider endpoint.
    """
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

        # Clean up slug
        provider_slug = provider_slug.lstrip("@").lower()

        if provider_slug not in providers:
            providers[provider_slug] = {
                "slug": provider_slug,
                "site_url": model.get("provider_site_url"),
                "logo_url": model.get("model_logo_url") or model.get("logo_url"),
                "moderated_by_openrouter": False,
                "source_gateway": gateway_name,
                "source_gateways": [gateway_name],
            }

    return list(providers.values())


def merge_provider_lists(*provider_lists: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for providers in provider_lists:
        for provider in providers or []:
            slug = provider.get("slug")
            if not slug:
                continue
            if slug not in merged:
                copied = provider.copy()
                sources = list(copied.get("source_gateways", []) or [])
                source = copied.get("source_gateway")
                if source and source not in sources:
                    sources.append(source)
                copied["source_gateways"] = sources
                merged[slug] = copied
            else:
                existing = merged[slug]
                sources = existing.get("source_gateways", [])
                for src in provider.get("source_gateways", []) or []:
                    if src and src not in sources:
                        sources.append(src)
                source = provider.get("source_gateway")
                if source and source not in sources:
                    sources.append(source)
                existing["source_gateways"] = sources
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
@handle_endpoint_errors("Failed to get providers")
async def get_providers(
    moderated_only: bool = Query(False, description="Filter for moderated providers only"),
    limit: int | None = Query(None, description=DESC_LIMIT_NUMBER_OF_RESULTS),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    gateway: str | None = Query(
        "all",
        description=DESC_GATEWAY_WITH_ALL,
    ),
):
    """Get all available provider list with detailed metric data including model count and logo URLs"""
    # REFACTORED: Use helper for gateway normalization
    gateway_value = normalize_gateway_value(gateway)

    # REFACTORED: Use gateway aggregator to fetch models
    gateway_models = fetch_models_from_gateways(gateway_value)
    provider_groups: list[list[dict]] = []

    # Handle OpenRouter providers (has dedicated endpoint)
    if gateway_value in ("openrouter", "all"):
        raw_providers = get_cached_providers()
        if not raw_providers and gateway_value == "openrouter":
            logger.warning("OpenRouter provider data unavailable - returning empty response")

        enhanced_openrouter = annotate_provider_sources(
            enhance_providers_with_logos_and_sites(raw_providers or []),
            "openrouter",
        )
        provider_groups.append(enhanced_openrouter)

    # Derive providers from models for other gateways
    derived_providers = derive_providers_from_gateway_models(gateway_models, gateway_value)
    if derived_providers:
        provider_groups.append(derived_providers)

    if not provider_groups:
        logger.warning(f"No provider data available for gateway={gateway_value} - returning empty response")
        return {
            "data": [],
            "total": 0,
            "returned": 0,
            "offset": offset or 0,
            "limit": limit,
            "gateway": gateway_value,
            "timestamp": get_timestamp(),
        }

    combined_providers = merge_provider_lists(*provider_groups)

    # Get all models for counting
    models_for_counts = gateway_models.get_all_models()

    # Filter moderated providers
    if moderated_only:
        combined_providers = [
            provider
            for provider in combined_providers
            if provider.get("moderated_by_openrouter")
        ]

    # Apply pagination
    total_providers = len(combined_providers)
    if offset:
        combined_providers = combined_providers[offset:]
    if limit:
        combined_providers = combined_providers[:limit]

    # Add model counts to providers
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
        "timestamp": get_timestamp(),
    }


# ============================================================================
# INTERNAL HELPER FUNCTIONS (Used by public API endpoints below)
# These functions contain the actual logic but are not directly exposed as routes
# ============================================================================


@handle_endpoint_errors("Failed to get models")
async def get_models(
    provider: str | None = Query(None, description="Filter models by provider"),
    is_private: bool | None = Query(
        None,
        description="Filter by private models: true=private only, false=non-private only, null=all models",
    ),
    limit: int | None = Query(None, description=DESC_LIMIT_NUMBER_OF_RESULTS),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    include_huggingface: bool = Query(
        True, description="Include Hugging Face metrics for models that have hugging_face_id"
    ),
    gateway: str | None = Query(
        "all",
        description=DESC_GATEWAY_WITH_ALL,
    ),
):
    """Get all metric data of available models with optional filtering, pagination, Hugging Face integration, and provider logos"""
    provider = normalize_developer_segment(provider)
    logger.debug(f"/models endpoint called with gateway parameter: {repr(gateway)}")
    # REFACTORED: Use helper for gateway normalization
    gateway_value = normalize_gateway_value(gateway)
    logger.debug(
        f"Getting models with provider={provider}, limit={limit}, offset={offset}, gateway={gateway_value}"
    )

    # REFACTORED: Fetch models from all matching gateways (replaces 193 lines)
    gateway_models = fetch_models_from_gateways(gateway_value)
    models = gateway_models.get_models_for_gateway_value(gateway_value)

    # Handle empty results
    if not models:
        config = get_gateway_config(gateway_value)
        if config and not config["supports_public_catalog"]:
            logger.info(
                f"Returning empty {config['name']} catalog response because no public model listing exists"
            )
        else:
            logger.warning(
                "No models available for gateway=%s. Returning empty response. "
                "This may indicate provider API keys not configured or all providers are down.",
                gateway_value
            )

    # REFACTORED: Build provider lists (replaces 115 lines)
    provider_groups: list[list[dict]] = []

    # Handle OpenRouter providers (has dedicated endpoint)
    if gateway_value in ("openrouter", "all"):
        providers = get_cached_providers()
        if not providers and gateway_value == "openrouter":
            logger.warning("OpenRouter provider data unavailable - returning empty providers list")
        enhanced_providers = annotate_provider_sources(
            enhance_providers_with_logos_and_sites(providers or []),
            "openrouter",
        )
        provider_groups.append(enhanced_providers)

    # Derive providers from models for other gateways
    derived_providers = derive_providers_from_gateway_models(gateway_models, gateway_value)
    if derived_providers:
        # Annotate derived providers with their source gateway info
        for provider_dict in derived_providers:
            source_gateways = provider_dict.get("source_gateways", [])
            if source_gateways:
                provider_dict["source_gateway"] = source_gateways[0]
        provider_groups.append(derived_providers)

    enhanced_providers = merge_provider_lists(*provider_groups)
    logger.info(f"Retrieved {len(enhanced_providers)} enhanced providers from cache")

    if provider:
        provider_lower = provider.lower()
        original_count = len(models)
        filtered_models = []
        for model in models:
            model_id = (model.get("id") or "").lower()
            provider_slug = (model.get("provider_slug") or "").lower()
            if provider_lower in model_id or provider_lower == provider_slug:
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

    # Ensure offset and limit are integers
    try:
        offset_int = int(str(offset)) if offset else 0
        limit_int = int(str(limit)) if limit else None
    except (ValueError, TypeError):
        offset_int = 0
        limit_int = None

    if offset_int:
        models = models[offset_int:]
        logger.info(f"Applied offset {offset_int}: {len(models)} models remaining")
    if limit_int and gateway_value != "all":
        logger.debug(
            "Ignoring limit=%s for gateway '%s' to return full catalog",
            limit_int,
            gateway_value,
        )
        limit_int = None

    if limit_int:
        models = models[:limit_int]
        logger.info(f"Applied limit {limit_int}: {len(models)} models remaining")

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

    # REFACTORED: Use gateway registry for note generation
    note = get_gateway_note(gateway_value)

    result = {
        "data": enhanced_models,
        "total": total_models,
        "returned": len(enhanced_models),
        "offset": offset_int,
        "limit": limit_int,
        "include_huggingface": include_huggingface,
        "gateway": gateway_value,
        "note": note,
        "timestamp": get_timestamp(),
    }
    logger.debug(
        f"Returning /models response with keys: {list(result.keys())}, gateway={gateway_value}, first_model={enhanced_models[0]['id'] if enhanced_models else 'none'}"
    )

    # Return response with cache headers for browser/CDN caching
    # Cache for 5 minutes since data changes infrequently (1 hour TTL on backend)
    return Response(
        content=json.dumps(result),
        media_type="application/json",
        headers={
            "Cache-Control": "public, max-age=300",  # 5 minute browser cache
            "ETag": f'"{hash(json.dumps(enhanced_models[:5]))}"',  # Simple ETag for validation
        },
    )


@handle_endpoint_errors("Failed to get model data")
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
    openrouter_providers = get_cached_providers()
    if openrouter_providers:
        enhanced_openrouter = annotate_provider_sources(
            enhance_providers_with_logos_and_sites(openrouter_providers),
            "openrouter",
        )
        provider_groups.append(enhanced_openrouter)

    # REFACTORED: Derive providers from all non-OpenRouter gateways (replaces 34 lines of hard-coded lists)
    # OpenRouter providers already fetched above, all other gateways need provider derivation
    if detected_gateway != "openrouter" and is_valid_gateway(detected_gateway):
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
        "timestamp": get_timestamp(),
    }


@handle_endpoint_errors("Failed to get developer models")
async def get_developer_models(
    developer_name: str,
    limit: int | None = Query(None, description=DESC_LIMIT_NUMBER_OF_RESULTS),
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
    developer_name = normalize_developer_segment(developer_name) or developer_name
    logger.info("Getting models for developer: %s", sanitize_for_logging(developer_name))

    # REFACTORED: Use helper for gateway normalization
    gateway_value = normalize_gateway_value(gateway)
    models = get_cached_models(gateway_value)

    if not models:
        raise HTTPException(status_code=503, detail=ERROR_MODELS_DATA_UNAVAILABLE)

    # Filter models by developer/provider
    developer_lower = developer_name.lower()
    filtered_models = []

    for model in models:
        model_id = (model.get("id") or "").lower()
        provider_slug = (model.get("provider_slug") or "").lower()

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

    # REFACTORED: Use batch enhancement helper
    providers = get_cached_providers()
    enhanced_providers = enhance_providers_with_logos_and_sites(providers or [])
    enhanced_models = enhance_models_batch(filtered_models, enhanced_providers, include_huggingface)

    return {
        "developer": developer_name,
        "models": enhanced_models,
        "total": total_models,
        "count": len(enhanced_models),
        "offset": offset,
        "limit": limit,
        "gateway": gateway_value,
    }


# ==================== NEW: Gateway & Provider Statistics Endpoints ====================


@router.get("/provider/{provider_name}/stats", tags=["statistics"])
@handle_endpoint_errors("Failed to get provider statistics")
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

    return {"success": True, "data": stats, "timestamp": get_timestamp()}


@router.get("/gateway/{gateway}/stats", tags=["statistics"])
@handle_endpoint_errors("Failed to get gateway statistics")
async def get_gateway_statistics(
    gateway: str, time_range: str = Query("24h", description=DESC_TIME_RANGE_ALL)
):
    """
    Get comprehensive statistics for a specific gateway

    This endpoint provides usage statistics for a gateway (e.g., openrouter, groq, deepinfra).
    **This fixes the "Top Provider: N/A" issue in your UI!**

    Args:
        gateway: Gateway name ('openrouter', 'featherless', 'deepinfra', 'chutes', 'groq', 'helicone', etc.)
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
    logger.info(
        "Fetching stats for gateway: %s, time_range: %s",
        sanitize_for_logging(gateway),
        sanitize_for_logging(time_range),
    )

    # REFACTORED: Use gateway registry for validation
    if not is_valid_gateway(gateway.lower()):
        valid_gateways = get_all_gateway_slugs()
        raise HTTPException(
            status_code=400,
            detail=f"Invalid gateway. Must be one of: {', '.join(valid_gateways)}",
        )

    stats = get_gateway_stats(gateway=gateway, time_range=time_range)

    if "error" in stats:
        raise HTTPException(status_code=500, detail=stats["error"])

    return {"success": True, "data": stats, "timestamp": get_timestamp()}


@handle_endpoint_errors("Failed to get trending models")
async def get_trending_models_endpoint(
    gateway: str | None = Query("all", description="Gateway filter or 'all'"),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_NO_ALL),
    limit: int = Query(10, description=DESC_NUMBER_OF_MODELS_TO_RETURN, ge=1, le=100),
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
        sort_by: Sort criteria ('requests', 'tokens', 'users')

    Returns:
        List of trending models with statistics

    Example:
        GET /catalog/models/trending?time_range=24h&limit=10
        GET /catalog/models/trending?gateway=deepinfra&sort_by=tokens
    """
    logger.info(
        "Fetching trending models: gateway=%s, time_range=%s, sort_by=%s",
        sanitize_for_logging(gateway),
        sanitize_for_logging(time_range),
        sanitize_for_logging(sort_by),
    )

    # Validate sort_by
    valid_sort = ["requests", "tokens", "users"]
    if sort_by not in valid_sort:
        raise HTTPException(
            status_code=400, detail=f"Invalid sort_by. Must be one of: {', '.join(valid_sort)}"
        )

    trending = get_trending_models(
        gateway=gateway, time_range=time_range, limit=limit, sort_by=sort_by
    )

    return {
        "success": True,
        "data": trending,
        "count": len(trending),
        "gateway": gateway,
        "time_range": time_range,
        "sort_by": sort_by,
        "timestamp": get_timestamp(),
    }


@router.get("/gateways/summary", tags=["statistics"])
@handle_endpoint_errors("Failed to get gateways summary")
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
    logger.info(
        "Fetching summary for all gateways: time_range=%s", sanitize_for_logging(time_range)
    )

    summary = get_all_gateways_summary(time_range=time_range)

    if "error" in summary:
        raise HTTPException(status_code=500, detail=summary["error"])

    return {
        "success": True,
        "data": summary,
        "timestamp": get_timestamp(),
    }


@router.get("/provider/{provider_name}/top-models", tags=["statistics"])
@handle_endpoint_errors("Failed to get top models")
async def get_provider_top_models_endpoint(
    provider_name: str,
    limit: int = Query(5, description=DESC_NUMBER_OF_MODELS_TO_RETURN, ge=1, le=20),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_ALL),
):
    """
    Get top models for a specific provider

    This endpoint returns the most used models from a provider.

    Args:
        provider_name: Provider name (e.g., 'openai', 'anthropic')
        limit: Number of models to return
        time_range: Time range for statistics

    Returns:
        List of top models with usage statistics

    Example:
        GET /catalog/provider/openai/top-models?limit=5&time_range=7d
    """
    provider_name = normalize_developer_segment(provider_name) or provider_name
    logger.info("Fetching top models for provider: %s", sanitize_for_logging(provider_name))

    top_models = get_top_models_by_provider(
        provider_name=provider_name, limit=limit, time_range=time_range
    )

    return {
        "success": True,
        "provider": provider_name,
        "data": top_models,
        "count": len(top_models),
        "time_range": time_range,
        "timestamp": get_timestamp(),
    }


# ==================== NEW: Model Comparison Endpoints ====================


@handle_endpoint_errors("Failed to compare model")
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
    provider_name = normalize_developer_segment(provider_name) or provider_name
    model_name = normalize_model_segment(model_name) or model_name
    logger.info(
        "Comparing model %s/%s across gateways",
        sanitize_for_logging(provider_name),
        sanitize_for_logging(model_name),
    )

    # REFACTORED: Use gateway registry instead of hard-coded list
    if gateways and gateways.lower() != "all":
        gateway_list = [g.strip().lower() for g in gateways.split(",")]
    else:
        gateway_list = get_comparison_gateways()

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
        "timestamp": get_timestamp(),
    }


@handle_endpoint_errors("Failed to batch compare models")
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
            # REFACTORED: Use gateway registry instead of hard-coded list
            all_gateways = get_comparison_gateways()
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
        "timestamp": get_timestamp(),
    }


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
    limit: int | None = Query(
        50, description=f"{DESC_LIMIT_NUMBER_OF_RESULTS} (default: 50 for fast load)"
    ),
    offset: int | None = Query(0, description=DESC_OFFSET_FOR_PAGINATION),
    include_huggingface: bool = Query(
        False,
        description="Include Hugging Face metrics for models that have hugging_face_id (slower, default: false)",
    ),
    gateway: str | None = Query(
        "openrouter",
        description=DESC_GATEWAY_WITH_ALL,
    ),
):
    return await get_models(
        provider=provider,
        is_private=is_private,
        limit=limit,
        offset=offset,
        include_huggingface=include_huggingface,
        gateway=gateway,
    )


@router.get("/models/trending", tags=["statistics"])
async def get_trending_models_api(
    gateway: str | None = Query("all", description="Gateway filter or 'all'"),
    time_range: str = Query("24h", description=DESC_TIME_RANGE_NO_ALL),
    limit: int = Query(10, description=DESC_NUMBER_OF_MODELS_TO_RETURN, ge=1, le=100),
    sort_by: str = Query("requests", description="Sort by: 'requests', 'tokens', 'users'"),
):
    return await get_trending_models_endpoint(
        gateway=gateway,
        time_range=time_range,
        limit=limit,
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
        get_low_latency_models,
        get_ultra_low_latency_models,
        get_fastest_providers,
        PROVIDER_LATENCY_TIERS,
        suggest_low_latency_alternative,
    )

    if include_ultra_only:
        models = get_ultra_low_latency_models()
    else:
        models = get_low_latency_models()

    result = {
        "models": models,
        "count": len(models),
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
    limit: int | None = Query(None, description=DESC_LIMIT_NUMBER_OF_RESULTS),
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
@handle_endpoint_errors("Failed to search models")
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
        description="Gateway filter: openrouter, featherless, deepinfra, chutes, groq, fireworks, together, helicone, aihubmix, vercel-ai-gateway, or all",
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
    # REFACTORED: Use gateway aggregator to fetch models
    gateway_value = gateway.lower() if gateway else "all"
    gateway_models = fetch_models_from_gateways(gateway_value)
    all_models = gateway_models.get_all_models()

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
                if prompt and completion:
                    return (float(prompt) + float(completion)) / 2
            return float("inf")  # Put models without pricing at the end

        elif sort_by == "context":
            return model.get("context_length", 0)

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
        "timestamp": get_timestamp(),
    }


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
        except (ValueError, TypeError):
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
        except (ValueError, TypeError):
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
@handle_endpoint_errors("Failed to fetch models from Modelz")
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
            # REFACTORED: Use helper for graduation filter description
            "description": get_graduation_filter_description(is_graduated),
        },
        "source": "modelz",
        "api_reference": "https://backend.alpacanetwork.ai/api/tokens",
    }


@router.get("/modelz/ids")
@handle_endpoint_errors("Failed to fetch model IDs from Modelz")
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
    logger.info("Fetching Modelz model IDs with is_graduated=%s", is_graduated)

    model_ids = await get_modelz_model_ids(is_graduated)

    return {
        "model_ids": model_ids,
        "total_count": len(model_ids),
        "filter": {
            "is_graduated": is_graduated,
            # REFACTORED: Use helper for graduation filter description
            "description": get_graduation_filter_description(is_graduated),
        },
        "source": "modelz",
    }


@router.get("/modelz/check/{model_id}")
@handle_endpoint_errors("Unexpected error in check_model_on_modelz")
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
            # REFACTORED: Use helper for graduation filter description
            "description": get_graduation_filter_description(is_graduated),
        },
        "source": "modelz",
    }

    # If model exists, get additional details
    if exists:
        model_details = await get_modelz_model_details(model_id)
        if model_details:
            result["model_details"] = model_details

    return result


# HuggingFace Hub SDK Discovery Endpoints
@router.get("/huggingface/discovery", tags=["huggingface-discovery"])
@handle_endpoint_errors("Failed to discover HuggingFace models")
async def discover_huggingface_models(
    task: str | None = Query(
        "text-generation",
        description="Filter by task type (e.g., 'text-generation', 'text2text-generation', 'conversational')",
    ),
    sort: str = Query("likes", description="Sort by: 'likes' or 'downloads'"),
    limit: int = Query(50, description="Number of models to return", ge=1, le=500),
):
    """
    Discover HuggingFace models using the official Hub SDK.

    This endpoint provides advanced model discovery with filtering by task type
    and sorting by popularity metrics (likes/downloads). Great for exploring
    available models on HuggingFace Hub.

    Uses the official huggingface_hub SDK for direct API access to Hub metadata.
    """
    from src.services.huggingface_hub_service import list_huggingface_models

    logger.info(f"Discovering HuggingFace models: task={task}, sort={sort}, limit={limit}")

    models = list_huggingface_models(
        task=task,
        sort=sort,
        limit=limit,
    )

    if not models:
        logger.warning(f"No HuggingFace models found for task={task}")
        return {
            "models": [],
            "count": 0,
            "source": "huggingface-hub",
            "task": task,
            "sort": sort,
        }

    return {
        "models": models,
        "count": len(models),
        "source": "huggingface-hub",
        "task": task,
        "sort": sort,
    }


@router.get("/huggingface/search", tags=["huggingface-discovery"])
@handle_endpoint_errors("Failed to search HuggingFace models")
async def search_huggingface_models_endpoint(
    q: str = Query(..., description="Search query (model name, description, etc.)", min_length=1),
    task: str | None = Query(None, description="Optional task filter"),
    limit: int = Query(20, description="Number of results to return", ge=1, le=100),
):
    """
    Search for HuggingFace models by query.

    Searches across model names, descriptions, and other metadata.
    Uses the official huggingface_hub SDK.
    """
    from src.services.huggingface_hub_service import search_models_by_query

    logger.info(f"Searching HuggingFace models: q='{q}', task={task}, limit={limit}")

    models = search_models_by_query(
        query=q,
        task=task,
        limit=limit,
    )

    return {
        "query": q,
        "models": models,
        "count": len(models),
        "source": "huggingface-hub",
    }


@router.get("/huggingface/models/{model_id:path}/details", tags=["huggingface-discovery"])
@handle_endpoint_errors("Failed to fetch model details")
async def get_huggingface_model_details_endpoint(
    model_id: str,
):
    """
    Get detailed information about a specific HuggingFace model.

    Returns comprehensive metadata including model card, library info, and metrics.
    Uses the official huggingface_hub SDK for direct Hub access.
    """
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


@router.get("/huggingface/models/{model_id:path}/card", tags=["huggingface-discovery"])
@handle_endpoint_errors("Failed to fetch model card")
async def get_huggingface_model_card_endpoint(
    model_id: str,
):
    """
    Retrieve the model card (README) for a HuggingFace model.

    The model card contains documentation, usage instructions, and metadata
    about the model in Markdown format.
    """
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


@router.get("/huggingface/author/{author}/models", tags=["huggingface-discovery"])
@handle_endpoint_errors("Failed to list author models")
async def list_author_models_endpoint(
    author: str,
    limit: int = Query(50, description="Number of models to return", ge=1, le=500),
):
    """
    List all models from a specific HuggingFace author or organization.

    Returns all public models published by the specified author/org.
    """
    from src.services.huggingface_hub_service import list_models_by_author

    logger.info(f"Listing models from author: {author}, limit={limit}")

    models = list_models_by_author(author=author, limit=limit)

    return {
        "author": author,
        "models": models,
        "count": len(models),
        "source": "huggingface-hub",
    }


@router.get("/huggingface/models/{model_id:path}/files", tags=["huggingface-discovery"])
@handle_endpoint_errors("Failed to fetch model files")
async def get_model_files_endpoint(
    model_id: str,
):
    """
    Get information about all files in a HuggingFace model repository.

    Returns a list of files with sizes and metadata, useful for understanding
    what's available in the model repository.
    """
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
