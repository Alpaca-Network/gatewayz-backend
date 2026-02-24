"""
Database layer for providers management
Handles CRUD operations for AI model providers
"""

import logging
from datetime import UTC, datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def get_all_providers(
    is_active_only: bool = True, include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Get all providers from database

    Args:
        is_active_only: Only return active providers
        include_inactive: Include inactive providers

    Returns:
        List of provider dictionaries
    """
    try:
        supabase = get_supabase_client()
        query = supabase.table("providers").select("*")

        if is_active_only and not include_inactive:
            query = query.eq("is_active", True)

        query = query.order("name")
        response = query.execute()

        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching providers: {e}")
        return []


def get_provider_by_id(provider_id: int) -> dict[str, Any] | None:
    """
    Get a provider by its ID

    Args:
        provider_id: Provider ID

    Returns:
        Provider dictionary or None
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("providers").select("*").eq("id", provider_id).single().execute()
        return response.data
    except Exception as e:
        logger.error(f"Error fetching provider {provider_id}: {e}")
        return None


def get_provider_by_slug(slug: str) -> dict[str, Any] | None:
    """
    Get a provider by its slug

    Args:
        slug: Provider slug (e.g., 'openrouter', 'portkey')

    Returns:
        Provider dictionary or None
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("providers").select("*").eq("slug", slug).single().execute()
        return response.data
    except Exception as e:
        logger.error(f"Error fetching provider by slug {slug}: {e}")
        return None


def create_provider(provider_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Create a new provider

    Args:
        provider_data: Provider data dictionary

    Returns:
        Created provider dictionary or None
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("providers").insert(provider_data).execute()

        if response.data:
            logger.info(f"Created provider: {provider_data.get('name')}")
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Error creating provider: {e}")
        return None


def update_provider(provider_id: int, provider_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Update a provider

    Args:
        provider_id: Provider ID
        provider_data: Updated provider data

    Returns:
        Updated provider dictionary or None
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("providers").update(provider_data).eq("id", provider_id).execute()

        if response.data:
            logger.info(f"Updated provider {provider_id}")
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Error updating provider {provider_id}: {e}")
        return None


def delete_provider(provider_id: int) -> bool:
    """
    Delete a provider (hard delete)

    Args:
        provider_id: Provider ID

    Returns:
        True if successful, False otherwise
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("providers").delete().eq("id", provider_id).execute()

        if response.data:
            logger.info(f"Deleted provider {provider_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error deleting provider {provider_id}: {e}")
        return False


def deactivate_provider(provider_id: int) -> dict[str, Any] | None:
    """
    Deactivate a provider (soft delete)

    Args:
        provider_id: Provider ID

    Returns:
        Updated provider dictionary or None
    """
    return update_provider(provider_id, {"is_active": False})


def activate_provider(provider_id: int) -> dict[str, Any] | None:
    """
    Activate a provider

    Args:
        provider_id: Provider ID

    Returns:
        Updated provider dictionary or None
    """
    return update_provider(provider_id, {"is_active": True})


def update_provider_health(
    provider_id: int, health_status: str, average_response_time_ms: int | None = None
) -> dict[str, Any] | None:
    """
    Update provider health status

    Args:
        provider_id: Provider ID
        health_status: Health status ('healthy', 'degraded', 'down', 'unknown')
        average_response_time_ms: Average response time in milliseconds

    Returns:
        Updated provider dictionary or None
    """
    try:
        update_data = {
            "health_status": health_status,
            "last_health_check_at": datetime.now(UTC).isoformat(),
        }

        if average_response_time_ms is not None:
            update_data["average_response_time_ms"] = average_response_time_ms

        return update_provider(provider_id, update_data)
    except Exception as e:
        logger.error(f"Error updating provider health {provider_id}: {e}")
        return None


def get_providers_by_health_status(health_status: str) -> list[dict[str, Any]]:
    """
    Get providers by health status

    Args:
        health_status: Health status to filter by

    Returns:
        List of provider dictionaries
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("providers")
            .select("*")
            .eq("health_status", health_status)
            .eq("is_active", True)
            .execute()
        )

        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching providers by health status {health_status}: {e}")
        return []


def get_providers_stats() -> dict[str, Any]:
    """
    Get overall statistics about providers

    Returns:
        Dictionary with provider statistics
    """
    try:
        supabase = get_supabase_client()

        # Get all providers
        all_response = supabase.table("providers").select("*").execute()
        all_providers = all_response.data or []

        # Count by status
        stats = {
            "total": len(all_providers),
            "active": len([p for p in all_providers if p.get("is_active")]),
            "inactive": len([p for p in all_providers if not p.get("is_active")]),
            "healthy": len([p for p in all_providers if p.get("health_status") == "healthy"]),
            "degraded": len([p for p in all_providers if p.get("health_status") == "degraded"]),
            "down": len([p for p in all_providers if p.get("health_status") == "down"]),
            "unknown": len([p for p in all_providers if p.get("health_status") == "unknown"]),
        }

        return stats
    except Exception as e:
        logger.error(f"Error fetching provider stats: {e}")
        return {
            "total": 0,
            "active": 0,
            "inactive": 0,
            "healthy": 0,
            "degraded": 0,
            "down": 0,
            "unknown": 0,
        }


def search_providers(query: str) -> list[dict[str, Any]]:
    """
    Search providers by name, slug, or description

    Args:
        query: Search query string

    Returns:
        List of matching provider dictionaries
    """
    try:
        supabase = get_supabase_client()

        # Search in name, slug, and description
        response = (
            supabase.table("providers")
            .select("*")
            .or_(f"name.ilike.%{query}%,slug.ilike.%{query}%,description.ilike.%{query}%")
            .execute()
        )

        return response.data or []
    except Exception as e:
        logger.error(f"Error searching providers with query '{query}': {e}")
        return []
