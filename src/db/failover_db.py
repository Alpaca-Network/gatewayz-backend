"""
Database layer for provider failover support
Handles queries for finding alternative providers for models
"""

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def get_providers_for_model(
    model_id: str,
    active_only: bool = True,
    healthy_only: bool = False,
    min_success_rate: float = 0.0
) -> list[dict[str, Any]]:
    """
    Get all providers that offer a specific model, sorted by health and performance

    This is the CRITICAL function for failover - it finds alternative providers
    when the primary provider fails.

    Args:
        model_id: Canonical model ID (e.g., "gpt-4", "llama-3-70b")
        active_only: Only return active providers
        healthy_only: Only return providers with health_status='healthy'
        min_success_rate: Minimum success rate (0.0 to 1.0)

    Returns:
        List of provider dictionaries with model-specific data, sorted by:
        1. Health status (healthy first)
        2. Response time (fastest first)
        3. Price (cheapest first)

    Example:
        providers = get_providers_for_model("gpt-4")
        # Returns:
        # [
        #   {
        #     "provider_id": 1,
        #     "provider_slug": "openrouter",
        #     "provider_name": "OpenRouter",
        #     "provider_model_id": "openai/gpt-4",
        #     "model_id": "gpt-4",
        #     "health_status": "healthy",
        #     "average_response_time_ms": 150,
        #     "pricing_prompt": 0.000030,  # per-token from model_pricing table
        #     "pricing_completion": 0.000060,  # per-token from model_pricing table
        #     "success_rate": 98.5,
        #     "is_active": true
        #   },
        #   {...}
        # ]
    """
    try:
        supabase = get_supabase_client()

        # Query models table with provider join and pricing from model_pricing table
        query = supabase.table("models").select(
            """
            id,
            model_id,
            provider_model_id,
            average_response_time_ms,
            health_status,
            success_rate,
            is_active,
            supports_streaming,
            supports_function_calling,
            supports_vision,
            context_length,
            model_pricing(
                price_per_input_token,
                price_per_output_token,
                price_per_image,
                price_per_request
            ),
            providers!inner(
                id,
                slug,
                name,
                health_status,
                average_response_time_ms,
                is_active,
                supports_streaming,
                supports_function_calling,
                supports_vision
            )
            """
        )

        # Filter by model_id (canonical name)
        query = query.eq("model_id", model_id)

        # Apply filters
        if active_only:
            query = query.eq("is_active", True)
            query = query.eq("providers.is_active", True)

        if healthy_only:
            query = query.eq("providers.health_status", "healthy")

        # Execute query
        response = query.execute()

        if not response.data:
            logger.warning(f"No providers found for model: {model_id}")
            return []

        # Transform and enrich data
        providers = []
        for row in response.data:
            provider_info = row["providers"]
            pricing_info = row.get("model_pricing") or {}

            # Build combined provider dict
            provider = {
                # Provider info
                "provider_id": provider_info["id"],
                "provider_slug": provider_info["slug"],
                "provider_name": provider_info["name"],
                "provider_health_status": provider_info["health_status"],
                "provider_response_time_ms": provider_info["average_response_time_ms"],
                "provider_is_active": provider_info["is_active"],

                # Model-specific info
                "model_db_id": row["id"],
                "model_id": row["model_id"],  # Canonical ID
                "provider_model_id": row["provider_model_id"],  # Provider-specific ID

                # Pricing from model_pricing table (per-token pricing)
                "pricing_prompt": float(pricing_info.get("price_per_input_token", 0)) if pricing_info.get("price_per_input_token") else 0.0,
                "pricing_completion": float(pricing_info.get("price_per_output_token", 0)) if pricing_info.get("price_per_output_token") else 0.0,
                "pricing_image": float(pricing_info.get("price_per_image", 0)) if pricing_info.get("price_per_image") else 0.0,
                "pricing_request": float(pricing_info.get("price_per_request", 0)) if pricing_info.get("price_per_request") else 0.0,

                # Health
                "model_health_status": row["health_status"],
                "model_response_time_ms": row["average_response_time_ms"],
                "success_rate": float(row["success_rate"]) if row["success_rate"] else 0.0,

                # Capabilities
                "supports_streaming": row["supports_streaming"],
                "supports_function_calling": row["supports_function_calling"],
                "supports_vision": row["supports_vision"],
                "context_length": row["context_length"],

                # Status
                "is_active": row["is_active"],
            }

            # Apply success rate filter
            if min_success_rate > 0 and provider["success_rate"] < min_success_rate:
                continue

            providers.append(provider)

        # Sort by priority (health, speed, cost)
        providers.sort(key=lambda p: (
            0 if p["provider_health_status"] == "healthy" else (
                1 if p["provider_health_status"] == "degraded" else 2
            ),
            p["provider_response_time_ms"] or 9999,
            p["pricing_prompt"]
        ))

        logger.info(f"Found {len(providers)} providers for model '{model_id}'")
        return providers

    except Exception as e:
        logger.error(f"Error fetching providers for model '{model_id}': {e}")
        return []


def get_provider_model_id(canonical_model_id: str, provider_slug: str) -> str | None:
    """
    Get the provider-specific model ID for a canonical model ID

    Example:
        get_provider_model_id("gpt-4", "openrouter")
        # Returns: "openai/gpt-4"

        get_provider_model_id("gpt-4", "featherless")
        # Returns: "gpt-4"

    Args:
        canonical_model_id: Canonical model ID (e.g., "gpt-4")
        provider_slug: Provider slug (e.g., "openrouter")

    Returns:
        Provider-specific model ID or None if not found
    """
    try:
        supabase = get_supabase_client()

        response = supabase.table("models").select(
            "provider_model_id"
        ).eq(
            "model_id", canonical_model_id
        ).eq(
            "providers.slug", provider_slug
        ).single().execute()

        if response.data:
            return response.data["provider_model_id"]

        return None

    except Exception as e:
        logger.warning(f"Could not find provider model ID for {canonical_model_id} on {provider_slug}: {e}")
        return None


def get_healthy_providers(min_success_rate: float = 80.0) -> list[dict[str, Any]]:
    """
    Get all healthy providers sorted by performance

    Args:
        min_success_rate: Minimum success rate percentage (0-100)

    Returns:
        List of healthy provider dictionaries
    """
    try:
        supabase = get_supabase_client()

        response = supabase.table("providers").select(
            "*"
        ).eq(
            "is_active", True
        ).eq(
            "health_status", "healthy"
        ).order(
            "average_response_time_ms", desc=False
        ).execute()

        return response.data or []

    except Exception as e:
        logger.error(f"Error fetching healthy providers: {e}")
        return []


def check_model_available_on_provider(
    model_id: str,
    provider_slug: str
) -> bool:
    """
    Check if a specific model is available on a provider

    Args:
        model_id: Canonical model ID
        provider_slug: Provider slug

    Returns:
        True if model is available and active on the provider
    """
    try:
        supabase = get_supabase_client()

        response = supabase.table("models").select(
            "id"
        ).eq(
            "model_id", model_id
        ).eq(
            "is_active", True
        ).eq(
            "providers.slug", provider_slug
        ).eq(
            "providers.is_active", True
        ).execute()

        return len(response.data or []) > 0

    except Exception as e:
        logger.error(f"Error checking model availability: {e}")
        return False
