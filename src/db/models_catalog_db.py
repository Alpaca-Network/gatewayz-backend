"""
Database layer for models catalog management
Handles CRUD operations for AI models with provider relationships
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def _serialize_model_data(data: dict[str, Any]) -> dict[str, Any]:
    """Convert Decimal and other non-JSON-serializable types to JSON-compatible types"""
    serialized = {}
    for key, value in data.items():
        if isinstance(value, Decimal):
            serialized[key] = float(value)
        elif isinstance(value, dict):
            serialized[key] = _serialize_model_data(value)
        elif isinstance(value, list):
            serialized[key] = [_serialize_model_data(item) if isinstance(item, dict) else item for item in value]
        else:
            serialized[key] = value
    return serialized


def get_all_models(
    provider_id: int | None = None,
    is_active_only: bool = True,
    limit: int = 100,
    offset: int = 0
) -> list[dict[str, Any]]:
    """
    Get all models from database

    Args:
        provider_id: Optional filter by provider ID
        is_active_only: Only return active models
        limit: Maximum number of results
        offset: Offset for pagination

    Returns:
        List of model dictionaries with provider information
    """
    try:
        supabase = get_supabase_client()

        # Join with providers table to get provider info
        query = (
            supabase.table("models")
            .select("*, providers!inner(*)")
        )

        if provider_id:
            query = query.eq("provider_id", provider_id)

        if is_active_only:
            query = query.eq("is_active", True)

        query = query.order("model_name").range(offset, offset + limit - 1)
        response = query.execute()

        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        return []


def get_model_by_id(model_id: int) -> dict[str, Any] | None:
    """
    Get a model by its ID

    Args:
        model_id: Model ID

    Returns:
        Model dictionary with provider information or None
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .eq("id", model_id)
            .single()
            .execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"Error fetching model {model_id}: {e}")
        return None


def get_models_by_provider_slug(
    provider_slug: str,
    is_active_only: bool = True
) -> list[dict[str, Any]]:
    """
    Get all models for a specific provider by slug

    Args:
        provider_slug: Provider slug (e.g., 'openrouter', 'portkey')
        is_active_only: Only return active models

    Returns:
        List of model dictionaries
    """
    try:
        supabase = get_supabase_client()

        query = (
            supabase.table("models")
            .select("*, providers!inner(*), model_pricing(price_per_input_token, price_per_output_token)")
            .eq("providers.slug", provider_slug)
        )

        if is_active_only:
            query = query.eq("is_active", True)

        query = query.order("model_name")
        response = query.execute()

        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching models for provider {provider_slug}: {e}")
        return []


def get_model_by_provider_and_model_id(
    provider_id: int,
    provider_model_id: str
) -> dict[str, Any] | None:
    """
    Get a model by provider ID and provider-specific model ID

    Args:
        provider_id: Provider ID
        provider_model_id: Provider-specific model identifier

    Returns:
        Model dictionary or None
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .eq("provider_id", provider_id)
            .eq("provider_model_id", provider_model_id)
            .single()
            .execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"Error fetching model {provider_model_id} for provider {provider_id}: {e}")
        return None


def create_model(model_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Create a new model

    Args:
        model_data: Model data dictionary

    Returns:
        Created model dictionary or None
    """
    try:
        supabase = get_supabase_client()
        serialized_data = _serialize_model_data(model_data)
        response = supabase.table("models").insert(serialized_data).execute()

        if response.data:
            logger.info(f"Created model: {model_data.get('model_name')}")
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Error creating model: {e}")
        return None


def bulk_create_models(models_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Create multiple models at once

    Args:
        models_data: List of model data dictionaries

    Returns:
        List of created model dictionaries
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("models").insert(models_data).execute()

        if response.data:
            logger.info(f"Created {len(response.data)} models")
            return response.data
        return []
    except Exception as e:
        logger.error(f"Error bulk creating models: {e}")
        return []


def update_model(model_id: int, model_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Update a model

    Args:
        model_id: Model ID
        model_data: Updated model data

    Returns:
        Updated model dictionary or None
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("models")
            .update(model_data)
            .eq("id", model_id)
            .execute()
        )

        if response.data:
            logger.info(f"Updated model {model_id}")
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Error updating model {model_id}: {e}")
        return None


def delete_model(model_id: int) -> bool:
    """
    Delete a model (hard delete)

    Args:
        model_id: Model ID

    Returns:
        True if successful, False otherwise
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("models").delete().eq("id", model_id).execute()

        if response.data:
            logger.info(f"Deleted model {model_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error deleting model {model_id}: {e}")
        return False


def deactivate_model(model_id: int) -> dict[str, Any] | None:
    """
    Deactivate a model (soft delete)

    Args:
        model_id: Model ID

    Returns:
        Updated model dictionary or None
    """
    return update_model(model_id, {"is_active": False})


def activate_model(model_id: int) -> dict[str, Any] | None:
    """
    Activate a model

    Args:
        model_id: Model ID

    Returns:
        Updated model dictionary or None
    """
    return update_model(model_id, {"is_active": True})


def update_model_health(
    model_id: int,
    health_status: str,
    response_time_ms: int | None = None,
    error_message: str | None = None
) -> dict[str, Any] | None:
    """
    Update model health status and log to history

    Args:
        model_id: Model ID
        health_status: Health status ('healthy', 'degraded', 'down', 'unknown')
        response_time_ms: Response time in milliseconds
        error_message: Optional error message

    Returns:
        Updated model dictionary or None
    """
    try:
        supabase = get_supabase_client()

        # Update model health
        update_data = {
            "health_status": health_status,
            "last_health_check_at": datetime.now(timezone.utc).isoformat(),
        }

        if response_time_ms is not None:
            update_data["average_response_time_ms"] = response_time_ms

        model = update_model(model_id, update_data)

        # Log health check to history
        history_data = {
            "model_id": model_id,
            "health_status": health_status,
            "response_time_ms": response_time_ms,
            "error_message": error_message,
        }

        supabase.table("model_health_history").insert(history_data).execute()

        return model
    except Exception as e:
        logger.error(f"Error updating model health {model_id}: {e}")
        return None


def get_model_health_history(
    model_id: int,
    limit: int = 100
) -> list[dict[str, Any]]:
    """
    Get health check history for a model

    Args:
        model_id: Model ID
        limit: Maximum number of records to return

    Returns:
        List of health check records
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("model_health_history")
            .select("*")
            .eq("model_id", model_id)
            .order("checked_at", desc=True)
            .limit(limit)
            .execute()
        )

        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching health history for model {model_id}: {e}")
        return []


