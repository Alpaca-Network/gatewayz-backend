"""
Catalog route helpers - Functions for model/provider fetching, normalization, and enhancement.

This module contains reusable utilities for catalog endpoints including:
- Gateway normalization and validation
- Model and provider enhancement
- Error handling decorators
- Timestamp utilities
"""

from datetime import datetime, timezone
from typing import Any, Callable
from functools import wraps
import logging

logger = logging.getLogger(__name__)


def handle_endpoint_errors(error_message: str) -> Callable:
    """
    Decorator to handle common error patterns in catalog endpoints

    Replaces the repetitive pattern of:
        try:
            # endpoint logic
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to ...: {e}")
            raise HTTPException(status_code=500, detail="Failed to ...")

    Args:
        error_message: Base error message for the operation (e.g., "Failed to get models")

    Returns:
        Decorated function with error handling

    Example:
        @handle_endpoint_errors("Failed to get providers")
        async def get_providers(...):
            # implementation
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from fastapi import HTTPException

            try:
                return await func(*args, **kwargs)
            except HTTPException:
                # Re-raise HTTP exceptions (like 404, 400, etc.)
                raise
            except Exception as e:
                logger.error(f"{error_message}: {e}")
                raise HTTPException(status_code=500, detail=error_message)

        return wrapper
    return decorator


def normalize_gateway_value(gateway: str | None) -> str:
    """
    Normalize gateway value to handle common aliases and defaults

    Args:
        gateway: Raw gateway value from query parameter

    Returns:
        Normalized gateway slug ('hug' for huggingface, 'all' for None, lowercase otherwise)
    """
    gateway_value = (gateway or "all").lower()
    if gateway_value == "huggingface":
        gateway_value = "hug"
    return gateway_value


def get_graduation_filter_description(is_graduated: bool | None) -> str:
    """
    Get appropriate description text based on graduation filter

    Args:
        is_graduated: Filter value (True=graduated only, False=non-graduated only, None=all)

    Returns:
        Appropriate description constant
    """
    from src.routes.catalog import (
        DESC_ALL_MODELS,
        DESC_GRADUATED_MODELS_ONLY,
        DESC_NON_GRADUATED_MODELS_ONLY,
    )

    if is_graduated is None:
        return DESC_ALL_MODELS
    elif is_graduated:
        return DESC_GRADUATED_MODELS_ONLY
    else:
        return DESC_NON_GRADUATED_MODELS_ONLY


def enhance_models_batch(
    models: list[dict],
    providers: list[dict],
    include_huggingface: bool = True
) -> list[dict]:
    """
    Enhance a batch of models with provider info and optionally HuggingFace data

    This replaces the common pattern of:
        for model in models:
            enhanced = enhance_model_with_provider_info(model, providers)
            if include_huggingface and enhanced.get("hugging_face_id"):
                enhanced = enhance_model_with_huggingface_data(enhanced)
            enhanced_models.append(enhanced)

    Args:
        models: List of model dictionaries to enhance
        providers: List of provider dictionaries for matching
        include_huggingface: Whether to fetch HuggingFace data

    Returns:
        List of enhanced model dictionaries
    """
    from src.services.models import (
        enhance_model_with_provider_info,
        enhance_model_with_huggingface_data,
    )

    enhanced_models = []
    for model in models:
        enhanced_model = enhance_model_with_provider_info(model, providers)
        if include_huggingface and enhanced_model.get("hugging_face_id"):
            enhanced_model = enhance_model_with_huggingface_data(enhanced_model)
        enhanced_models.append(enhanced_model)

    return enhanced_models


def get_timestamp() -> str:
    """
    Get current timestamp in ISO format with UTC timezone

    Returns:
        ISO-formatted timestamp string
    """
    return datetime.now(timezone.utc).isoformat()


def fetch_and_merge_providers(
    gateway_models: Any,  # GatewayModels type
    gateway_value: str
) -> list[dict]:
    """
    Fetch providers from OpenRouter endpoint and derive from other gateway models

    This replaces the common pattern of:
        1. Fetch OpenRouter providers if applicable
        2. Derive providers from gateway models
        3. Merge and enhance provider lists

    Args:
        gateway_models: GatewayModels instance with fetched model data
        gateway_value: Normalized gateway value ('all', 'openrouter', etc.)

    Returns:
        Merged and enhanced provider list
    """
    from src.services.providers import (
        get_cached_providers,
        enhance_providers_with_logos_and_sites,
    )
    from src.services.gateway_aggregator import derive_providers_from_gateway_models
    from src.routes.catalog import annotate_provider_sources, merge_provider_lists

    provider_groups: list[list[dict]] = []

    # Handle OpenRouter providers (has dedicated endpoint)
    if gateway_value in ("openrouter", "all"):
        openrouter_providers = get_cached_providers()
        if not openrouter_providers and gateway_value == "openrouter":
            logger.warning("OpenRouter provider data unavailable")
        enhanced_providers = annotate_provider_sources(
            enhance_providers_with_logos_and_sites(openrouter_providers or []),
            "openrouter",
        )
        provider_groups.append(enhanced_providers)

    # Derive providers from models for other gateways
    derived_providers = derive_providers_from_gateway_models(gateway_models, gateway_value)
    if derived_providers:
        provider_groups.append(derived_providers)

    # Merge all provider groups
    merged_providers = merge_provider_lists(*provider_groups)
    logger.info(f"Retrieved {len(merged_providers)} enhanced providers")

    return merged_providers


def annotate_provider_sources(providers: list[dict], source: str) -> list[dict]:
    """
    Annotate each provider with its source gateway

    Args:
        providers: List of provider dictionaries
        source: Source gateway slug (e.g., 'openrouter', 'huggingface')

    Returns:
        List of providers with source_gateway and source_gateways fields
    """
    annotated = []
    for provider in providers or []:
        entry = provider.copy()
        entry.setdefault("source_gateway", source)
        entry.setdefault("source_gateways", [source])
        if source not in entry["source_gateways"]:
            entry["source_gateways"].append(source)
        annotated.append(entry)
    return annotated


def merge_provider_lists(*provider_lists: list[list[dict]]) -> list[dict]:
    """
    Merge multiple provider lists by slug, preserving all source gateways

    Args:
        *provider_lists: Variable number of provider list arguments

    Returns:
        Deduplicated list of providers with combined source_gateways
    """
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
