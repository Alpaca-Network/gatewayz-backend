"""
API routes for models catalog management
Handles CRUD operations for AI models with provider relationships
"""

import logging

from fastapi import APIRouter, HTTPException, Path, Query, status

from src.db.models_catalog_db import (
    activate_model,
    bulk_create_models,
    bulk_upsert_models,
    create_model,
    deactivate_model,
    delete_model,
    get_all_models,
    get_model_by_id,
    get_model_by_provider_and_model_id,
    get_model_health_history,
    get_models_by_health_status,
    get_models_by_provider_slug,
    get_models_stats,
    search_models,
    update_model,
    update_model_health,
    upsert_model,
)
from src.db.providers_db import get_provider_by_id, get_provider_by_slug
from src.schemas.models_catalog import (
    ModelBulkCreate,
    ModelCreate,
    ModelHealthHistoryResponse,
    ModelHealthUpdate,
    ModelResponse,
    ModelStats,
    ModelUpdate,
    ModelWithProvider,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/catalog/models-db",
    tags=["Models Catalog Management"],
)


@router.get("/", response_model=list[ModelWithProvider])
async def list_models(
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    provider_slug: str | None = Query(None, description="Filter by provider slug"),
    is_active_only: bool = Query(True, description="Only return active models"),
    health_status: str | None = Query(None, description="Filter by health status"),
    modality: str | None = Query(None, description="Filter by modality"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Get all models with optional filters

    Returns list of all models with their provider information
    """
    try:
        # If provider_slug is provided, resolve to provider_id
        if provider_slug:
            provider = get_provider_by_slug(provider_slug)
            if not provider:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Provider with slug '{provider_slug}' not found",
                )
            provider_id = provider["id"]

        models = get_all_models(
            provider_id=provider_id, is_active_only=is_active_only, limit=limit, offset=offset
        )

        # Apply additional filters if needed
        if health_status:
            models = [m for m in models if m.get("health_status") == health_status]

        if modality:
            models = [m for m in models if m.get("modality") == modality]

        return models
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch models"
        )


@router.get("/stats", response_model=ModelStats)
async def get_model_statistics(
    provider_id: int | None = Query(None, description="Filter by provider ID"),
):
    """
    Get model statistics

    Returns overall statistics about models including:
    - Total count
    - Active/inactive counts
    - Health status distribution
    - Count by modality
    """
    try:
        stats = get_models_stats(provider_id=provider_id)
        return stats
    except Exception as e:
        logger.error(f"Error fetching model stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch model statistics",
        )


@router.get("/search", response_model=list[ModelWithProvider])
async def search_models_endpoint(
    q: str = Query(..., min_length=1, description="Search query"),
    provider_id: int | None = Query(None, description="Optional provider filter"),
):
    """
    Search models by name, model_id, or description

    Args:
        q: Search query string
        provider_id: Optional provider filter

    Returns list of matching models
    """
    try:
        models = search_models(q, provider_id)
        return models
    except Exception as e:
        logger.error(f"Error searching models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to search models"
        )


@router.get("/health/{health_status}", response_model=list[ModelWithProvider])
async def get_models_by_health(
    health_status: str = Path(
        ..., description="Health status: 'healthy', 'degraded', 'down', 'unknown'"
    ),
):
    """
    Get models by health status

    Args:
        health_status: Health status to filter by

    Returns list of models with specified health status
    """
    try:
        if health_status not in ["healthy", "degraded", "down", "unknown"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid health status. Must be: 'healthy', 'degraded', 'down', or 'unknown'",
            )

        models = get_models_by_health_status(health_status)
        return models
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching models by health status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch models by health status",
        )


@router.get("/provider/{provider_slug}", response_model=list[ModelWithProvider])
async def get_models_by_provider(
    provider_slug: str,
    is_active_only: bool = Query(True, description="Only return active models"),
):
    """
    Get all models for a specific provider

    Args:
        provider_slug: Provider slug (e.g., 'openrouter', 'portkey')
        is_active_only: Only return active models

    Returns list of models for the provider
    """
    try:
        # Verify provider exists
        provider = get_provider_by_slug(provider_slug)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with slug '{provider_slug}' not found",
            )

        models = get_models_by_provider_slug(provider_slug, is_active_only)
        return models
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching models for provider {provider_slug}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch models for provider",
        )


@router.get("/{model_id}", response_model=ModelWithProvider)
async def get_model(model_id: int):
    """
    Get a specific model by ID

    Args:
        model_id: Model ID

    Returns model details with provider information
    """
    try:
        model = get_model_by_id(model_id)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )
        return model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching model {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch model"
        )


@router.get("/{model_id}/health/history", response_model=list[ModelHealthHistoryResponse])
async def get_model_health_history_endpoint(
    model_id: int,
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
):
    """
    Get health check history for a model

    Args:
        model_id: Model ID
        limit: Maximum number of records to return

    Returns list of health check records
    """
    try:
        # Verify model exists
        model = get_model_by_id(model_id)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )

        history = get_model_health_history(model_id, limit)
        return history
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching health history for model {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch health history",
        )


@router.post("/", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model_endpoint(model: ModelCreate):
    """
    Create a new model

    Args:
        model: Model creation data

    Returns created model
    """
    try:
        # Verify provider exists
        provider = get_provider_by_id(model.provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {model.provider_id} not found",
            )

        # Check if model already exists for this provider
        existing = get_model_by_provider_and_model_id(model.provider_id, model.provider_model_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Model '{model.provider_model_id}' already exists for provider {model.provider_id}",
            )

        created_model = create_model(model.model_dump())
        if not created_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create model"
            )
        return created_model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create model"
        )


@router.post("/bulk", response_model=list[ModelResponse], status_code=status.HTTP_201_CREATED)
async def bulk_create_models_endpoint(bulk_data: ModelBulkCreate):
    """
    Create multiple models at once

    Args:
        bulk_data: Bulk model creation data

    Returns list of created models
    """
    try:
        if not bulk_data.models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No models provided"
            )

        # Verify all providers exist
        provider_ids = {m.provider_id for m in bulk_data.models}
        for provider_id in provider_ids:
            provider = get_provider_by_id(provider_id)
            if not provider:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Provider with ID {provider_id} not found",
                )

        models_data = [m.model_dump() for m in bulk_data.models]
        created_models = bulk_create_models(models_data)

        if not created_models:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create models"
            )

        return created_models
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk creating models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to bulk create models"
        )


@router.post("/upsert", response_model=ModelResponse, status_code=status.HTTP_200_OK)
async def upsert_model_endpoint(model: ModelCreate):
    """
    Upsert a model (insert or update if exists)

    Args:
        model: Model data

    Returns upserted model
    """
    try:
        # Verify provider exists
        provider = get_provider_by_id(model.provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {model.provider_id} not found",
            )

        upserted_model = upsert_model(model.model_dump())
        if not upserted_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upsert model"
            )
        return upserted_model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error upserting model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upsert model"
        )


@router.post("/bulk-upsert", response_model=list[ModelResponse], status_code=status.HTTP_200_OK)
async def bulk_upsert_models_endpoint(bulk_data: ModelBulkCreate):
    """
    Upsert multiple models at once (insert or update if exists)

    Args:
        bulk_data: Bulk model data

    Returns list of upserted models
    """
    try:
        if not bulk_data.models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No models provided"
            )

        # Verify all providers exist
        provider_ids = {m.provider_id for m in bulk_data.models}
        for provider_id in provider_ids:
            provider = get_provider_by_id(provider_id)
            if not provider:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Provider with ID {provider_id} not found",
                )

        models_data = [m.model_dump() for m in bulk_data.models]
        upserted_models = bulk_upsert_models(models_data)

        if not upserted_models:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upsert models"
            )

        return upserted_models
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk upserting models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to bulk upsert models"
        )


@router.patch("/{model_id}", response_model=ModelResponse)
async def update_model_endpoint(model_id: int, model: ModelUpdate):
    """
    Update a model

    Args:
        model_id: Model ID
        model: Model update data (only specified fields will be updated)

    Returns updated model
    """
    try:
        # Check if model exists
        existing = get_model_by_id(model_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )

        # Only include fields that were actually set
        update_data = model.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
            )

        # If provider_id is being updated, verify it exists
        if "provider_id" in update_data:
            provider = get_provider_by_id(update_data["provider_id"])
            if not provider:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Provider with ID {update_data['provider_id']} not found",
                )

        updated_model = update_model(model_id, update_data)
        if not updated_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update model"
            )
        return updated_model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating model {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update model"
        )


@router.patch("/{model_id}/health", response_model=ModelResponse)
async def update_model_health_endpoint(model_id: int, health_update: ModelHealthUpdate):
    """
    Update model health status

    Args:
        model_id: Model ID
        health_update: Health status update data

    Returns updated model
    """
    try:
        # Check if model exists
        existing = get_model_by_id(model_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )

        # Validate health status
        if health_update.health_status not in ["healthy", "degraded", "down", "unknown"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid health status. Must be: 'healthy', 'degraded', 'down', or 'unknown'",
            )

        updated_model = update_model_health(
            model_id,
            health_update.health_status,
            health_update.response_time_ms,
            health_update.error_message,
        )

        if not updated_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update model health",
            )
        return updated_model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating model health {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update model health",
        )


@router.post("/{model_id}/activate", response_model=ModelResponse)
async def activate_model_endpoint(model_id: int):
    """
    Activate a model

    Args:
        model_id: Model ID

    Returns activated model
    """
    try:
        model = get_model_by_id(model_id)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )

        activated_model = activate_model(model_id)
        if not activated_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to activate model"
            )
        return activated_model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error activating model {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to activate model"
        )


@router.post("/{model_id}/deactivate", response_model=ModelResponse)
async def deactivate_model_endpoint(model_id: int):
    """
    Deactivate a model (soft delete)

    Args:
        model_id: Model ID

    Returns deactivated model
    """
    try:
        model = get_model_by_id(model_id)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )

        deactivated_model = deactivate_model(model_id)
        if not deactivated_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to deactivate model",
            )
        return deactivated_model
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deactivating model {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to deactivate model"
        )


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_endpoint(model_id: int):
    """
    Delete a model (hard delete)

    Args:
        model_id: Model ID
    """
    try:
        model = get_model_by_id(model_id)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found"
            )

        success = delete_model(model_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete model"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting model {model_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete model"
        )
