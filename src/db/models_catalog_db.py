"""
Database layer for models catalog management
Handles CRUD operations for AI models with provider relationships
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.config.supabase_config import get_supabase_client, get_client_for_query

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
        # Use read replica for catalog queries (read-only)
        supabase = get_client_for_query(read_only=True)

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
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)
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
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)

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
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)
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
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)
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
    Search models by name, model_name, or description

    Args:
        query: Search query string
        provider_id: Optional filter by provider ID

    Returns:
        List of matching model dictionaries
    """
    try:
        supabase = get_supabase_client()

        # Search in model_name and description
        search_query = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .or_(f"model_name.ilike.%{query}%,description.ilike.%{query}%")
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
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)

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


def flush_models_table() -> dict[str, Any]:
    """
    Flush (delete all records from) the models table

    WARNING: This is a destructive operation. All model data will be deleted.
    Providers table is preserved.

    Returns:
        Dictionary with success status and count of deleted models
    """
    try:
        supabase = get_supabase_client()

        # Get count before deletion
        count_response = supabase.table("models").select("id", count="exact").execute()
        models_count = count_response.count or 0

        logger.warning(f"🗑️  Flushing models table - deleting {models_count} models")

        # Delete all models
        # Using neq (not equal) with a value that doesn't exist ensures we delete all rows
        delete_response = supabase.table("models").delete().neq("id", -1).execute()

        logger.info(f"✅ Flushed models table - deleted {models_count} models")

        return {
            "success": True,
            "deleted_count": models_count,
            "message": f"Successfully deleted {models_count} models"
        }
    except Exception as e:
        logger.error(f"❌ Error flushing models table: {e}")
        return {
            "success": False,
            "deleted_count": 0,
            "error": str(e),
            "message": f"Failed to flush models table: {e}"
        }


def flush_providers_table() -> dict[str, Any]:
    """
    Flush (delete all records from) the providers table

    WARNING: This is a HIGHLY destructive operation. All provider and model data will be deleted.
    Due to CASCADE constraint, deleting providers automatically deletes all associated models.

    Returns:
        Dictionary with success status and counts of deleted providers and models
    """
    try:
        supabase = get_supabase_client()

        # Get counts before deletion
        providers_response = supabase.table("providers").select("id", count="exact").execute()
        providers_count = providers_response.count or 0

        models_response = supabase.table("models").select("id", count="exact").execute()
        models_count = models_response.count or 0

        logger.warning(
            f"🗑️  Flushing providers table - deleting {providers_count} providers "
            f"and {models_count} models (CASCADE)"
        )

        # Delete all providers (CASCADE will delete all models)
        delete_response = supabase.table("providers").delete().neq("id", -1).execute()

        logger.info(
            f"✅ Flushed providers table - deleted {providers_count} providers "
            f"and {models_count} models"
        )

        return {
            "success": True,
            "deleted_providers_count": providers_count,
            "deleted_models_count": models_count,
            "message": f"Successfully deleted {providers_count} providers and {models_count} models"
        }
    except Exception as e:
        logger.error(f"❌ Error flushing providers table: {e}")
        return {
            "success": False,
            "deleted_providers_count": 0,
            "deleted_models_count": 0,
            "error": str(e),
            "message": f"Failed to flush providers table: {e}"
        }


# ============================================================================
# DATABASE-FIRST CATALOG FUNCTIONS (Issue #980)
# These functions are optimized for catalog building using database as the
# single source of truth, replacing direct provider API calls.
# ============================================================================


