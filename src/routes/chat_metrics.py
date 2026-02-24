"""
Chat Completions Metrics Endpoints

Provides tokens-per-second metrics filtered to top 3 most popular models
plus minimum 1 health check per provider, exposed in Prometheus format.

Endpoints:
- GET /v1/chat/completions/metrics/tokens-per-second/all
- GET /v1/chat/completions/metrics/tokens-per-second
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from src.db.chat_completion_requests import (
    calculate_tokens_per_second,
    get_all_providers,
    get_models_with_min_one_per_provider,
    get_top_models_by_requests,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat/completions/metrics", tags=["chat-metrics"])


def _format_tokens_per_second_metric(data: list[dict]) -> str:
    """
    Format token throughput data as Prometheus text format.

    Args:
        data: List of dictionaries with tokens_per_second metrics

    Returns:
        Prometheus text format string
    """
    lines = [
        "# HELP gatewayz_tokens_per_second Token throughput (tokens/second) by model and provider",
        "# TYPE gatewayz_tokens_per_second gauge",
    ]

    # Add timestamp comment
    lines.append(f"# Generated: {datetime.now(UTC).isoformat()}")

    if data and len(data) > 0:
        # Add time range info
        time_range = data[0].get("time_range", "all")
        lines.append(f"# Time range: {time_range}")
        lines.append("# Filtered to: top 3 models + minimum 1 per provider")
        lines.append("")

        # Sort by tokens per second (descending)
        sorted_data = sorted(data, key=lambda x: x.get("tokens_per_second", 0), reverse=True)

        # Add metrics
        for item in sorted_data:
            model_name = item.get("model_name", "unknown").replace('"', '\\"')
            provider = item.get("provider", "unknown").replace('"', '\\"')
            tokens_per_sec = item.get("tokens_per_second", 0)
            request_count = item.get("request_count", 0)
            total_tokens = item.get("total_tokens", 0)

            # Prometheus metric line with labels
            metric_line = (
                f'gatewayz_tokens_per_second{{model="{model_name}",'
                f'provider="{provider}",'
                f'requests="{request_count}",'
                f'total_tokens="{total_tokens}"}} {tokens_per_sec}'
            )
            lines.append(metric_line)
    else:
        lines.append("")
        lines.append("# No data available")

    return "\n".join(lines) + "\n"


@router.get("/tokens-per-second/all")
async def get_all_tokens_per_second(
    provider_id: str = Query(..., description="Provider identifier (slug)"),
    model_id: int = Query(..., description="Model ID"),
) -> Response:
    """
    Get tokens per second metrics for all time (no time filtering).

    Used by Prometheus for scraping and Grafana for plotting historical data.

    Query Parameters:
    - provider_id: Provider slug (e.g., "openrouter", "anthropic")
    - model_id: Model ID integer

    Returns:
        Prometheus text format with token throughput metrics
    """
    try:
        logger.info(
            f"Fetching tokens-per-second/all: model_id={model_id}, provider_id={provider_id}"
        )

        # Calculate tokens per second for all time
        result = await _calculate_tokens_per_second_async(
            model_id=model_id, provider_id=provider_id, time_range=None  # No time filtering
        )

        if result is None or result.get("error"):
            logger.warning(
                f"No data found for tokens-per-second/all: "
                f"model_id={model_id}, provider_id={provider_id}"
            )
            # Return empty metrics instead of error
            return Response(_format_tokens_per_second_metric([]), media_type="text/plain")

        # Format as Prometheus and return
        prometheus_text = _format_tokens_per_second_metric([result])
        return Response(prometheus_text, media_type="text/plain")

    except Exception as e:
        logger.error(
            f"Error in get_all_tokens_per_second: model_id={model_id}, "
            f"provider_id={provider_id}, error={str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/tokens-per-second")
async def get_tokens_per_second(
    time: str = Query(..., description="Time range: hour, week, month, 1year, 2year"),
    model_id: int = Query(..., description="Model ID"),
    provider_id: str = Query(..., description="Provider identifier (slug)"),
) -> Response:
    """
    Get tokens per second metrics for specific time range.

    Filtered to top 3 most popular models (by request count) plus
    minimum 1 health check per provider.

    Query Parameters:
    - time: Time range filter - hour, week, month, 1year, or 2year
    - model_id: Model ID integer
    - provider_id: Provider slug (e.g., "openrouter", "anthropic")

    Returns:
        Prometheus text format with token throughput metrics, sorted by tokens/sec
    """
    try:
        logger.info(
            f"Fetching tokens-per-second: time={time}, model_id={model_id}, "
            f"provider_id={provider_id}"
        )

        # Validate time parameter
        valid_times = ["hour", "week", "month", "1year", "2year"]
        if time not in valid_times:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid time parameter. Must be one of: {', '.join(valid_times)}",
            )

        # Get top 3 models by request count
        try:
            top_models = await _get_top_models_async(limit=3)
        except Exception as e:
            logger.error(f"Error fetching top models: {str(e)}", exc_info=True)
            top_models = []

        # Get all providers
        try:
            all_providers = await _get_all_providers_async()
        except Exception as e:
            logger.error(f"Error fetching providers: {str(e)}", exc_info=True)
            all_providers = []

        # Ensure at least 1 model per provider
        try:
            filtered_models = await _get_models_with_min_one_per_provider_async(
                top_models=top_models, all_providers=all_providers
            )
        except Exception as e:
            logger.error(f"Error filtering models: {str(e)}", exc_info=True)
            filtered_models = []

        # Check if requested model is in filtered list
        model_ids_in_list = [m.get("id") for m in filtered_models]
        if model_id not in model_ids_in_list:
            logger.warning(
                f"Model {model_id} not in top 3 or minimum provider coverage. "
                f"Available models: {model_ids_in_list}"
            )
            raise HTTPException(
                status_code=403, detail="Model not in top 3 models or minimum provider coverage"
            )

        # Calculate tokens per second for the requested model
        result = await _calculate_tokens_per_second_async(
            model_id=model_id, provider_id=provider_id, time_range=time
        )

        if result is None or result.get("error"):
            logger.warning(
                f"No data found for tokens-per-second: time={time}, "
                f"model_id={model_id}, provider_id={provider_id}"
            )
            # Return empty metrics instead of error
            return Response(_format_tokens_per_second_metric([]), media_type="text/plain")

        # Format as Prometheus and return
        prometheus_text = _format_tokens_per_second_metric([result])
        return Response(prometheus_text, media_type="text/plain")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error in get_tokens_per_second: time={time}, model_id={model_id}, "
            f"provider_id={provider_id}, error={str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# Async wrapper functions for database operations
async def _get_top_models_async(limit: int = 3) -> list[dict]:
    """Async wrapper for get_top_models_by_requests"""
    return get_top_models_by_requests(limit=limit)


async def _get_all_providers_async() -> list[str]:
    """Async wrapper for get_all_providers"""
    return get_all_providers()


async def _get_models_with_min_one_per_provider_async(
    top_models: list[dict], all_providers: list[str]
) -> list[dict]:
    """Async wrapper for get_models_with_min_one_per_provider"""
    return get_models_with_min_one_per_provider(top_models=top_models, all_providers=all_providers)


async def _calculate_tokens_per_second_async(
    model_id: int, provider_id: str, time_range: str | None = None
) -> dict[str, Any] | None:
    """Async wrapper for calculate_tokens_per_second"""
    result = calculate_tokens_per_second(
        model_id=model_id, provider_id=provider_id, time_range=time_range
    )
    return result if not result.get("error") else None
