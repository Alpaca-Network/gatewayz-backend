"""
Model health tracking endpoints.

Provides endpoints to view and monitor model health metrics including
response times, success rates, and error tracking.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.db import model_health as model_health_db
from src.security.deps import get_optional_user  # Optional auth for monitoring

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/model-health", tags=["monitoring"])
async def get_all_model_health(
    provider: str | None = Query(None, description="Filter by provider"),
    status: str | None = Query(
        None, description="Filter by last status (success, error, timeout, etc.)"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    _: dict[str, Any] | None = Depends(get_optional_user),
) -> dict:
    """
    Get health metrics for all monitored models.

    Returns a list of model health records with response times, success rates,
    and last call information.

    Args:
        provider: Optional filter by provider (e.g., 'openrouter', 'huggingface')
        status: Optional filter by last status
        limit: Maximum number of records (1-1000)
        offset: Number of records to skip for pagination

    Returns:
        Dictionary with model health records and metadata
    """
    try:
        records = model_health_db.get_all_model_health(
            provider=provider,
            status=status,
            limit=limit,
            offset=offset,
        )

        return {
            "total": len(records),
            "limit": limit,
            "offset": offset,
            "filters": {
                "provider": provider,
                "status": status,
            },
            "models": records,
        }
    except Exception as e:
        logger.error(f"Error fetching model health data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch model health data")


@router.get("/model-health/{provider}/{model}", tags=["monitoring"])
async def get_model_health(
    provider: str,
    model: str,
    _: dict[str, Any] | None = Depends(get_optional_user),
) -> dict:
    """
    Get health metrics for a specific provider-model combination.

    Args:
        provider: Provider name (e.g., 'openrouter', 'huggingface')
        model: Model identifier

    Returns:
        Dictionary with health metrics for the specified model
    """
    try:
        record = model_health_db.get_model_health(provider=provider, model=model)

        if not record:
            raise HTTPException(
                status_code=404,
                detail=f"No health data found for provider '{provider}' and model '{model}'",
            )

        return record
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching model health for {provider}/{model}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch model health data")


@router.get("/model-health/unhealthy", tags=["monitoring"])
async def get_unhealthy_models(
    error_threshold: float = Query(0.2, ge=0.0, le=1.0, description="Minimum error rate (0.0-1.0)"),
    min_calls: int = Query(10, ge=1, description="Minimum number of calls to evaluate"),
    _: dict[str, Any] | None = Depends(get_optional_user),
) -> dict:
    """
    Get models with high error rates (unhealthy models).

    Args:
        error_threshold: Minimum error rate to be considered unhealthy (default: 0.2 = 20%)
        min_calls: Minimum number of calls required to evaluate health (default: 10)

    Returns:
        List of unhealthy models with error rates
    """
    try:
        unhealthy_models = model_health_db.get_unhealthy_models(
            error_threshold=error_threshold,
            min_calls=min_calls,
        )

        return {
            "threshold": error_threshold,
            "min_calls": min_calls,
            "total_unhealthy": len(unhealthy_models),
            "models": unhealthy_models,
        }
    except Exception as e:
        logger.error(f"Error fetching unhealthy models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch unhealthy models")


@router.get("/model-health/stats", tags=["monitoring"])
async def get_model_health_stats(
    _: dict[str, Any] | None = Depends(get_optional_user),
) -> dict:
    """
    Get aggregate statistics for model health tracking.

    Returns:
        Dictionary with aggregate statistics:
        - total_models: Total number of tracked models
        - total_calls: Total number of calls across all models
        - total_success: Total successful calls
        - total_errors: Total failed calls
        - average_response_time: Average response time across all models
        - success_rate: Overall success rate (0.0-1.0)
    """
    try:
        stats = model_health_db.get_model_health_stats()
        return stats
    except Exception as e:
        logger.error(f"Error fetching model health stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch model health stats")


@router.get("/model-health/provider/{provider}/summary", tags=["monitoring"])
async def get_provider_health_summary(
    provider: str,
    _: dict[str, Any] | None = Depends(get_optional_user),
) -> dict:
    """
    Get health summary for all models from a specific provider.

    Args:
        provider: Provider name (e.g., 'openrouter', 'huggingface')

    Returns:
        Dictionary with provider-level statistics
    """
    try:
        summary = model_health_db.get_provider_health_summary(provider=provider)

        if summary["total_models"] == 0:
            raise HTTPException(
                status_code=404, detail=f"No health data found for provider '{provider}'"
            )

        return summary
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching provider health summary for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch provider health summary")


@router.get("/model-health/providers", tags=["monitoring"])
async def get_all_providers(
    _: dict[str, Any] | None = Depends(get_optional_user),
) -> dict:
    """
    Get list of all providers with health data.

    Returns:
        Dictionary with list of providers and their basic stats
    """
    try:
        # Get all records and extract unique providers
        all_records = model_health_db.get_all_model_health(limit=1000)

        providers = {}
        for record in all_records:
            provider = record["provider"]
            if provider not in providers:
                providers[provider] = {
                    "provider": provider,
                    "model_count": 0,
                    "total_calls": 0,
                }
            providers[provider]["model_count"] += 1
            providers[provider]["total_calls"] += record.get("call_count", 0)

        provider_list = list(providers.values())
        provider_list.sort(key=lambda x: x["total_calls"], reverse=True)

        return {
            "total_providers": len(provider_list),
            "providers": provider_list,
        }
    except Exception as e:
        logger.error(f"Error fetching providers list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch providers list")
