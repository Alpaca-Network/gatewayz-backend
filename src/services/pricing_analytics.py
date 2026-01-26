"""
Pricing Analytics Service
Provides admin analytics for model usage and costs using the model_pricing table.

Note: The original pricing_calculator.py module was removed as part of the
pricing consolidation (commit d3f6c5b7). Pricing is now stored in the
model_pricing database table.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def calculate_request_cost_with_standard(
    provider: str,
    model_data: Dict[str, Any],
    prompt_tokens: int,
    completion_tokens: int
) -> Dict[str, Any]:
    """
    Calculate cost for a request using model pricing data.

    Uses pricing from model_data if available, otherwise falls back to
    a default rate.

    Args:
        provider: Provider name (e.g., 'openrouter', 'deepinfra')
        model_data: Model data including pricing fields
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens

    Returns:
        Dict with cost breakdown:
        {
            "total_cost": float,
            "input_cost": float,
            "output_cost": float,
            "pricing_source": str,
            "provider": str,
            "modality": str
        }
    """
    try:
        # Try to get pricing from model_data (from model_pricing table)
        input_price = model_data.get("input_price_per_token") or model_data.get("price_per_input_token")
        output_price = model_data.get("output_price_per_token") or model_data.get("price_per_output_token")

        if input_price is not None and output_price is not None:
            input_cost = prompt_tokens * float(input_price)
            output_cost = completion_tokens * float(output_price)
            return {
                "total_cost": input_cost + output_cost,
                "input_cost": input_cost,
                "output_cost": output_cost,
                "pricing_source": "model_pricing",
                "provider": provider,
                "modality": model_data.get("modality", "text")
            }

        # Fallback to default rate if no pricing data available
        default_rate = 0.00002  # $0.02 per 1K tokens
        input_cost = prompt_tokens * default_rate
        output_cost = completion_tokens * default_rate
        return {
            "total_cost": input_cost + output_cost,
            "input_cost": input_cost,
            "output_cost": output_cost,
            "pricing_source": "default",
            "provider": provider,
            "modality": model_data.get("modality", "unknown")
        }

    except Exception as e:
        logger.error(f"Error calculating cost: {e}")
        # Fallback to simple calculation
        default_rate = 0.00002
        return {
            "total_cost": (prompt_tokens + completion_tokens) * default_rate,
            "input_cost": prompt_tokens * default_rate,
            "output_cost": completion_tokens * default_rate,
            "pricing_source": "fallback",
            "provider": provider,
            "modality": "unknown"
        }


def get_model_usage_analytics(
    time_range: Optional[str] = "30d",
    provider: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "cost",
    include_free: bool = False
) -> Dict[str, Any]:
    """
    Get comprehensive model usage analytics with cost tracking

    Args:
        time_range: Time range ('1h', '24h', '7d', '30d', 'all')
        provider: Optional provider filter
        limit: Maximum results to return
        offset: Offset for pagination
        sort_by: Sort field ('cost', 'requests', 'tokens')
        include_free: Whether to include models with zero cost

    Returns:
        Dict with analytics data and summary
    """
    try:
        client = get_supabase_client()

        # Build time filter
        start_time = None
        if time_range and time_range != "all":
            now = datetime.now(timezone.utc)
            if time_range == "1h":
                start_time = now - timedelta(hours=1)
            elif time_range == "24h":
                start_time = now - timedelta(hours=24)
            elif time_range == "7d":
                start_time = now - timedelta(days=7)
            elif time_range == "30d":
                start_time = now - timedelta(days=30)

        # Query model_usage_analytics view with filters
        query = client.table("model_usage_analytics").select("*")

        if provider:
            query = query.eq("provider_slug", provider)

        if not include_free:
            query = query.neq("total_cost_usd", 0)

        # Sort
        sort_map = {
            "cost": "total_cost_usd",
            "requests": "successful_requests",
            "tokens": "total_tokens"
        }
        sort_field = sort_map.get(sort_by, "total_cost_usd")
        query = query.order(sort_field, desc=True)

        # Pagination
        query = query.range(offset, offset + limit - 1)

        result = query.execute()
        models = result.data or []

        # Calculate summary
        total_query = client.table("model_usage_analytics").select(
            "total_cost_usd, successful_requests, total_tokens"
        )

        if provider:
            total_query = total_query.eq("provider_slug", provider)

        if not include_free:
            total_query = total_query.neq("total_cost_usd", 0)

        total_result = total_query.execute()
        all_models = total_result.data or []

        summary = {
            "total_models": len(all_models),
            "total_cost_usd": sum(float(m.get("total_cost_usd", 0)) for m in all_models),
            "total_requests": sum(int(m.get("successful_requests", 0)) for m in all_models),
            "total_tokens": sum(int(m.get("total_tokens", 0)) for m in all_models),
            "time_range": time_range,
            "provider_filter": provider,
            "sort_by": sort_by
        }

        return {
            "models": models,
            "summary": summary,
            "limit": limit,
            "offset": offset,
            "total_count": len(all_models)
        }

    except Exception as e:
        logger.error(f"Error getting model usage analytics: {e}", exc_info=True)
        return {
            "models": [],
            "summary": {
                "total_models": 0,
                "total_cost_usd": 0,
                "total_requests": 0,
                "total_tokens": 0,
                "time_range": time_range,
                "provider_filter": provider,
                "sort_by": sort_by
            },
            "limit": limit,
            "offset": offset,
            "total_count": 0,
            "error": str(e)
        }


def get_cost_breakdown_by_provider(
    time_range: Optional[str] = "30d"
) -> List[Dict[str, Any]]:
    """
    Get cost breakdown aggregated by provider

    Args:
        time_range: Time range to analyze

    Returns:
        List of provider cost breakdowns
    """
    try:
        client = get_supabase_client()

        # Query aggregated by provider
        query = """
        SELECT
            provider_slug,
            provider_name,
            COUNT(DISTINCT model_id) as model_count,
            SUM(successful_requests) as total_requests,
            SUM(total_tokens) as total_tokens,
            SUM(total_cost_usd) as total_cost_usd,
            SUM(input_cost_usd) as input_cost_usd,
            SUM(output_cost_usd) as output_cost_usd,
            ROUND(AVG(avg_cost_per_request_usd), 6) as avg_cost_per_request
        FROM model_usage_analytics
        GROUP BY provider_slug, provider_name
        ORDER BY total_cost_usd DESC
        """

        result = client.rpc('exec_sql', {'query': query}).execute()

        if result.data:
            return result.data
        else:
            # Fallback: query view directly and aggregate in Python
            all_models = client.table("model_usage_analytics").select("*").execute()
            models = all_models.data or []

            # Group by provider
            provider_data = {}
            for model in models:
                provider = model.get("provider_slug", "unknown")
                if provider not in provider_data:
                    provider_data[provider] = {
                        "provider_slug": provider,
                        "provider_name": model.get("provider_name", provider),
                        "model_count": 0,
                        "total_requests": 0,
                        "total_tokens": 0,
                        "total_cost_usd": 0,
                        "input_cost_usd": 0,
                        "output_cost_usd": 0
                    }

                provider_data[provider]["model_count"] += 1
                provider_data[provider]["total_requests"] += model.get("successful_requests", 0)
                provider_data[provider]["total_tokens"] += model.get("total_tokens", 0)
                provider_data[provider]["total_cost_usd"] += float(model.get("total_cost_usd", 0))
                provider_data[provider]["input_cost_usd"] += float(model.get("input_cost_usd", 0))
                provider_data[provider]["output_cost_usd"] += float(model.get("output_cost_usd", 0))

            # Sort by cost
            providers = sorted(
                provider_data.values(),
                key=lambda x: x["total_cost_usd"],
                reverse=True
            )

            return providers

    except Exception as e:
        logger.error(f"Error getting cost breakdown by provider: {e}", exc_info=True)
        return []


def get_cost_trend(
    granularity: str = "day",
    time_range: str = "30d",
    provider: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get cost trend over time

    Args:
        granularity: Time granularity ('hour', 'day', 'week', 'month')
        time_range: Time range to analyze
        provider: Optional provider filter

    Returns:
        List of time-bucketed cost data
    """
    try:
        client = get_supabase_client()

        # Calculate start time
        now = datetime.now(timezone.utc)
        if time_range == "1h":
            start_time = now - timedelta(hours=1)
        elif time_range == "24h":
            start_time = now - timedelta(hours=24)
        elif time_range == "7d":
            start_time = now - timedelta(days=7)
        elif time_range == "30d":
            start_time = now - timedelta(days=30)
        else:
            start_time = now - timedelta(days=30)

        # Build time truncation for PostgreSQL
        trunc_map = {
            "hour": "hour",
            "day": "day",
            "week": "week",
            "month": "month"
        }
        trunc = trunc_map.get(granularity, "day")

        # Query with time bucketing
        query = f"""
        SELECT
            DATE_TRUNC('{trunc}', ccr.created_at) as time_bucket,
            COUNT(ccr.id) as request_count,
            SUM(ccr.input_tokens + ccr.output_tokens) as total_tokens,
            SUM(COALESCE(ccr.cost_usd, 0)) as total_cost,
            SUM(COALESCE(ccr.input_cost_usd, 0)) as input_cost,
            SUM(COALESCE(ccr.output_cost_usd, 0)) as output_cost
        FROM chat_completion_requests ccr
        WHERE ccr.status = 'completed'
        AND ccr.created_at >= '{start_time.isoformat()}'
        """

        if provider:
            query += f" AND ccr.model_id IN (SELECT id FROM models WHERE provider_id = (SELECT id FROM providers WHERE slug = '{provider}'))"

        query += f" GROUP BY time_bucket ORDER BY time_bucket ASC"

        result = client.rpc('exec_sql', {'query': query}).execute()

        if result.data:
            return result.data
        else:
            return []

    except Exception as e:
        logger.error(f"Error getting cost trend: {e}", exc_info=True)
        return []