def get_all_models_for_catalog(
    include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Get ALL models from database optimized for catalog building.

    This function is designed to replace direct provider API calls in catalog
    endpoints. It fetches all models with provider information using pagination
    to handle datasets larger than Supabase's default 1000-row limit.

    Key differences from get_all_models():
    - Uses pagination to fetch ALL models (no 1000-row limit)
    - No artificial limit (catalog needs complete dataset)
    - Optimized for full catalog building

    Implementation note:
    - Supabase (PostgREST) has a default page size of 1000 rows
    - We use chunked fetching with .range() to get all rows
    - For 11k+ models, this makes 11-12 queries instead of 1, but ensures completeness

    Args:
        include_inactive: Include inactive models (default: False)

    Returns:
        List of model dictionaries with provider information

    Example:
        >>> models = get_all_models_for_catalog()
        >>> len(models)
        11432  # All active models from all providers (not limited to 1000)
    """
    try:
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)
        all_models = []
        page_size = 1000  # Supabase default limit
        offset = 0

        logger.info(f"Fetching all models from database (include_inactive={include_inactive})...")

        while True:
            # Build query with pagination - join with providers table
            query = (
                supabase.table("models")
                .select("*, providers!inner(*)")
            )

            # Filter by active status
            if not include_inactive:
                query = query.eq("is_active", True)

            # Order by model name for consistent output and pagination
            query = query.order("model_name")

            # Apply pagination using range
            query = query.range(offset, offset + page_size - 1)

            # Execute query
            response = query.execute()
            batch = response.data or []

            if not batch:
                # No more results, we're done
                break

            all_models.extend(batch)
            logger.debug(f"Fetched batch of {len(batch)} models (offset={offset}, total so far={len(all_models)})")

            # If we got fewer than page_size rows, we've reached the end
            if len(batch) < page_size:
                break

            # Move to next page
            offset += page_size

        logger.info(
            f"Fetched {len(all_models)} models from database for catalog "
            f"({(offset // page_size) + 1} batches, page_size={page_size})"
        )

        return all_models

    except Exception as e:
        logger.error(f"Error fetching all models for catalog: {e}")
        return []


def get_models_by_gateway_for_catalog(
    gateway_slug: str,
    include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Get all models for a specific gateway/provider optimized for catalog.

    This function is designed to replace direct provider API calls when
    building a single-provider catalog. Uses pagination to handle providers
    with more than 1000 models.

    Args:
        gateway_slug: Gateway/provider slug (e.g., 'openrouter', 'anthropic')
        include_inactive: Include inactive models (default: False)

    Returns:
        List of model dictionaries for the specified gateway

    Example:
        >>> models = get_models_by_gateway_for_catalog('openrouter')
        >>> len(models)
        2834  # All OpenRouter models (not limited to 1000)
    """
    try:
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)
        all_models = []
        page_size = 1000  # Supabase default limit
        offset = 0

        logger.info(f"Fetching models for gateway: {gateway_slug} (include_inactive={include_inactive})...")

        while True:
            # Build query with provider filter
            query = (
                supabase.table("models")
                .select("*, providers!inner(*)")
                .eq("providers.slug", gateway_slug)
            )

            # Filter by active status
            if not include_inactive:
                query = query.eq("is_active", True)

            # Order by model name for consistent pagination
            query = query.order("model_name")

            # Apply pagination
            query = query.range(offset, offset + page_size - 1)

            # Execute query
            response = query.execute()
            batch = response.data or []

            if not batch:
                # No more results
                break

            all_models.extend(batch)
            logger.debug(f"Fetched batch of {len(batch)} models for {gateway_slug} (offset={offset}, total so far={len(all_models)})")

            # If we got fewer than page_size rows, we've reached the end
            if len(batch) < page_size:
                break

            # Move to next page
            offset += page_size

        logger.info(
            f"Fetched {len(all_models)} models from database for gateway: {gateway_slug} "
            f"({(offset // page_size) + 1} batches)"
        )

        return all_models

    except Exception as e:
        logger.error(f"Error fetching models for gateway {gateway_slug}: {e}")
        return []


