"""
API routes for provider management
Handles CRUD operations for AI model providers
"""

import logging

from fastapi import APIRouter, HTTPException, Path, Query, status

from src.db.models_catalog_db import get_models_stats
from src.db.providers_db import (
    activate_provider,
    create_provider,
    deactivate_provider,
    delete_provider,
    get_all_providers,
    get_provider_by_id,
    get_provider_by_slug,
    get_providers_by_health_status,
    get_providers_stats,
    search_providers,
    update_provider,
    update_provider_health,
)
from src.schemas.providers import (
    ProviderCreate,
    ProviderHealthUpdate,
    ProviderResponse,
    ProviderStats,
    ProviderUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/providers",
    tags=["Providers Management"],
)


@router.get("/", response_model=list[ProviderResponse])
async def list_providers(
    is_active_only: bool = Query(True, description="Only return active providers"),
    include_inactive: bool = Query(False, description="Include inactive providers"),
):
    """
    Get all providers

    Returns list of all providers with their current status
    """
    try:
        providers = get_all_providers(
            is_active_only=is_active_only, include_inactive=include_inactive
        )
        return providers
    except Exception as e:
        logger.error(f"Error fetching providers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch providers"
        )


@router.get("/stats", response_model=ProviderStats)
async def get_provider_statistics():
    """
    Get provider statistics

    Returns overall statistics about providers including:
    - Total count
    - Active/inactive counts
    - Health status distribution
    """
    try:
        stats = get_providers_stats()
        return stats
    except Exception as e:
        logger.error(f"Error fetching provider stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch provider statistics",
        )


@router.get("/search", response_model=list[ProviderResponse])
async def search_providers_endpoint(
    q: str = Query(..., min_length=1, description="Search query"),
):
    """
    Search providers by name, slug, or description

    Args:
        q: Search query string

    Returns list of matching providers
    """
    try:
        providers = search_providers(q)
        return providers
    except Exception as e:
        logger.error(f"Error searching providers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to search providers"
        )


@router.get("/health/{health_status}", response_model=list[ProviderResponse])
async def get_providers_by_health(
    health_status: str = Path(
        ..., description="Health status: 'healthy', 'degraded', 'down', 'unknown'"
    ),
):
    """
    Get providers by health status

    Args:
        health_status: Health status to filter by

    Returns list of providers with specified health status
    """
    try:
        if health_status not in ["healthy", "degraded", "down", "unknown"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid health status. Must be: 'healthy', 'degraded', 'down', or 'unknown'",
            )

        providers = get_providers_by_health_status(health_status)
        return providers
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching providers by health status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch providers by health status",
        )


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(provider_id: int):
    """
    Get a specific provider by ID

    Args:
        provider_id: Provider ID

    Returns provider details
    """
    try:
        provider = get_provider_by_id(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )
        return provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch provider"
        )


@router.get("/slug/{slug}", response_model=ProviderResponse)
async def get_provider_by_slug_endpoint(slug: str):
    """
    Get a specific provider by slug

    Args:
        slug: Provider slug (e.g., 'openrouter', 'portkey')

    Returns provider details
    """
    try:
        provider = get_provider_by_slug(slug)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with slug '{slug}' not found",
            )
        return provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching provider by slug {slug}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch provider"
        )


@router.get("/{provider_id}/models/stats")
async def get_provider_model_stats(provider_id: int):
    """
    Get model statistics for a specific provider

    Args:
        provider_id: Provider ID

    Returns model statistics for the provider
    """
    try:
        # First verify provider exists
        provider = get_provider_by_id(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )

        stats = get_models_stats(provider_id=provider_id)
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching model stats for provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch model statistics",
        )


@router.post("/", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider_endpoint(provider: ProviderCreate):
    """
    Create a new provider

    Args:
        provider: Provider creation data

    Returns created provider
    """
    try:
        # Check if provider with same slug already exists
        existing = get_provider_by_slug(provider.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Provider with slug '{provider.slug}' already exists",
            )

        created_provider = create_provider(provider.model_dump())
        if not created_provider:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create provider",
            )
        return created_provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create provider"
        )


@router.patch("/{provider_id}", response_model=ProviderResponse)
async def update_provider_endpoint(provider_id: int, provider: ProviderUpdate):
    """
    Update a provider

    Args:
        provider_id: Provider ID
        provider: Provider update data (only specified fields will be updated)

    Returns updated provider
    """
    try:
        # Check if provider exists
        existing = get_provider_by_id(provider_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )

        # Only include fields that were actually set
        update_data = provider.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
            )

        updated_provider = update_provider(provider_id, update_data)
        if not updated_provider:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update provider",
            )
        return updated_provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update provider"
        )


@router.patch("/{provider_id}/health", response_model=ProviderResponse)
async def update_provider_health_endpoint(provider_id: int, health_update: ProviderHealthUpdate):
    """
    Update provider health status

    Args:
        provider_id: Provider ID
        health_update: Health status update data

    Returns updated provider
    """
    try:
        # Check if provider exists
        existing = get_provider_by_id(provider_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )

        # Validate health status
        if health_update.health_status not in ["healthy", "degraded", "down", "unknown"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid health status. Must be: 'healthy', 'degraded', 'down', or 'unknown'",
            )

        updated_provider = update_provider_health(
            provider_id, health_update.health_status, health_update.average_response_time_ms
        )

        if not updated_provider:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update provider health",
            )
        return updated_provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating provider health {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update provider health",
        )


@router.post("/{provider_id}/activate", response_model=ProviderResponse)
async def activate_provider_endpoint(provider_id: int):
    """
    Activate a provider

    Args:
        provider_id: Provider ID

    Returns activated provider
    """
    try:
        provider = get_provider_by_id(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )

        activated_provider = activate_provider(provider_id)
        if not activated_provider:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to activate provider",
            )
        return activated_provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error activating provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to activate provider"
        )


@router.post("/{provider_id}/deactivate", response_model=ProviderResponse)
async def deactivate_provider_endpoint(provider_id: int):
    """
    Deactivate a provider (soft delete)

    Args:
        provider_id: Provider ID

    Returns deactivated provider
    """
    try:
        provider = get_provider_by_id(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )

        deactivated_provider = deactivate_provider(provider_id)
        if not deactivated_provider:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to deactivate provider",
            )
        return deactivated_provider
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deactivating provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate provider",
        )


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_endpoint(provider_id: int):
    """
    Delete a provider (hard delete)

    Args:
        provider_id: Provider ID

    Note: This will cascade delete all models associated with this provider
    """
    try:
        provider = get_provider_by_id(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider with ID {provider_id} not found",
            )

        success = delete_provider(provider_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete provider",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete provider"
        )
