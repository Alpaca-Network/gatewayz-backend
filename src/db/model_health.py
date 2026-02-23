"""
Database operations for model health tracking.

This module handles recording and querying model call metrics,
including response times, success rates, and health status.
"""

import logging
from datetime import datetime, UTC

from postgrest.exceptions import APIError

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def record_model_call(
    provider: str,
    model: str,
    response_time_ms: float,
    status: str,
    error_message: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    gateway: str | None = None,
) -> dict:
    """
    Record a model call and update health tracking metrics.

    This function uses an atomic upsert operation to either insert a new record
    or update an existing one for the provider-model combination, preventing
    race conditions under concurrent requests.

    Args:
        provider: The AI provider/gateway name (e.g., 'openrouter', 'featherless')
        model: The model identifier
        response_time_ms: Response time in milliseconds
        status: Call status ('success', 'error', 'timeout', 'rate_limited', etc.)
        error_message: Optional error message if status is 'error'
        input_tokens: Optional number of input tokens used
        output_tokens: Optional number of output tokens generated
        total_tokens: Optional total tokens (input + output)
        gateway: Optional gateway name (defaults to provider if not specified)

    Returns:
        Dictionary with the updated/created record, or empty dict if table doesn't exist
    """
    # Use provider as gateway if gateway not explicitly provided
    effective_gateway = gateway if gateway else provider

    try:
        supabase = get_supabase_client()

        # First, try to get the existing record for calculating new averages
        existing = (
            supabase.table("model_health_tracking")
            .select("*")
            .eq("provider", provider)
            .eq("model", model)
            .execute()
        )
    except APIError as e:
        # Table doesn't exist (likely in test environment or migration not run)
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.debug(
                f"model_health_tracking table not found - skipping health tracking "
                f"for {provider}/{model}. Run migrations to enable health tracking."
            )
            return {}
        # Re-raise other API errors
        raise
    except Exception as e:
        logger.warning(f"Failed to record model health for {provider}/{model}: {e}")
        return {}

    try:
        # Prepare upsert data - always include provider and model for the upsert
        if existing.data and len(existing.data) > 0:
            # Calculate updated values based on existing record
            record = existing.data[0]
            new_call_count = record["call_count"] + 1
            new_success_count = record["success_count"] + (1 if status == "success" else 0)
            new_error_count = record["error_count"] + (1 if status != "success" else 0)

            # Calculate new average response time
            if record["average_response_time_ms"] is not None:
                new_avg = (
                    (record["average_response_time_ms"] * record["call_count"]) + response_time_ms
                ) / new_call_count
            else:
                new_avg = response_time_ms

            upsert_data = {
                "provider": provider,
                "model": model,
                "gateway": effective_gateway,
                "last_response_time_ms": response_time_ms,
                "last_status": status,
                "last_called_at": datetime.now(UTC).isoformat(),
                "call_count": new_call_count,
                "success_count": new_success_count,
                "error_count": new_error_count,
                "average_response_time_ms": new_avg,
            }

            if error_message:
                upsert_data["last_error_message"] = error_message

            if input_tokens is not None:
                upsert_data["input_tokens"] = input_tokens
            if output_tokens is not None:
                upsert_data["output_tokens"] = output_tokens
            if total_tokens is not None:
                upsert_data["total_tokens"] = total_tokens
        else:
            # New record with initial values
            upsert_data = {
                "provider": provider,
                "model": model,
                "gateway": effective_gateway,
                "last_response_time_ms": response_time_ms,
                "last_status": status,
                "last_called_at": datetime.now(UTC).isoformat(),
                "call_count": 1,
                "success_count": 1 if status == "success" else 0,
                "error_count": 1 if status != "success" else 0,
                "average_response_time_ms": response_time_ms,
            }

            if error_message:
                upsert_data["last_error_message"] = error_message

            if input_tokens is not None:
                upsert_data["input_tokens"] = input_tokens
            if output_tokens is not None:
                upsert_data["output_tokens"] = output_tokens
            if total_tokens is not None:
                upsert_data["total_tokens"] = total_tokens

        # Use atomic upsert to prevent race conditions
        # on_conflict specifies the primary key columns for conflict resolution
        result = (
            supabase.table("model_health_tracking")
            .upsert(upsert_data, on_conflict="provider,model")
            .execute()
        )

        return result.data[0] if result.data else {}

    except APIError as e:
        # Table doesn't exist or other API error
        error_str = str(e)
        if "PGRST205" in error_str or "Could not find the table" in error_str:
            logger.debug(
                f"model_health_tracking table not found during update - skipping health tracking "
                f"for {provider}/{model}"
            )
            return {}
        # Handle duplicate key by logging a warning instead of failing
        # This can happen if two requests race to insert the same record
        if "23505" in error_str or "duplicate key" in error_str.lower():
            logger.warning(
                f"Race condition detected for health metric {provider}/{model}, "
                "record was already created by another request"
            )
            return {}
        # Re-raise other API errors
        raise
    except Exception as e:
        error_str = str(e)
        # Handle duplicate key errors that might come through as generic exceptions
        if "23505" in error_str or "duplicate key" in error_str.lower():
            logger.warning(
                f"Race condition detected for health metric {provider}/{model}, "
                "record was already created by another request"
            )
            return {}
        logger.warning(f"Failed to update model health for {provider}/{model}: {e}")
        return {}