def get_model_by_model_name_string(
    model_name: str,
    provider_slug: str | None = None
) -> dict[str, Any] | None:
    """
    Get a model by its model_name string (not integer primary key).

    This is useful for looking up models by their API-facing model name
    (e.g., "gpt-4", "claude-3-opus") rather than the database primary key.

    Args:
        model_name: The model's string identifier (model_name field)
        provider_slug: Optional provider slug to narrow search

    Returns:
        Model dictionary or None if not found

    Example:
        >>> model = get_model_by_model_name_string("gpt-4")
        >>> model["model_name"]
        "GPT-4"
    """
    try:
        supabase = get_supabase_client()

        query = (
            supabase.table("models")
            .select("*, providers!inner(*)")
            .eq("model_name", model_name)
        )

        # Optionally filter by provider
        if provider_slug:
            query = query.eq("providers.slug", provider_slug)

        response = query.single().execute()
        return response.data
    except Exception as e:
        logger.debug(f"Model {model_name} not found in database: {e}")
        return None


def transform_db_model_to_api_format(db_model: dict[str, Any]) -> dict[str, Any]:
    """
    Transform database model format to API response format.

    Database models have the schema from the `models` table, but API responses
    expect a different structure. This function handles that conversion.

    Database format:
    - id (int) - primary key
    - model_name (str) - the API-facing identifier and display name
    - provider_id (int) - foreign key
    - providers (dict) - joined provider data
    - pricing_prompt (decimal) - per-token input cost
    - pricing_completion (decimal) - per-token output cost
    - context_length (int)
    - is_active (bool)
    - metadata (jsonb)
    - etc.

    API format:
    - id (str) - the model_name (not the DB primary key!)
    - name (str) - display name
    - source_gateway (str) - provider slug
    - provider_slug (str) - provider slug
    - context_length (int)
    - pricing (dict) - with prompt/completion keys
    - etc.

    Args:
        db_model: Model dictionary from database

    Returns:
        Model dictionary in API format

    Example:
        >>> db_model = get_model_by_model_name_string("gpt-4")
        >>> api_model = transform_db_model_to_api_format(db_model)
        >>> api_model["id"]
        "gpt-4"  # model_name, not primary key
        >>> api_model["source_gateway"]
        "openai"
    """
    try:
        # Extract provider info from joined data
        provider = db_model.get("providers", {})
        provider_slug = provider.get("slug", "unknown")

        # Build pricing dict from database pricing fields
        pricing = {}
        if db_model.get("pricing_prompt") is not None:
            pricing["prompt"] = str(db_model["pricing_prompt"])
        if db_model.get("pricing_completion") is not None:
            pricing["completion"] = str(db_model["pricing_completion"])

        # Build API format model
        api_model = {
            # Use model_name as the API-facing id (not the DB primary key)
            "id": db_model.get("model_name", ""),
            "name": db_model.get("model_name", ""),
            "source_gateway": provider_slug,
            "provider_slug": provider_slug,
            "context_length": db_model.get("context_length"),
            "pricing": pricing if pricing else None,
            "description": db_model.get("description"),
            "modality": db_model.get("modality"),
            "is_active": db_model.get("is_active", True),
            "health_status": db_model.get("health_status"),
            # Include provider URLs from the joined providers table
            "provider_site_url": provider.get("site_url"),
            "model_logo_url": provider.get("logo_url"),
        }

        # Include metadata if present
        if db_model.get("metadata"):
            api_model["metadata"] = db_model["metadata"]

        # Include additional fields that might be useful
        if db_model.get("average_response_time_ms"):
            api_model["average_response_time_ms"] = db_model["average_response_time_ms"]

        # Serialize any Decimal values
        api_model = _serialize_model_data(api_model)

        return api_model
    except Exception as e:
        logger.error(f"Error transforming DB model to API format: {e}")
        # Return a minimal model on error to avoid breaking the catalog
        return {
            "id": db_model.get("model_name", "unknown"),
            "name": db_model.get("model_name", "Unknown Model"),
            "source_gateway": "unknown",
            "provider_slug": "unknown",
        }


# ============================================================================
# ENHANCED CATALOG FUNCTIONS (Phase 1 - Issue #990)
# Additional query capabilities for DB-first architecture
# ============================================================================


