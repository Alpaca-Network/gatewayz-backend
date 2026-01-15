"""
Admin Pricing Analytics Routes
API endpoints for admin monitoring and cost analytics
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.security.deps import get_current_admin_user
from src.services.pricing_analytics import (
    get_cost_breakdown_by_provider,
    get_cost_trend,
    get_model_usage_analytics,
    get_most_expensive_models,
    get_most_used_models,
    get_pricing_efficiency_report,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/pricing-analytics", tags=["admin-pricing"])


@router.get("/models")
async def get_model_pricing_analytics(
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d, all"),
    provider: Optional[str] = Query(None, description="Filter by provider slug"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    sort_by: str = Query("cost", description="Sort by: cost, requests, tokens"),
    include_free: bool = Query(False, description="Include models with zero cost"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get comprehensive model usage analytics with cost tracking

    **Admin only endpoint**

    Returns detailed analytics for each model including:
    - Total cost (USD)
    - Input/output cost breakdown
    - Request counts
    - Token usage (input/output/total)
    - Average costs per request
    - Processing time metrics
    - Model metadata (pricing, context length, modality, health)

    Example response:
    ```json
    {
      "models": [
        {
          "model_id": 123,
          "model_name": "GPT-4",
          "provider_slug": "openrouter",
          "successful_requests": 1500,
          "total_input_tokens": 150000,
          "total_output_tokens": 75000,
          "total_tokens": 225000,
          "input_token_price": 0.00003,
          "output_token_price": 0.00006,
          "total_cost_usd": 9.0,
          "input_cost_usd": 4.5,
          "output_cost_usd": 4.5,
          "avg_cost_per_request_usd": 0.006
        }
      ],
      "summary": {
        "total_models": 50,
        "total_cost_usd": 1250.50,
        "total_requests": 125000,
        "total_tokens": 15000000
      }
    }
    ```
    """
    try:
        analytics = get_model_usage_analytics(
            time_range=time_range,
            provider=provider,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            include_free=include_free
        )

        return analytics

    except Exception as e:
        logger.error(f"Error in model pricing analytics endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers")
async def get_provider_cost_breakdown(
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d, all"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get cost breakdown aggregated by provider

    **Admin only endpoint**

    Returns total costs, requests, and token usage per provider.

    Example response:
    ```json
    [
      {
        "provider_slug": "openrouter",
        "provider_name": "OpenRouter",
        "model_count": 25,
        "total_requests": 50000,
        "total_tokens": 10000000,
        "total_cost_usd": 850.50,
        "input_cost_usd": 425.25,
        "output_cost_usd": 425.25,
        "avg_cost_per_request": 0.017
      }
    ]
    ```
    """
    try:
        breakdown = get_cost_breakdown_by_provider(time_range=time_range)
        return breakdown

    except Exception as e:
        logger.error(f"Error in provider cost breakdown endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trend")
async def get_cost_trend_data(
    granularity: str = Query("day", description="Time granularity: hour, day, week, month"),
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d"),
    provider: Optional[str] = Query(None, description="Filter by provider slug"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get cost trend over time

    **Admin only endpoint**

    Returns time-bucketed cost data for trend analysis.

    Example response:
    ```json
    [
      {
        "time_bucket": "2026-01-01T00:00:00Z",
        "request_count": 5000,
        "total_tokens": 1000000,
        "total_cost": 50.25,
        "input_cost": 25.12,
        "output_cost": 25.13
      }
    ]
    ```
    """
    try:
        trend = get_cost_trend(
            granularity=granularity,
            time_range=time_range,
            provider=provider
        )
        return trend

    except Exception as e:
        logger.error(f"Error in cost trend endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/expensive-models")
async def get_most_expensive_models_endpoint(
    limit: int = Query(10, ge=1, le=100, description="Number of models to return"),
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d, all"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get the most expensive models by total cost

    **Admin only endpoint**

    Identifies models with highest total cost to help optimize spending.
    """
    try:
        models = get_most_expensive_models(limit=limit, time_range=time_range)
        return {"models": models}

    except Exception as e:
        logger.error(f"Error in most expensive models endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/popular-models")
async def get_most_used_models_endpoint(
    limit: int = Query(10, ge=1, le=100, description="Number of models to return"),
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d, all"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get the most used models by request count

    **Admin only endpoint**

    Identifies most popular models to understand usage patterns.
    """
    try:
        models = get_most_used_models(limit=limit, time_range=time_range)
        return {"models": models}

    except Exception as e:
        logger.error(f"Error in most used models endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/efficiency-report")
async def get_efficiency_report(
    provider: Optional[str] = Query(None, description="Filter by provider slug"),
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d, all"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get a comprehensive pricing efficiency report

    **Admin only endpoint**

    Provides detailed analysis of cost efficiency including:
    - Average cost per request and per token
    - Most and least efficient models
    - Cost optimization recommendations

    Example response:
    ```json
    {
      "summary": {
        "total_models": 50,
        "total_cost_usd": 1250.50,
        "total_requests": 125000,
        "total_tokens": 15000000
      },
      "efficiency_metrics": {
        "avg_cost_per_request": 0.010004,
        "avg_cost_per_token": 0.000000083,
        "most_efficient_models": [
          {
            "model_name": "Llama-3-8B",
            "provider": "groq",
            "cost_per_request": 0.000001,
            "total_cost": 0.05,
            "requests": 50000
          }
        ],
        "least_efficient_models": [
          {
            "model_name": "GPT-4",
            "provider": "openrouter",
            "cost_per_request": 0.06,
            "total_cost": 600.00,
            "requests": 10000
          }
        ]
      },
      "recommendations": [
        {
          "type": "high_cost_model",
          "message": "Consider alternatives to GPT-4 which costs $0.060000 per request",
          "model": "GPT-4",
          "current_cost": 0.06
        }
      ]
    }
    ```
    """
    try:
        report = get_pricing_efficiency_report(
            provider=provider,
            time_range=time_range
        )
        return report

    except Exception as e:
        logger.error(f"Error in efficiency report endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_pricing_summary(
    time_range: str = Query("30d", description="Time range: 1h, 24h, 7d, 30d, all"),
    _admin=Depends(get_current_admin_user)
):
    """
    Get a quick summary of pricing analytics

    **Admin only endpoint**

    Returns high-level metrics for dashboard display:
    - Total cost across all models
    - Total requests and tokens
    - Top 5 most expensive models
    - Top 5 most used models
    - Cost breakdown by provider
    """
    try:
        # Get all the data in parallel
        analytics = get_model_usage_analytics(time_range=time_range, limit=1000)
        expensive = get_most_expensive_models(limit=5, time_range=time_range)
        popular = get_most_used_models(limit=5, time_range=time_range)
        providers = get_cost_breakdown_by_provider(time_range=time_range)

        return {
            "summary": analytics.get("summary", {}),
            "top_expensive_models": expensive,
            "top_popular_models": popular,
            "cost_by_provider": providers[:10],  # Top 10 providers
            "time_range": time_range
        }

    except Exception as e:
        logger.error(f"Error in pricing summary endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
