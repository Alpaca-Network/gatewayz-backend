"""
Database layer for models catalog management
Handles CRUD operations for AI models with provider relationships
"""

import logging
from typing import Any
from datetime import datetime, timezone
from decimal import Decimal

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
            .select("*, providers!inner(*)")
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
    Search models by name, model_id, or description with flexible matching.
    Handles variations in spacing, hyphens, and special characters.

    Examples:
        - "gpt 4" matches "gpt-4", "gpt4", "gpt-4o", "gpt 4 turbo"
        - "claude3" matches "claude-3", "claude 3", "claude-3-opus"

    Args:
        query: Search query string
        provider_id: Optional filter by provider ID

    Returns:
        List of matching model dictionaries
    """
    try:
        supabase = get_supabase_client()
        import re

        # Create multiple search variations to handle different separator styles
        # E.g., "gpt 4" will search for "gpt 4", "gpt-4", "gpt4", "gpt_4"
        search_variations = [query]

        # Create normalized version (no separators)
        normalized = re.sub(r'[\s\-_.]+', '', query)
        if normalized != query:
            search_variations.append(normalized)

        # Create hyphen version
        hyphenated = re.sub(r'[\s\-_.]+', '-', query)
        if hyphenated != query and hyphenated not in search_variations:
            search_variations.append(hyphenated)

        # Create space version
        spaced = re.sub(r'[\s\-_.]+', ' ', query)
        if spaced != query and spaced not in search_variations:
            search_variations.append(spaced)

        # Create underscore version
        underscored = re.sub(r'[\s\-_.]+', '_', query)
        if underscored != query and underscored not in search_variations:
            search_variations.append(underscored)

        # Build OR conditions for all variations across all searchable fields
        or_conditions = []
        for variant in search_variations:
            or_conditions.extend([
                f"model_name.ilike.*{variant}*",
                f"model_id.ilike.*{variant}*",
                f"description.ilike.*{variant}*"
            ])

        search_query = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .or_(','.join(or_conditions))
        )

        if provider_id:
            search_query = search_query.eq("provider_id", provider_id)

        response = search_query.execute()
        results = response.data or []

        # Remove duplicates (same model might match multiple variations)
        seen_ids = set()
        unique_results = []
        for result in results:
            model_id = result.get('id')
            if model_id not in seen_ids:
                seen_ids.add(model_id)
                unique_results.append(result)

        return unique_results
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


def bulk_upsert_models(models_data: list[dict[str, Any]], batch_size: int = 500) -> list[dict[str, Any]]:
    """
    Upsert multiple models at once with batching to handle large catalogs

    Args:
        models_data: List of model data dictionaries
        batch_size: Number of models to upsert per batch (default: 500)

    Returns:
        List of upserted model dictionaries
    """
    if not models_data:
        return []

    try:
        supabase = get_supabase_client()

        # Serialize Decimal objects to floats
        serialized_models = [_serialize_model_data(model) for model in models_data]

        # Process in batches to avoid payload size and timeout issues
        all_upserted = []
        total_batches = (len(serialized_models) + batch_size - 1) // batch_size

        for i in range(0, len(serialized_models), batch_size):
            batch = serialized_models[i:i + batch_size]
            batch_num = (i // batch_size) + 1

            try:
                logger.info(f"Upserting batch {batch_num}/{total_batches} ({len(batch)} models)")

                response = (
                    supabase.table("models")
                    .upsert(
                        batch,
                        on_conflict="provider_id,provider_model_id"
                    )
                    .execute()
                )

                if response.data:
                    all_upserted.extend(response.data)
                    logger.info(f"Successfully upserted batch {batch_num}/{total_batches} ({len(response.data)} models)")
                else:
                    logger.warning(f"Batch {batch_num}/{total_batches} returned no data")

            except Exception as batch_error:
                logger.error(f"Error upserting batch {batch_num}/{total_batches}: {batch_error}")
                # Continue with next batch instead of failing completely
                continue

        logger.info(f"Upserted {len(all_upserted)} total models across {total_batches} batches")
        return all_upserted

    except Exception as e:
        logger.error(f"Error bulk upserting models: {e}")
        return []