def get_model_health(provider: str, model: str) -> dict | None:
    """
    Get health tracking data for a specific provider-model combination.

    Args:
        provider: The AI provider name
        model: The model identifier

    Returns:
        Dictionary with health tracking data or None if not found
    """
    try:
        supabase = get_supabase_client()

        result = (
            supabase.table("model_health_tracking")
            .select("*")
            .eq("provider", provider)
            .eq("model", model)
            .execute()
        )

        return result.data[0] if result.data else None

    except APIError as e:
        # Table doesn't exist
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.debug("model_health_tracking table not found")
            return None
        raise
    except Exception as e:
        logger.warning(f"Failed to get model health for {provider}/{model}: {e}")
        return None


def get_all_model_health(
    provider: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Get health tracking data for all models with optional filtering.

    Args:
        provider: Optional provider filter
        status: Optional status filter ('success', 'error', etc.)
        limit: Maximum number of records to return
        offset: Number of records to skip

    Returns:
        List of health tracking records
    """
    try:
        supabase = get_supabase_client()

        query = supabase.table("model_health_tracking").select("*")

        if provider:
            query = query.eq("provider", provider)

        if status:
            query = query.eq("last_status", status)

        query = query.order("last_called_at", desc=True).range(offset, offset + limit - 1)

        result = query.execute()

        return result.data if result.data else []

    except APIError as e:
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.debug("model_health_tracking table not found")
            return []
        raise
    except Exception as e:
        logger.warning(f"Failed to get all model health: {e}")
        return []


def get_unhealthy_models(
    error_threshold: float = 0.2,  # 20% error rate
    min_calls: int = 10,
) -> list[dict]:
    """
    Get models with high error rates (unhealthy models).

    Args:
        error_threshold: Minimum error rate to be considered unhealthy (0.0 to 1.0)
        min_calls: Minimum number of calls required for a model to be evaluated

    Returns:
        List of unhealthy model records
    """
    try:
        supabase = get_supabase_client()

        result = (
            supabase.table("model_health_tracking")
            .select("*")
            .gte("call_count", min_calls)
            .execute()
        )

        if not result.data:
            return []

        # Filter for models with error rate above threshold
        unhealthy = []
        for record in result.data:
            if record["call_count"] > 0:
                error_rate = record["error_count"] / record["call_count"]
                if error_rate >= error_threshold:
                    record["error_rate"] = error_rate
                    unhealthy.append(record)

        return sorted(unhealthy, key=lambda x: x["error_rate"], reverse=True)

    except APIError as e:
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.debug("model_health_tracking table not found")
            return []
        raise
    except Exception as e:
        logger.warning(f"Failed to get unhealthy models: {e}")
        return []


def get_model_health_stats() -> dict:
    """
    Get aggregate statistics for model health tracking.

    Returns:
        Dictionary with aggregate stats:
        - total_models: Total number of tracked models
        - total_calls: Total number of calls across all models
        - total_success: Total successful calls
        - total_errors: Total failed calls
        - average_response_time: Average response time across all models
    """
    try:
        supabase = get_supabase_client()

        result = supabase.table("model_health_tracking").select("*").execute()

        if not result.data:
            return {
                "total_models": 0,
                "total_calls": 0,
                "total_success": 0,
                "total_errors": 0,
                "average_response_time": 0,
            }

        total_calls = sum(r["call_count"] for r in result.data)
        total_success = sum(r["success_count"] for r in result.data)
        total_errors = sum(r["error_count"] for r in result.data)

        # Calculate weighted average response time
        total_weighted_time = sum(
            r["average_response_time_ms"] * r["call_count"]
            for r in result.data
            if r["average_response_time_ms"] is not None
        )
        avg_response_time = total_weighted_time / total_calls if total_calls > 0 else 0

        return {
            "total_models": len(result.data),
            "total_calls": total_calls,
            "total_success": total_success,
            "total_errors": total_errors,
            "average_response_time": avg_response_time,
            "success_rate": total_success / total_calls if total_calls > 0 else 0,
        }

    except APIError as e:
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.debug("model_health_tracking table not found")
            return {
                "total_models": 0,
                "total_calls": 0,
                "total_success": 0,
                "total_errors": 0,
                "average_response_time": 0,
            }
        raise
    except Exception as e:
        logger.warning(f"Failed to get model health stats: {e}")
        return {
            "total_models": 0,
            "total_calls": 0,
            "total_success": 0,
            "total_errors": 0,
            "average_response_time": 0,
        }


def get_provider_health_summary(provider: str) -> dict:
    """
    Get health summary for all models from a specific provider.

    Args:
        provider: The AI provider name

    Returns:
        Dictionary with provider-level statistics
    """
    try:
        supabase = get_supabase_client()

        result = (
            supabase.table("model_health_tracking")
            .select("*")
            .eq("provider", provider)
            .execute()
        )

        if not result.data:
            return {
                "provider": provider,
                "total_models": 0,
                "total_calls": 0,
                "total_success": 0,
                "total_errors": 0,
                "average_response_time": 0,
                "success_rate": 0,
            }

        total_calls = sum(r["call_count"] for r in result.data)
        total_success = sum(r["success_count"] for r in result.data)
        total_errors = sum(r["error_count"] for r in result.data)

        total_weighted_time = sum(
            r["average_response_time_ms"] * r["call_count"]
            for r in result.data
            if r["average_response_time_ms"] is not None
        )
        avg_response_time = total_weighted_time / total_calls if total_calls > 0 else 0

        return {
            "provider": provider,
            "total_models": len(result.data),
            "total_calls": total_calls,
            "total_success": total_success,
            "total_errors": total_errors,
            "average_response_time": avg_response_time,
            "success_rate": total_success / total_calls if total_calls > 0 else 0,
        }

    except APIError as e:
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.debug(f"model_health_tracking table not found for provider {provider}")
            return {
                "provider": provider,
                "total_models": 0,
                "total_calls": 0,
                "total_success": 0,
                "total_errors": 0,
                "average_response_time": 0,
                "success_rate": 0,
            }
        raise
    except Exception as e:
        logger.warning(f"Failed to get provider health summary for {provider}: {e}")
        return {
            "provider": provider,
            "total_models": 0,
            "total_calls": 0,
            "total_success": 0,
            "total_errors": 0,
            "average_response_time": 0,
            "success_rate": 0,
        }


def delete_old_health_records(days: int = 30) -> int:
    """
    Delete health tracking records older than specified days
    that have no recent activity.

    Args:
        days: Number of days to keep records

    Returns:
        Number of records deleted
    """
    supabase = get_supabase_client()

    cutoff_date = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=days)

    result = (
        supabase.table("model_health_tracking")
        .delete()
        .lt("last_called_at", cutoff_date.isoformat())
        .execute()
    )

    return len(result.data) if result.data else 0


# Import for delete function
from datetime import timedelta
