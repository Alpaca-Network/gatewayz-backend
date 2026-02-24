"""
Butter.dev Cache Analytics API Endpoints

Provides analytics endpoints for monitoring LLM response cache performance,
including cache hit rates, cost savings, and usage trends.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config.config import Config
from src.config.supabase_config import get_supabase_client
from src.db.users import get_user
from src.security.deps import get_api_key
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/analytics/cache", tags=["analytics"])
async def get_cache_analytics(
    days: int = Query(30, ge=1, le=90, description="Number of days to analyze"),
    api_key: str = Depends(get_api_key),
) -> dict[str, Any]:
    """
    Get Butter.dev cache performance analytics for the authenticated user.

    Returns cache hit rate, total savings, and usage breakdown over the specified period.

    Args:
        days: Number of days to include in the analysis (1-90, default: 30)

    Returns:
        Analytics data including:
        - total_requests: Total API requests in the period
        - cache_hits: Number of cache hits
        - cache_misses: Number of cache misses
        - cache_hit_rate_percent: Cache hit rate as percentage
        - total_savings_usd: Total cost savings from cache hits
        - estimated_monthly_savings_usd: Projected monthly savings
        - top_cached_models: Models with highest cache hit rates
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = user.get("id")
        since_date = datetime.now(UTC) - timedelta(days=days)

        client = get_supabase_client()

        # Query chat completion requests with cache metadata
        result = (
            client.table("chat_completion_requests")
            .select(
                "model_id, cost_usd, metadata, created_at, models(model_name, providers(name, slug))"
            )
            .eq("user_id", user_id)
            .eq("status", "completed")
            .gte("created_at", since_date.isoformat())
            .execute()
        )

        requests = result.data or []

        # Calculate statistics
        total_requests = len(requests)
        cache_hits = 0
        cache_misses = 0
        total_savings = 0.0
        model_stats: dict[str, dict[str, Any]] = {}

        for req in requests:
            metadata = req.get("metadata") or {}
            is_cache_hit = metadata.get("butter_cache_hit", False)

            # Get model info
            model_info = req.get("models") or {}
            model_name = model_info.get("model_name", "unknown")
            provider_info = model_info.get("providers") or {}
            provider_name = provider_info.get("name", "unknown")

            # Initialize model stats if needed
            if model_name not in model_stats:
                model_stats[model_name] = {
                    "model_name": model_name,
                    "provider": provider_name,
                    "total_requests": 0,
                    "cache_hits": 0,
                    "savings_usd": 0.0,
                }

            model_stats[model_name]["total_requests"] += 1

            if is_cache_hit:
                cache_hits += 1
                model_stats[model_name]["cache_hits"] += 1

                # Get actual cost that was saved
                actual_cost = metadata.get("actual_cost_usd", 0.0)
                if actual_cost:
                    total_savings += float(actual_cost)
                    model_stats[model_name]["savings_usd"] += float(actual_cost)
            else:
                cache_misses += 1

        # Calculate derived metrics
        cache_hit_rate = (cache_hits / total_requests * 100) if total_requests > 0 else 0
        estimated_monthly_savings = (total_savings * 30 / days) if days > 0 else 0

        # Sort models by cache hit rate and get top 10
        top_cached_models = []
        for model_name, stats in model_stats.items():
            if stats["total_requests"] >= 5:  # Only include models with sufficient data
                hit_rate = stats["cache_hits"] / stats["total_requests"] * 100
                top_cached_models.append(
                    {
                        "model_name": model_name,
                        "provider": stats["provider"],
                        "total_requests": stats["total_requests"],
                        "cache_hits": stats["cache_hits"],
                        "cache_hit_rate_percent": round(hit_rate, 2),
                        "savings_usd": round(stats["savings_usd"], 6),
                    }
                )

        # Sort by cache hit rate descending
        top_cached_models.sort(key=lambda x: x["cache_hit_rate_percent"], reverse=True)
        top_cached_models = top_cached_models[:10]

        return {
            "period_days": days,
            "start_date": since_date.isoformat(),
            "end_date": datetime.now(UTC).isoformat(),
            "total_requests": total_requests,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate_percent": round(cache_hit_rate, 2),
            "total_savings_usd": round(total_savings, 6),
            "estimated_monthly_savings_usd": round(estimated_monthly_savings, 2),
            "top_cached_models": top_cached_models,
            "cache_enabled": (user.get("preferences") or {}).get(
                "enable_butter_cache", True
            ),  # Enabled by default
            "system_enabled": Config.BUTTER_DEV_ENABLED,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting cache analytics for user %s: %s",
            sanitize_for_logging(str(locals().get("user_id", "unknown"))),
            sanitize_for_logging(str(e)),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve cache analytics")


@router.get("/analytics/cache/summary", tags=["analytics"])
async def get_cache_summary(
    api_key: str = Depends(get_api_key),
) -> dict[str, Any]:
    """
    Get a quick summary of Butter.dev cache performance.

    Returns a simplified overview suitable for dashboard widgets.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = user.get("id")
        preferences = user.get("preferences") or {}
        cache_enabled = preferences.get("enable_butter_cache", True)  # Enabled by default

        # If cache is not enabled, return minimal response
        if not cache_enabled or not Config.BUTTER_DEV_ENABLED:
            return {
                "cache_enabled": cache_enabled,
                "system_enabled": Config.BUTTER_DEV_ENABLED,
                "message": (
                    "Cache is disabled. Enable it in settings to start saving on API costs."
                    if not cache_enabled
                    else "Butter.dev caching is currently disabled system-wide."
                ),
                "total_savings_usd": 0.0,
                "cache_hit_rate_percent": 0.0,
            }

        # Get last 30 days of data
        since_date = datetime.now(UTC) - timedelta(days=30)

        client = get_supabase_client()

        # Use RPC function if available, otherwise do manual calculation
        try:
            result = client.rpc(
                "get_user_cache_savings", {"p_user_id": user_id, "p_days": 30}
            ).execute()

            if result.data and len(result.data) > 0:
                stats = result.data[0]
                return {
                    "cache_enabled": cache_enabled,
                    "system_enabled": Config.BUTTER_DEV_ENABLED,
                    "total_requests": stats.get("total_requests", 0),
                    "cache_hits": stats.get("cache_hits", 0),
                    "cache_hit_rate_percent": float(stats.get("cache_hit_rate_percent", 0)),
                    "total_savings_usd": float(stats.get("total_savings_usd", 0)),
                    "estimated_monthly_savings_usd": float(
                        stats.get("estimated_monthly_savings_usd", 0)
                    ),
                }
        except Exception as rpc_err:
            logger.debug(f"RPC function not available, using fallback: {rpc_err}")

        # Fallback: manual query
        result = (
            client.table("chat_completion_requests")
            .select("metadata")
            .eq("user_id", user_id)
            .eq("status", "completed")
            .gte("created_at", since_date.isoformat())
            .execute()
        )

        requests = result.data or []
        total_requests = len(requests)
        cache_hits = 0
        total_savings = 0.0

        for req in requests:
            metadata = req.get("metadata") or {}
            if metadata.get("butter_cache_hit"):
                cache_hits += 1
                total_savings += float(metadata.get("actual_cost_usd", 0))

        cache_hit_rate = (cache_hits / total_requests * 100) if total_requests > 0 else 0

        return {
            "cache_enabled": cache_enabled,
            "system_enabled": Config.BUTTER_DEV_ENABLED,
            "total_requests": total_requests,
            "cache_hits": cache_hits,
            "cache_hit_rate_percent": round(cache_hit_rate, 2),
            "total_savings_usd": round(total_savings, 6),
            "estimated_monthly_savings_usd": round(total_savings, 2),  # Already 30 days
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting cache summary: %s",
            sanitize_for_logging(str(e)),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve cache summary")