def get_models_by_health_status(health_status: str) -> list[dict[str, Any]]:
    """
    Get models by health status

    Args:
        health_status: Health status to filter by

    Returns:
        List of model dictionaries
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .eq("health_status", health_status)
            .eq("is_active", True)
            .execute()
        )

        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching models by health status {health_status}: {e}")
        return []


def search_models(query: str, provider_id: int | None = None) -> list[dict[str, Any]]:
    """
    Search models by name, model_id, or description

    Args:
        query: Search query string
        provider_id: Optional filter by provider ID

    Returns:
        List of matching model dictionaries
    """
    try:
        supabase = get_supabase_client()

        # Search in model_name, model_id, and description
        search_query = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .or_(f"model_name.ilike.%{query}%,model_id.ilike.%{query}%,description.ilike.%{query}%")
        )

        if provider_id:
            search_query = search_query.eq("provider_id", provider_id)

        response = search_query.execute()
        return response.data or []
    except Exception as e:
        logger.error(f"Error searching models with query '{query}': {e}")
        return []


def get_models_stats(provider_id: int | None = None) -> dict[str, Any]:
    """
    Get overall statistics about models

    Args:
        provider_id: Optional filter by provider ID

    Returns:
        Dictionary with model statistics
    """
    try:
        supabase = get_supabase_client()

        # Get all models (with optional provider filter)
        query = supabase.table("models").select("*")
        if provider_id:
            query = query.eq("provider_id", provider_id)

        all_response = query.execute()
        all_models = all_response.data or []

        # Count by status
        stats = {
            "total": len(all_models),
            "active": len([m for m in all_models if m.get("is_active")]),
            "inactive": len([m for m in all_models if not m.get("is_active")]),
            "healthy": len([m for m in all_models if m.get("health_status") == "healthy"]),
            "degraded": len([m for m in all_models if m.get("health_status") == "degraded"]),
            "down": len([m for m in all_models if m.get("health_status") == "down"]),
            "unknown": len([m for m in all_models if m.get("health_status") == "unknown"]),
        }

        # Count by modality
        modalities = {}
        for model in all_models:
            modality = model.get("modality", "unknown")
            modalities[modality] = modalities.get(modality, 0) + 1

        stats["by_modality"] = modalities

        return stats
    except Exception as e:
        logger.error(f"Error fetching model stats: {e}")
        return {
            "total": 0,
            "active": 0,
            "inactive": 0,
            "healthy": 0,
            "degraded": 0,
            "down": 0,
            "unknown": 0,
            "by_modality": {},
        }


def upsert_model(model_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Upsert a model (insert or update if exists)
    Uses provider_id and provider_model_id as unique constraint

    Args:
        model_data: Model data dictionary

    Returns:
        Upserted model dictionary or None
    """
    try:
        supabase = get_supabase_client()
        serialized_data = _serialize_model_data(model_data)
        response = (
            supabase.table("models")
            .upsert(
                serialized_data,
                on_conflict="provider_id,provider_model_id"
            )
            .execute()
        )

        if response.data:
            logger.info(f"Upserted model: {model_data.get('model_name')}")
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Error upserting model: {e}")
        return None


def bulk_upsert_models(models_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Upsert multiple models at once

    Args:
        models_data: List of model data dictionaries

    Returns:
        List of upserted model dictionaries
    """
    try:
        supabase = get_supabase_client()

        # Serialize Decimal objects to floats
        serialized_models = [_serialize_model_data(model) for model in models_data]

        response = (
            supabase.table("models")
            .upsert(
                serialized_models,
                on_conflict="provider_id,provider_model_id"
            )
            .execute()
        )

        if response.data:
            logger.info(f"Upserted {len(response.data)} models")
            return response.data
        return []
    except Exception as e:
        logger.error(f"Error bulk upserting models: {e}")
        return []