def get_models_for_catalog_with_filters(
    gateway_slug: str | None = None,
    modality: str | None = None,
    search_query: str | None = None,
    include_inactive: bool = False,
    limit: int | None = None,
    offset: int = 0
) -> list[dict[str, Any]]:
    """
    Get models from database with advanced filtering for catalog endpoints.

    This is the main query function for DB-first catalog architecture,
    supporting all common filtering patterns.

    Args:
        gateway_slug: Filter by provider (e.g., 'openrouter', 'anthropic')
        modality: Filter by modality (e.g., 'text->text', 'text->image')
        search_query: Search in model name, ID, or description
        include_inactive: Include inactive models (default: False)
        limit: Maximum number of results (None = unlimited)
        offset: Offset for pagination (default: 0)

    Returns:
        List of model dictionaries with provider information

    Example:
        >>> # Get all text models from OpenRouter
        >>> models = get_models_for_catalog_with_filters(
        ...     gateway_slug='openrouter',
        ...     modality='text->text',
        ...     limit=100
        ... )
        >>> len(models)
        100

        >>> # Search for GPT models across all providers
        >>> models = get_models_for_catalog_with_filters(
        ...     search_query='gpt-4'
        ... )
    """
    try:
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)

        # Build base query with provider join
        query = (
            supabase.table("models")
            .select("*, providers!inner(*)")
        )

        # Apply filters
        if not include_inactive:
            query = query.eq("is_active", True)

        if gateway_slug:
            query = query.eq("providers.slug", gateway_slug)

        if modality:
            query = query.eq("modality", modality)

        if search_query:
            # Search in model_name or description
            search_pattern = f"%{search_query}%"
            query = query.or_(
                f"model_name.ilike.{search_pattern},"
                f"description.ilike.{search_pattern}"
            )

        # Order by model name for consistent results
        query = query.order("model_name")

        # Apply pagination
        if limit is not None:
            query = query.range(offset, offset + limit - 1)

        response = query.execute()
        models = response.data or []

        logger.info(
            f"Fetched {len(models)} models from database "
            f"(gateway={gateway_slug}, modality={modality}, "
            f"search={search_query}, limit={limit}, offset={offset})"
        )

        return models

    except Exception as e:
        logger.error(f"Error fetching models with filters: {e}")
        return []


def get_models_count_by_filters(
    gateway_slug: str | None = None,
    modality: str | None = None,
    search_query: str | None = None,
    include_inactive: bool = False
) -> int:
    """
    Get count of models matching filters (for pagination).

    Args:
        gateway_slug: Filter by provider
        modality: Filter by modality
        search_query: Search query
        include_inactive: Include inactive models

    Returns:
        Total count of matching models

    Example:
        >>> count = get_models_count_by_filters(gateway_slug='openrouter')
        >>> print(f"OpenRouter has {count} models")
    """
    try:
        # Use read replica for read-only catalog queries
        supabase = get_client_for_query(read_only=True)

        # Build query (same filters as get_models_for_catalog_with_filters)
        query = supabase.table("models").select("id", count="exact")

        if not include_inactive:
            query = query.eq("is_active", True)

        if gateway_slug:
            # Need to join with providers for slug filter
            query = (
                supabase.table("models")
                .select("id, providers!inner(slug)", count="exact")
                .eq("providers.slug", gateway_slug)
            )
            if not include_inactive:
                query = query.eq("is_active", True)

        if modality:
            query = query.eq("modality", modality)

        if search_query:
            search_pattern = f"%{search_query}%"
            query = query.or_(
                f"model_name.ilike.{search_pattern},"
                f"description.ilike.{search_pattern}"
            )

        response = query.execute()
        return response.count or 0

    except Exception as e:
        logger.error(f"Error counting models: {e}")
        return 0