def get_most_expensive_models(
    limit: int = 10,
    time_range: str = "30d"
) -> List[Dict[str, Any]]:
    """
    Get the most expensive models by total cost

    Args:
        limit: Number of models to return
        time_range: Time range to analyze

    Returns:
        List of most expensive models
    """
    try:
        analytics = get_model_usage_analytics(
            time_range=time_range,
            limit=limit,
            sort_by="cost"
        )

        return analytics.get("models", [])

    except Exception as e:
        logger.error(f"Error getting most expensive models: {e}", exc_info=True)
        return []


def get_most_used_models(
    limit: int = 10,
    time_range: str = "30d"
) -> List[Dict[str, Any]]:
    """
    Get the most used models by request count

    Args:
        limit: Number of models to return
        time_range: Time range to analyze

    Returns:
        List of most used models
    """
    try:
        analytics = get_model_usage_analytics(
            time_range=time_range,
            limit=limit,
            sort_by="requests"
        )

        return analytics.get("models", [])

    except Exception as e:
        logger.error(f"Error getting most used models: {e}", exc_info=True)
        return []


def get_pricing_efficiency_report(
    provider: Optional[str] = None,
    time_range: str = "30d"
) -> Dict[str, Any]:
    """
    Get a comprehensive pricing efficiency report

    Args:
        provider: Optional provider filter
        time_range: Time range to analyze

    Returns:
        Dict with efficiency metrics
    """
    try:
        analytics = get_model_usage_analytics(
            time_range=time_range,
            provider=provider,
            limit=1000
        )

        models = analytics.get("models", [])
        summary = analytics.get("summary", {})

        if not models:
            return {
                "summary": summary,
                "efficiency_metrics": {},
                "recommendations": []
            }

        # Calculate efficiency metrics
        avg_cost_per_request = summary["total_cost_usd"] / summary["total_requests"] if summary["total_requests"] > 0 else 0
        avg_cost_per_token = summary["total_cost_usd"] / summary["total_tokens"] if summary["total_tokens"] > 0 else 0

        # Find most/least efficient models
        models_with_efficiency = []
        for model in models:
            if model.get("successful_requests", 0) > 0:
                efficiency = model.get("total_cost_usd", 0) / model.get("successful_requests", 0)
                models_with_efficiency.append({
                    "model_name": model.get("model_name"),
                    "provider": model.get("provider_slug"),
                    "cost_per_request": efficiency,
                    "total_cost": model.get("total_cost_usd"),
                    "requests": model.get("successful_requests")
                })

        most_efficient = sorted(models_with_efficiency, key=lambda x: x["cost_per_request"])[:5]
        least_efficient = sorted(models_with_efficiency, key=lambda x: x["cost_per_request"], reverse=True)[:5]

        # Generate recommendations
        recommendations = []
        if least_efficient:
            recommendations.append({
                "type": "high_cost_model",
                "message": f"Consider alternatives to {least_efficient[0]['model_name']} which costs ${least_efficient[0]['cost_per_request']:.6f} per request",
                "model": least_efficient[0]['model_name'],
                "current_cost": least_efficient[0]['cost_per_request']
            })

        return {
            "summary": summary,
            "efficiency_metrics": {
                "avg_cost_per_request": round(avg_cost_per_request, 6),
                "avg_cost_per_token": round(avg_cost_per_token, 9),
                "most_efficient_models": most_efficient,
                "least_efficient_models": least_efficient
            },
            "recommendations": recommendations
        }

    except Exception as e:
        logger.error(f"Error generating pricing efficiency report: {e}", exc_info=True)
        return {
            "summary": {},
            "efficiency_metrics": {},
            "recommendations": [],
            "error": str(e)
        }