def transform_db_models_batch(
    db_models: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Transform multiple database models to API format efficiently.

    This is a convenience function for batch transformations,
    used by catalog endpoints to convert DB results.

    Args:
        db_models: List of model dictionaries from database

    Returns:
        List of models in API format

    Example:
        >>> db_models = get_all_models_for_catalog()
        >>> api_models = transform_db_models_batch(db_models)
        >>> len(api_models) == len(db_models)
        True
    """
    return [transform_db_model_to_api_format(model) for model in db_models]


def get_catalog_statistics() -> dict[str, Any]:
    """
    Get statistics about the model catalog in the database.

    Useful for monitoring and health checks.

    Returns:
        Dictionary with catalog statistics

    Example:
        >>> stats = get_catalog_statistics()
        >>> print(f"Total models: {stats['total_models']}")
        >>> print(f"Providers: {stats['total_providers']}")
    """
    try:
        supabase = get_supabase_client()

        # Get total model count
        models_response = (
            supabase.table("models")
            .select("id", count="exact")
            .eq("is_active", True)
            .execute()
        )
        total_models = models_response.count or 0

        # Get provider count
        providers_response = (
            supabase.table("providers")
            .select("id", count="exact")
            .eq("is_active", True)
            .execute()
        )
        total_providers = providers_response.count or 0

        # Get count by modality
        modalities_response = (
            supabase.table("models")
            .select("modality")
            .eq("is_active", True)
            .execute()
        )

        modality_counts = {}
        for model in modalities_response.data or []:
            modality = model.get("modality", "unknown")
            modality_counts[modality] = modality_counts.get(modality, 0) + 1

        # Get models per provider
        provider_models_response = (
            supabase.table("models")
            .select("provider_id, providers!inner(slug)")
            .eq("is_active", True)
            .execute()
        )

        provider_counts = {}
        for model in provider_models_response.data or []:
            provider = model.get("providers", {})
            slug = provider.get("slug", "unknown")
            provider_counts[slug] = provider_counts.get(slug, 0) + 1

        return {
            "total_models": total_models,
            "total_providers": total_providers,
            "models_by_modality": modality_counts,
            "models_by_provider": provider_counts,
            "top_providers": sorted(
                provider_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]
        }

    except Exception as e:
        logger.error(f"Error fetching catalog statistics: {e}")
        return {
            "total_models": 0,
            "total_providers": 0,
            "models_by_modality": {},
            "models_by_provider": {},
            "top_providers": []
        }


def get_all_unique_models_for_catalog(
    include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Fetch unique models with provider relationships from the database.

    OPTIMIZED VERSION: Uses a single query to fetch all data instead of N+1 queries.
    This dramatically improves performance from 10-30s to under 1s for 500+ models.

    Previous issue (N+1 query problem):
    - Made 1 query to fetch unique models
    - Made N additional queries (one per unique model) to fetch providers
    - For 500 models: 501 total queries, taking 10-30+ seconds
    - Caused 499 errors (client timeout) in production

    Current optimization:
    - Makes 2 queries total (unique_models + all provider mappings)
    - Groups results in Python memory (fast)
    - Executes in <1s even for 1000+ models

    Args:
        include_inactive: If True, includes inactive models. Default: False.

    Returns:
        List of unique models with aggregated provider information:
        [
            {
                'unique_model_id': 123,
                'model_name': 'GPT-4',
                'model_count': 3,
                'sample_model_id': 'openai/gpt-4',
                'providers': [
                    {
                        'provider_id': 1,
                        'provider_slug': 'openrouter',
                        'provider_name': 'OpenRouter',
                        'model_id': 456,
                        'model_api_id': 'openai/gpt-4',
                        'provider_model_id': 'openai/gpt-4',
                        'pricing_prompt': 0.03,
                        'pricing_completion': 0.06,
                        'pricing_image': 0,
                        'pricing_request': 0,
                        'context_length': 8192,
                        'health_status': 'healthy',
                        'average_response_time_ms': 1200,
                        'modality': 'text->text',
                        'supports_streaming': True,
                        'supports_function_calling': True,
                        'supports_vision': False
                    },
                    ...
                ]
            }
        ]
    """
    from src.config.supabase_config import get_supabase_client
    import time
    from collections import defaultdict

    supabase = get_supabase_client()

    try:
        start_time = time.time()
        logger.info(f"Fetching unique models (include_inactive={include_inactive})")

        # OPTIMIZATION: Fetch all unique models in one query
        um_query = supabase.table("unique_models").select("*")
        um_response = um_query.execute()
        unique_models_data = um_response.data or []

        if not unique_models_data:
            logger.info("No unique models found in database")
            return []

        # OPTIMIZATION: Fetch ALL provider mappings in a SINGLE query
        # This replaces N individual queries (one per unique model)
        ump_query = (
            supabase.table("unique_models_provider")
            .select("*, models!inner(*), providers!inner(*)")
        )

        if not include_inactive:
            ump_query = ump_query.eq("models.is_active", True)

        ump_response = ump_query.execute()
        all_provider_mappings = ump_response.data or []

        # OPTIMIZATION: Group provider mappings by unique_model_id in memory
        # This is MUCH faster than N database queries
        providers_by_unique_model = defaultdict(list)

        for ump in all_provider_mappings:
            unique_model_id = ump.get("unique_model_id")
            if not unique_model_id:
                continue

            model = ump.get("models", {})
            provider = ump.get("providers", {})

            provider_data = {
                'provider_id': provider.get('id'),
                'provider_slug': provider.get('slug'),
                'provider_name': provider.get('name'),
                'model_id': model.get('id'),
                'model_api_name': model.get('model_name'),
                'provider_model_id': model.get('provider_model_id'),
                'pricing_prompt': model.get('pricing_prompt'),
                'pricing_completion': model.get('pricing_completion'),
                'pricing_image': model.get('pricing_image'),
                'pricing_request': model.get('pricing_request'),
                'context_length': model.get('context_length'),
                'health_status': model.get('health_status'),
                'average_response_time_ms': model.get('average_response_time_ms'),
                'modality': model.get('modality'),
                'supports_streaming': model.get('supports_streaming'),
                'supports_function_calling': model.get('supports_function_calling'),
                'supports_vision': model.get('supports_vision'),
                'description': model.get('description'),
                'architecture': model.get('architecture')
            }

            providers_by_unique_model[unique_model_id].append(provider_data)

        # Build final result by combining unique models with their providers
        result = []
        for um in unique_models_data:
            unique_model_id = um['id']
            providers = providers_by_unique_model.get(unique_model_id, [])

            # Only include models that have at least one provider
            if providers:
                # Sort providers by price (cheapest first)
                providers.sort(
                    key=lambda p: p['pricing_prompt'] if p['pricing_prompt'] is not None else float('inf')
                )

                result.append({
                    'unique_model_id': unique_model_id,
                    'model_name': um['model_name'],
                    'model_count': um['model_count'],
                    'sample_model_id': um['sample_model_id'],
                    'providers': providers
                })

        query_time = time.time() - start_time
        logger.info(
            f"Fetched {len(result)} unique models with {len(all_provider_mappings)} "
            f"provider mappings in {query_time:.2f}s (OPTIMIZED: 2 queries instead of N+1)"
        )

        if query_time > 1.0:
            logger.warning(
                f"Slow unique models query: {query_time:.2f}s "
                f"(threshold: 1.0s, include_inactive={include_inactive})"
            )

        return result

    except Exception as e:
        logger.error(f"Error fetching unique models: {e}")
        return []


def transform_unique_model_to_api_format(db_model: dict[str, Any]) -> dict[str, Any]:
    """
    Transform unique_models database record to API format.

    Args:
        db_model: Database record from get_all_unique_models_for_catalog()

    Returns:
        API-formatted model with provider array:
        {
            'id': 'gpt-4',
            'name': 'GPT-4',
            'providers': [
                {
                    'slug': 'openrouter',
                    'name': 'OpenRouter',
                    'pricing': {
                        'prompt': '0.03',
                        'completion': '0.06',
                        'image': '0',
                        'request': '0'
                    },
                    'context_length': 8192,
                    'health_status': 'healthy',
                    'average_response_time_ms': 1200,
                    'modality': 'text->text',
                    'supports_streaming': True,
                    'supports_function_calling': True,
                    'supports_vision': False
                },
                ...
            ],
            'provider_count': 3,
            'cheapest_provider': 'groq',
            'fastest_provider': 'openrouter',
            'cheapest_prompt_price': 0.025,
            'fastest_response_time': 950
        }
    """
    from decimal import Decimal

    try:
        providers_data = db_model.get('providers', [])

        # Transform provider data
        transformed_providers = []
        for provider in providers_data:
            # Format pricing
            pricing_prompt = provider.get('pricing_prompt')
            pricing_completion = provider.get('pricing_completion')
            pricing_image = provider.get('pricing_image')
            pricing_request = provider.get('pricing_request')

            # Convert Decimal to string for JSON serialization
            def format_price(price):
                if price is None:
                    return "0"
                if isinstance(price, Decimal):
                    return str(price)
                return str(price)

            transformed_provider = {
                'slug': provider.get('provider_slug', 'unknown'),
                'name': provider.get('provider_name', 'Unknown'),
                'pricing': {
                    'prompt': format_price(pricing_prompt),
                    'completion': format_price(pricing_completion),
                    'image': format_price(pricing_image),
                    'request': format_price(pricing_request)
                },
                'context_length': provider.get('context_length'),
                'health_status': provider.get('health_status', 'unknown'),
                'average_response_time_ms': provider.get('average_response_time_ms'),
                'modality': provider.get('modality', 'text->text'),
                'supports_streaming': provider.get('supports_streaming', False),
                'supports_function_calling': provider.get('supports_function_calling', False),
                'supports_vision': provider.get('supports_vision', False),
                'description': provider.get('description'),
                'architecture': provider.get('architecture'),
                'model_name': provider.get('model_api_name')  # Include original model_name
            }

            transformed_providers.append(transformed_provider)

        # Calculate cheapest provider
        cheapest_provider = None
        cheapest_prompt_price = None
        for provider in providers_data:
            price = provider.get('pricing_prompt')
            if price is not None:
                if cheapest_prompt_price is None or price < cheapest_prompt_price:
                    cheapest_prompt_price = price
                    cheapest_provider = provider.get('provider_slug')

        # Calculate fastest provider
        fastest_provider = None
        fastest_response_time = None
        for provider in providers_data:
            response_time = provider.get('average_response_time_ms')
            if response_time is not None:
                if fastest_response_time is None or response_time < fastest_response_time:
                    fastest_response_time = response_time
                    fastest_provider = provider.get('provider_slug')

        # Generate normalized model ID (use sample_model_id or model_name as fallback)
        model_id = db_model.get('sample_model_id') or db_model.get('model_name', 'unknown')

        # Try to normalize the ID (remove provider prefix if exists)
        if '/' in model_id:
            # Format like "openai/gpt-4" -> "gpt-4"
            model_id = model_id.split('/')[-1]

        api_model = {
            'id': model_id,
            'name': db_model.get('model_name', 'Unknown'),
            'providers': transformed_providers,
            'provider_count': len(transformed_providers),
            'cheapest_provider': cheapest_provider,
            'fastest_provider': fastest_provider,
            'cheapest_prompt_price': float(cheapest_prompt_price) if cheapest_prompt_price is not None else None,
            'fastest_response_time': fastest_response_time
        }

        return api_model

    except Exception as e:
        logger.error(f"Error transforming unique model to API format: {e}")
        # Return minimal valid structure
        return {
            'id': db_model.get('sample_model_id', 'unknown'),
            'name': db_model.get('model_name', 'Unknown'),
            'providers': [],
            'provider_count': 0,
            'cheapest_provider': None,
            'fastest_provider': None,
            'cheapest_prompt_price': None,
            'fastest_response_time': None
        }


def transform_unique_models_batch(db_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Batch transform unique models from database format to API format.

    Args:
        db_models: List of database records from get_all_unique_models_for_catalog()

    Returns:
        List of API-formatted models
    """
    return [transform_unique_model_to_api_format(model) for model in db_models]
