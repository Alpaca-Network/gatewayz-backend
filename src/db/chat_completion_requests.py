"""
Chat Completion Requests Database Operations
Handles saving and retrieval of chat completion request metrics
"""

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.utils.db_safety import DatabaseResultError, safe_get_first

logger = logging.getLogger(__name__)


def get_model_id_by_name(model_name: str, provider_name: str | None = None) -> int | None:
    """
    Get the model ID from the models table by model name and provider.

    Lookup strategy when provider is specified (recommended):
    1. First, find provider_id from provider name
    2. Then search models with provider_id filter for better matching
    3. Try matching: provider_model_id, model_name (all case-insensitive)

    Lookup strategy when provider is not specified:
    1. Search across all providers (less reliable)
    2. Return first match

    Args:
        model_name: The model name/identifier (e.g., "gpt-4", "gemini-2.0-flash-exp:free")
        provider_name: Provider name (e.g., "openrouter", "openai") - strongly recommended

    Returns:
        The model ID if found, None otherwise
    """
    try:
        client = get_supabase_client()

        # Step 1: If provider specified, lookup provider_id first (more reliable)
        provider_id = None
        if provider_name:
            provider_result = (
                client.table("providers")
                .select("id")
                .or_(f"slug.ilike.{provider_name},name.ilike.{provider_name}")
                .execute()
            )
            try:
                provider_data = safe_get_first(provider_result, error_message="Provider not found")
                provider_id = provider_data.get("id")
                logger.debug(f"Found provider_id={provider_id} for provider={provider_name}")
            except DatabaseResultError:
                logger.debug(f"Provider not found: {provider_name}")

        # Step 2: Search models with provider filter
        if provider_id:
            # Search with provider_id filter for more accurate matching
            # Try multiple fields: provider_model_id, model_name (model_id column was removed)
            # Use prefix wildcard for "ends with" matching (e.g., "gpt-4o-mini" matches "openai/gpt-4o-mini")
            # This prevents matching longer variants like "gpt-4o-mini-2024-07-18"
            result = (
                client.table("models")
                .select("id, provider_model_id, model_name")
                .eq("provider_id", provider_id)
                .or_(
                    f"provider_model_id.ilike.%{model_name},"
                    f"model_name.ilike.%{model_name}"
                )
                .execute()
            )

            if result.data:
                # Prefer exact match, then case-insensitive match
                for row in result.data:
                    if (
                        row.get("provider_model_id") == model_name
                        or row.get("model_name") == model_name
                    ):
                        logger.debug(
                            f"Found model_id={row.get('id')} for model={model_name}, "
                            f"provider={provider_name} (exact match)"
                        )
                        return row.get("id")

                # Return first case-insensitive match
                try:
                    first_model = safe_get_first(result, error_message="Model not found")
                    logger.debug(
                        f"Found model_id={first_model.get('id')} for model={model_name}, "
                        f"provider={provider_name} (fuzzy match)"
                    )
                    return first_model.get("id")
                except DatabaseResultError:
                    pass  # Fall through to next strategy

        # Step 3: Fallback to search without provider filter (less reliable)
        # Use prefix wildcard for "ends with" matching
        result = (
            client.table("models")
            .select("id, provider_model_id, model_name")
            .or_(
                f"provider_model_id.ilike.%{model_name},"
                f"model_name.ilike.%{model_name}"
            )
            .limit(1)
            .execute()
        )

        try:
            first_model = safe_get_first(result, error_message="Model not found")
            logger.debug(
                f"Found model_id={first_model.get('id')} for model={model_name} "
                f"(no provider filter)"
            )
            return first_model.get("id")
        except DatabaseResultError:
            pass  # Return None below

        logger.warning(
            f"Model not found in database: model_name={model_name}, provider={provider_name}, "
            f"provider_id={provider_id}"
        )
        return None

    except Exception as e:
        logger.error(
            f"Failed to get model ID for {model_name} (provider: {provider_name}): {e}",
            exc_info=True
        )
        return None


def save_chat_completion_request(
    request_id: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    processing_time_ms: int,
    status: str = "completed",
    error_message: str | None = None,
    user_id: int | None = None,
    provider_name: str | None = None,
    model_id: int | None = None,
    api_key_id: int | None = None,
    is_anonymous: bool = False,
) -> dict[str, Any] | None:
    """
    Save a chat completion request to the database.

    Args:
        request_id: Unique identifier for the request
        model_name: Model name used for the request
        input_tokens: Number of tokens in the input/prompt
        output_tokens: Number of tokens in the completion/response
        processing_time_ms: Total time to process the request in milliseconds
        status: Status of the request (completed, failed, partial)
        error_message: Error message if the request failed
        user_id: Optional user identifier (integer) for the request
        provider_name: Optional provider name to help identify the model
        model_id: Optional model ID if already resolved (avoids lookup)
        api_key_id: Optional API key identifier to track which key was used
        is_anonymous: Whether this request was made anonymously (default: False)

    Returns:
        Created record or None on error
    """
    try:
        client = get_supabase_client()

        # Use provided model_id if available, otherwise lookup
        if model_id is None:
            model_id = get_model_id_by_name(model_name, provider_name)

        if model_id is None:
            logger.warning(
                f"Skipping chat completion request save: model not found in database "
                f"(model_name={model_name}, provider={provider_name})"
            )
            return None

        # Prepare the data
        request_data = {
            "request_id": request_id,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "processing_time_ms": processing_time_ms,
            "status": status,
            "is_anonymous": is_anonymous,
        }

        # Add optional fields if provided
        if error_message:
            request_data["error_message"] = error_message
        if user_id:
            request_data["user_id"] = user_id
        if api_key_id:
            request_data["api_key_id"] = api_key_id

        # Insert into database
        result = client.table("chat_completion_requests").insert(request_data).execute()

        if result.data:
            logger.debug(
                f"Chat completion request saved: request_id={request_id}, "
                f"model={model_name}, tokens={input_tokens}+{output_tokens}, "
                f"time={processing_time_ms}ms"
            )
            try:
                return safe_get_first(result, error_message="Insert returned no data")
            except DatabaseResultError as e:
                logger.error(
                    f"Failed to save chat completion request: {e}. "
                    f"Request ID: {request_id}, Model: {model_name}"
                )
                return None
        else:
            logger.error(
                f"Failed to save chat completion request: insert returned no data. "
                f"Request ID: {request_id}, Model: {model_name}, Result: {result}"
            )
            return None

    except Exception as e:
        logger.error(
            f"Failed to save chat completion request {request_id} for model {model_name}: {e}",
            exc_info=True
        )
        # Don't raise - request tracking should not break the main flow
        return None


def get_chat_completion_stats(
    model_id: int | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Get chat completion request statistics with pagination.

    Args:
        model_id: Optional model ID to filter by
        user_id: Optional user ID to filter by
        limit: Maximum number of records to return (default: 100)
        offset: Number of records to skip for pagination (default: 0)

    Returns:
        Dictionary containing:
        - requests: List of chat completion request records
        - total_count: Total number of requests matching filters
        - limit: Applied limit
        - offset: Applied offset
    """
    try:
        client = get_supabase_client()

        # Build query for data retrieval
        query = client.table("chat_completion_requests").select("*")

        if model_id is not None:
            query = query.eq("model_id", model_id)
        if user_id is not None:
            query = query.eq("user_id", user_id)

        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)

        result = query.execute()

        # Get total count with filters applied
        count_query = client.table("chat_completion_requests").select("id", count="exact", head=True)

        if model_id is not None:
            count_query = count_query.eq("model_id", model_id)
        if user_id is not None:
            count_query = count_query.eq("user_id", user_id)

        count_result = count_query.execute()
        total_count = count_result.count if count_result.count is not None else len(result.data or [])

        return {
            "requests": result.data or [],
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }

    except Exception as e:
        logger.error(f"Failed to get chat completion stats: {e}", exc_info=True)
        return {
            "requests": [],
            "total_count": 0,
            "limit": limit,
            "offset": offset,
        }


def get_chat_completion_requests_by_api_key(
    api_key_id: int,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Get chat completion requests for a specific API key with enriched details.

    Args:
        api_key_id: API key ID to filter by
        limit: Maximum number of records to return (default: 100, max: 1000)
        offset: Number of records to skip for pagination (default: 0)

    Returns:
        Dictionary containing:
        - requests: List of chat completion request records with model and user info
        - total_count: Total number of requests for this API key
        - summary: Aggregated statistics (total tokens, avg processing time, etc.)
    """
    try:
        client = get_supabase_client()

        # Get total count for this API key
        count_result = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .eq("api_key_id", api_key_id)
            .execute()
        )
        total_count = count_result.count if hasattr(count_result, "count") else 0

        # Get paginated requests with related data
        query = (
            client.table("chat_completion_requests")
            .select("*, models(id, model_name, provider_model_id, provider_id, providers(name, slug)), users(id, username, email)")
            .eq("api_key_id", api_key_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )

        result = query.execute()
        requests = result.data or []

        # Calculate summary statistics using database aggregation (not limited by pagination)
        # This gets accurate totals across ALL requests for this API key
        try:
            # Try to use RPC function first for better performance
            try:
                summary_result = client.rpc(
                    "get_api_key_request_summary",
                    {"p_api_key_id": api_key_id}
                ).execute()

                if summary_result.data and len(summary_result.data) > 0:
                    summary_data = summary_result.data[0]
                    summary = {
                        "total_requests": total_count,
                        "total_input_tokens": int(summary_data.get("total_input_tokens", 0)),
                        "total_output_tokens": int(summary_data.get("total_output_tokens", 0)),
                        "total_tokens": int(summary_data.get("total_tokens", 0)),
                        "avg_processing_time_ms": round(float(summary_data.get("avg_processing_time_ms", 0)), 2),
                        "completed_requests": int(summary_data.get("completed_requests", 0)),
                        "failed_requests": int(summary_data.get("failed_requests", 0)),
                    }
                else:
                    raise Exception("RPC returned no data")
            except Exception as rpc_error:
                logger.debug(f"RPC function not available, using fallback aggregation: {rpc_error}")

                # Fallback: Use direct aggregation query
                # Fetch ALL requests but only the fields we need for aggregation
                agg_query = (
                    client.table("chat_completion_requests")
                    .select("input_tokens, output_tokens, processing_time_ms, status")
                    .eq("api_key_id", api_key_id)
                )
                agg_result = agg_query.execute()
                all_requests = agg_result.data or []

                if all_requests:
                    total_input = sum(r.get("input_tokens", 0) for r in all_requests)
                    total_output = sum(r.get("output_tokens", 0) for r in all_requests)
                    total_processing = sum(r.get("processing_time_ms", 0) for r in all_requests)
                    completed = sum(1 for r in all_requests if r.get("status") == "completed")
                    failed = sum(1 for r in all_requests if r.get("status") == "failed")

                    summary = {
                        "total_requests": total_count,
                        "total_input_tokens": total_input,
                        "total_output_tokens": total_output,
                        "total_tokens": total_input + total_output,
                        "avg_processing_time_ms": round(total_processing / len(all_requests), 2) if len(all_requests) > 0 else 0,
                        "completed_requests": completed,
                        "failed_requests": failed,
                    }
                else:
                    # No requests found
                    summary = {
                        "total_requests": 0,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "avg_processing_time_ms": 0,
                        "completed_requests": 0,
                        "failed_requests": 0,
                    }
        except Exception as summary_error:
            logger.warning(f"Failed to calculate summary statistics: {summary_error}")
            # Return zero summary on error
            summary = {
                "total_requests": total_count,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "avg_processing_time_ms": 0,
                "completed_requests": 0,
                "failed_requests": 0,
            }

        return {
            "requests": requests,
            "total_count": total_count,
            "summary": summary,
            "limit": limit,
            "offset": offset,
        }

    except Exception as e:
        logger.error(
            f"Failed to get chat completion requests for API key {api_key_id}: {e}",
            exc_info=True
        )
        return {
            "requests": [],
            "total_count": 0,
            "summary": {
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "avg_processing_time_ms": 0,
                "completed_requests": 0,
                "failed_requests": 0,
            },
            "limit": limit,
            "offset": offset,
        }


def get_chat_completion_summary_by_api_key(api_key_id: int) -> dict[str, Any]:
    """
    Get aggregated summary statistics for all chat completion requests by API key.

    Uses database-side aggregation for maximum performance - no data fetching required.
    Optimized for analytics dashboards and monitoring endpoints.

    Args:
        api_key_id: API key ID to get summary for

    Returns:
        Dictionary with aggregated statistics:
        {
            "total_requests": int,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "total_tokens": int,
            "avg_input_tokens": float,
            "avg_output_tokens": float,
            "avg_processing_time_ms": float,
            "completed_requests": int,
            "failed_requests": int,
            "success_rate": float (0-100),
            "first_request_at": str (ISO datetime) or None,
            "last_request_at": str (ISO datetime) or None,
            "total_cost_usd": float
        }
    """
    try:
        client = get_supabase_client()

        # Try to use RPC function first (fastest)
        try:
            result = client.rpc(
                "get_chat_completion_summary_by_api_key",
                {"p_api_key_id": api_key_id}
            ).execute()

            if result.data and len(result.data) > 0:
                summary_data = result.data[0]
                return {
                    "total_requests": int(summary_data.get("total_requests", 0)),
                    "total_input_tokens": int(summary_data.get("total_input_tokens", 0)),
                    "total_output_tokens": int(summary_data.get("total_output_tokens", 0)),
                    "total_tokens": int(summary_data.get("total_tokens", 0)),
                    "avg_input_tokens": round(float(summary_data.get("avg_input_tokens", 0)), 2),
                    "avg_output_tokens": round(float(summary_data.get("avg_output_tokens", 0)), 2),
                    "avg_processing_time_ms": round(float(summary_data.get("avg_processing_time_ms", 0)), 2),
                    "completed_requests": int(summary_data.get("completed_requests", 0)),
                    "failed_requests": int(summary_data.get("failed_requests", 0)),
                    "success_rate": round(float(summary_data.get("success_rate", 0)), 2),
                    "first_request_at": summary_data.get("first_request_at"),
                    "last_request_at": summary_data.get("last_request_at"),
                    "total_cost_usd": round(float(summary_data.get("total_cost_usd", 0)), 2),
                }
            else:
                raise Exception("RPC returned no data")

        except Exception as rpc_error:
            logger.debug(f"RPC function not available, using fallback aggregation: {rpc_error}")

            # Fallback: Use direct aggregation (fetch only needed columns)
            agg_query = (
                client.table("chat_completion_requests")
                .select("input_tokens, output_tokens, processing_time_ms, status, created_at, cost_usd")
                .eq("api_key_id", api_key_id)
            )
            agg_result = agg_query.execute()
            all_requests = agg_result.data or []

            if not all_requests:
                # No requests found - return zero summary
                return {
                    "total_requests": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "avg_input_tokens": 0,
                    "avg_output_tokens": 0,
                    "avg_processing_time_ms": 0,
                    "completed_requests": 0,
                    "failed_requests": 0,
                    "success_rate": 0,
                    "first_request_at": None,
                    "last_request_at": None,
                    "total_cost_usd": 0,
                }

            # Calculate statistics manually
            total_requests = len(all_requests)
            total_input = sum(r.get("input_tokens", 0) for r in all_requests)
            total_output = sum(r.get("output_tokens", 0) for r in all_requests)
            total_processing = sum(r.get("processing_time_ms", 0) for r in all_requests)
            completed = sum(1 for r in all_requests if r.get("status") == "completed")
            failed = sum(1 for r in all_requests if r.get("status") == "failed")
            total_cost = sum(float(r.get("cost_usd", 0)) for r in all_requests)

            # Get time range
            timestamps = [r.get("created_at") for r in all_requests if r.get("created_at")]
            first_request = min(timestamps) if timestamps else None
            last_request = max(timestamps) if timestamps else None

            return {
                "total_requests": total_requests,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "avg_input_tokens": round(total_input / total_requests, 2) if total_requests > 0 else 0,
                "avg_output_tokens": round(total_output / total_requests, 2) if total_requests > 0 else 0,
                "avg_processing_time_ms": round(total_processing / total_requests, 2) if total_requests > 0 else 0,
                "completed_requests": completed,
                "failed_requests": failed,
                "success_rate": round((completed / total_requests * 100), 2) if total_requests > 0 else 0,
                "first_request_at": first_request,
                "last_request_at": last_request,
                "total_cost_usd": round(total_cost, 2),
            }

    except Exception as e:
        logger.error(
            f"Failed to get chat completion summary for API key {api_key_id}: {e}",
            exc_info=True
        )
        # Return zero summary on error
        return {
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "avg_input_tokens": 0,
            "avg_output_tokens": 0,
            "avg_processing_time_ms": 0,
            "completed_requests": 0,
            "failed_requests": 0,
            "success_rate": 0,
            "first_request_at": None,
            "last_request_at": None,
            "total_cost_usd": 0,
        }


def get_chat_completion_summary_by_filters(
    model_id: int | None = None,
    provider_id: int | None = None,
    model_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """
    Get aggregated summary statistics for chat completion requests with flexible filtering.

    Uses database-side aggregation for maximum performance - no data fetching required.
    Optimized for analytics dashboards and model monitoring endpoints.

    Args:
        model_id: Optional model ID to filter by
        provider_id: Optional provider ID to filter by
        model_name: Optional model name to filter by (partial match)
        start_date: Optional start date (ISO format) to filter by
        end_date: Optional end date (ISO format) to filter by

    Returns:
        Dictionary with aggregated statistics:
        {
            "total_requests": int,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "total_tokens": int,
            "avg_input_tokens": float,
            "avg_output_tokens": float,
            "avg_processing_time_ms": float,
            "completed_requests": int,
            "failed_requests": int,
            "success_rate": float (0-100),
            "first_request_at": str (ISO datetime) or None,
            "last_request_at": str (ISO datetime) or None,
            "total_cost_usd": float
        }
    """
    try:
        client = get_supabase_client()

        # Try to use RPC function first (fastest)
        try:
            result = client.rpc(
                "get_chat_completion_summary_by_filters",
                {
                    "p_model_id": model_id,
                    "p_provider_id": provider_id,
                    "p_model_name": model_name,
                    "p_start_date": start_date,
                    "p_end_date": end_date,
                }
            ).execute()

            if result.data and len(result.data) > 0:
                summary_data = result.data[0]
                return {
                    "total_requests": int(summary_data.get("total_requests", 0)),
                    "total_input_tokens": int(summary_data.get("total_input_tokens", 0)),
                    "total_output_tokens": int(summary_data.get("total_output_tokens", 0)),
                    "total_tokens": int(summary_data.get("total_tokens", 0)),
                    "avg_input_tokens": round(float(summary_data.get("avg_input_tokens", 0)), 2),
                    "avg_output_tokens": round(float(summary_data.get("avg_output_tokens", 0)), 2),
                    "avg_processing_time_ms": round(float(summary_data.get("avg_processing_time_ms", 0)), 2),
                    "completed_requests": int(summary_data.get("completed_requests", 0)),
                    "failed_requests": int(summary_data.get("failed_requests", 0)),
                    "success_rate": round(float(summary_data.get("success_rate", 0)), 2),
                    "first_request_at": summary_data.get("first_request_at"),
                    "last_request_at": summary_data.get("last_request_at"),
                    "total_cost_usd": round(float(summary_data.get("total_cost_usd", 0)), 2),
                }
            else:
                raise Exception("RPC returned no data")

        except Exception as rpc_error:
            logger.debug(f"RPC function not available, using fallback aggregation: {rpc_error}")

            # Fallback: Use direct aggregation (fetch only needed columns)
            query = (
                client.table("chat_completion_requests")
                .select("input_tokens, output_tokens, processing_time_ms, status, created_at, cost_usd, models!inner(id, model_name, provider_id, providers!inner(id))")
            )

            # Apply filters
            if model_id is not None:
                query = query.eq("model_id", model_id)
            if provider_id is not None:
                query = query.eq("models.provider_id", provider_id)
            if model_name is not None:
                query = query.ilike("models.model_name", f"%{model_name}%")
            if start_date is not None:
                query = query.gte("created_at", start_date)
            if end_date is not None:
                query = query.lte("created_at", end_date)

            agg_result = query.execute()
            all_requests = agg_result.data or []

            if not all_requests:
                # No requests found - return zero summary
                return {
                    "total_requests": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "avg_input_tokens": 0,
                    "avg_output_tokens": 0,
                    "avg_processing_time_ms": 0,
                    "completed_requests": 0,
                    "failed_requests": 0,
                    "success_rate": 0,
                    "first_request_at": None,
                    "last_request_at": None,
                    "total_cost_usd": 0,
                }

            # Calculate statistics manually
            total_requests = len(all_requests)
            total_input = sum(r.get("input_tokens", 0) for r in all_requests)
            total_output = sum(r.get("output_tokens", 0) for r in all_requests)
            total_processing = sum(r.get("processing_time_ms", 0) for r in all_requests)
            completed = sum(1 for r in all_requests if r.get("status") == "completed")
            failed = sum(1 for r in all_requests if r.get("status") == "failed")
            total_cost = sum(float(r.get("cost_usd", 0)) for r in all_requests)

            # Get time range
            timestamps = [r.get("created_at") for r in all_requests if r.get("created_at")]
            first_request = min(timestamps) if timestamps else None
            last_request = max(timestamps) if timestamps else None

            return {
                "total_requests": total_requests,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "avg_input_tokens": round(total_input / total_requests, 2) if total_requests > 0 else 0,
                "avg_output_tokens": round(total_output / total_requests, 2) if total_requests > 0 else 0,
                "avg_processing_time_ms": round(total_processing / total_requests, 2) if total_requests > 0 else 0,
                "completed_requests": completed,
                "failed_requests": failed,
                "success_rate": round((completed / total_requests * 100), 2) if total_requests > 0 else 0,
                "first_request_at": first_request,
                "last_request_at": last_request,
                "total_cost_usd": round(total_cost, 2),
            }

    except Exception as e:
        logger.error(
            f"Failed to get chat completion summary with filters: {e}",
            exc_info=True
        )
        # Return zero summary on error
        return {
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "avg_input_tokens": 0,
            "avg_output_tokens": 0,
            "avg_processing_time_ms": 0,
            "completed_requests": 0,
            "failed_requests": 0,
            "success_rate": 0,
            "first_request_at": None,
            "last_request_at": None,
            "total_cost_usd": 0,
        }


def search_models_with_chat_summary(
    query: str,
    provider_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Search for models with flexible name matching and return summary statistics.

    This combines model search with aggregated chat completion statistics to show:
    - Model information (name, provider, pricing, etc.)
    - Summary statistics (total requests, averages, success rate)

    Examples:
        - "gpt 4" matches "gpt-4", "gpt4", "gpt-4o", "gpt-4-turbo" from all providers
        - "gpt 4" with provider_name="openrouter" matches only gpt-4 models from OpenRouter
        - "claude" matches all Claude variants across all providers

    Args:
        query: Search query for model name (handles spacing/hyphen variations)
        provider_name: Optional provider slug or name to filter results
        limit: Maximum number of models to return (default: 100)

    Returns:
        List of dictionaries containing model info + summary statistics:
        {
            "model_id": int,
            "model_name": str,
            "model_identifier": str,
            "provider": {"id": int, "name": str, "slug": str, ...},
            "pricing_prompt": float,
            "pricing_completion": float,
            "context_length": int,
            "modality": str,
            "health_status": str,
            "summary": {
                "total_requests": int,
                "avg_input_tokens": float,
                "avg_output_tokens": float,
                "avg_processing_time_ms": float,
                "success_rate": float,
                "completed_requests": int,
                "failed_requests": int,
                "total_tokens": int,
                "last_request_at": str (ISO datetime)
            }
        }
    """
    try:
        client = get_supabase_client()
        import re

        # Create search variations for flexible matching
        search_variations = [query]

        # Normalized (no separators): "gpt 4" -> "gpt4"
        normalized = re.sub(r'[\s\-_.]+', '', query)
        if normalized != query:
            search_variations.append(normalized)

        # Hyphenated: "gpt 4" -> "gpt-4"
        hyphenated = re.sub(r'[\s\-_.]+', '-', query)
        if hyphenated != query and hyphenated not in search_variations:
            search_variations.append(hyphenated)

        # Spaced: "gpt-4" -> "gpt 4"
        spaced = re.sub(r'[\s\-_.]+', ' ', query)
        if spaced != query and spaced not in search_variations:
            search_variations.append(spaced)

        # Underscored: "gpt 4" -> "gpt_4"
        underscored = re.sub(r'[\s\-_.]+', '_', query)
        if underscored != query and underscored not in search_variations:
            search_variations.append(underscored)

        # Build OR conditions for all variations
        # Note: model_id column was removed from models table - use model_name and provider_model_id instead
        or_conditions = []
        for variant in search_variations:
            or_conditions.extend([
                f"model_name.ilike.%{variant}%",
                f"provider_model_id.ilike.%{variant}%",
                f"description.ilike.%{variant}%"
            ])

        # Search models with provider info
        search_query = (
            client.table("models")
            .select("*, providers!inner(*)")
            .or_(','.join(or_conditions))
            .eq("is_active", True)
        )

        # Filter by provider if specified
        if provider_name:
            # We'll filter manually after fetching since we can't easily do case-insensitive
            # provider filtering in the query builder with joined tables
            pass

        search_query = search_query.limit(limit)
        models_result = search_query.execute()
        models = models_result.data or []

        # Filter by provider name if specified
        if provider_name:
            models = [
                m for m in models
                if m.get("providers", {}).get("slug", "").lower() == provider_name.lower()
                or m.get("providers", {}).get("name", "").lower() == provider_name.lower()
            ]

        # Remove duplicates
        seen_ids = set()
        unique_models = []
        for model in models:
            model_id = model.get('id')
            if model_id not in seen_ids:
                seen_ids.add(model_id)
                unique_models.append(model)

        # For each model, get summary statistics
        results = []
        for model in unique_models:
            model_id = model.get('id')

            # Get aggregated summary statistics
            try:
                # Try using PostgreSQL function first (faster)
                try:
                    stats_result = client.rpc(
                        'get_model_chat_stats',
                        {'p_model_id': model_id}
                    ).execute()

                    if stats_result.data and len(stats_result.data) > 0:
                        stats_data = stats_result.data[0]
                        summary = {
                            'total_requests': int(stats_data.get('total_requests', 0)),
                            'avg_input_tokens': float(stats_data.get('avg_input_tokens', 0)),
                            'avg_output_tokens': float(stats_data.get('avg_output_tokens', 0)),
                            'avg_processing_time_ms': float(stats_data.get('avg_processing_time_ms', 0)),
                            'success_rate': float(stats_data.get('success_rate', 0)),
                            'completed_requests': int(stats_data.get('completed_requests', 0)),
                            'failed_requests': int(stats_data.get('failed_requests', 0)),
                            'total_tokens': int(stats_data.get('total_tokens', 0)),
                            'last_request_at': stats_data.get('last_request_at')
                        }
                    else:
                        raise Exception("RPC returned no data")

                except Exception:
                    # Fallback: query directly and compute stats
                    requests_result = (
                        client.table("chat_completion_requests")
                        .select("*")
                        .eq("model_id", model_id)
                        .execute()
                    )

                    requests = requests_result.data or []

                    if requests:
                        total_requests = len(requests)
                        completed = [r for r in requests if r.get('status') == 'completed']
                        failed = [r for r in requests if r.get('status') == 'failed']

                        total_input = sum(r.get('input_tokens', 0) for r in requests)
                        total_output = sum(r.get('output_tokens', 0) for r in requests)
                        total_processing = sum(r.get('processing_time_ms', 0) for r in requests)

                        summary = {
                            'total_requests': total_requests,
                            'avg_input_tokens': round(total_input / total_requests, 2) if total_requests > 0 else 0,
                            'avg_output_tokens': round(total_output / total_requests, 2) if total_requests > 0 else 0,
                            'avg_processing_time_ms': round(total_processing / total_requests, 2) if total_requests > 0 else 0,
                            'success_rate': round((len(completed) / total_requests * 100), 2) if total_requests > 0 else 0,
                            'completed_requests': len(completed),
                            'failed_requests': len(failed),
                            'total_tokens': total_input + total_output,
                            'last_request_at': requests[0].get('created_at') if requests else None
                        }
                    else:
                        summary = {
                            'total_requests': 0,
                            'avg_input_tokens': 0,
                            'avg_output_tokens': 0,
                            'avg_processing_time_ms': 0,
                            'success_rate': 0,
                            'completed_requests': 0,
                            'failed_requests': 0,
                            'total_tokens': 0,
                            'last_request_at': None
                        }

            except Exception as stats_error:
                logger.warning(f"Failed to get summary stats for model {model_id}: {stats_error}")
                summary = {
                    'total_requests': 0,
                    'avg_input_tokens': 0,
                    'avg_output_tokens': 0,
                    'avg_processing_time_ms': 0,
                    'success_rate': 0,
                    'completed_requests': 0,
                    'failed_requests': 0,
                    'total_tokens': 0,
                    'last_request_at': None
                }

            # Build result dictionary
            # Note: model_id column was removed - use model_name as identifier
            result = {
                'model_id': model.get('id'),
                'model_name': model.get('model_name'),
                'model_identifier': model.get('model_name'),  # model_id removed, use model_name
                'provider_model_id': model.get('provider_model_id'),
                'provider': model.get('providers', {}),
                'description': model.get('description'),
                'pricing_prompt': float(model.get('pricing_prompt', 0)) if model.get('pricing_prompt') else None,
                'pricing_completion': float(model.get('pricing_completion', 0)) if model.get('pricing_completion') else None,
                'context_length': model.get('context_length'),
                'modality': model.get('modality'),
                'health_status': model.get('health_status'),
                'supports_streaming': model.get('supports_streaming'),
                'supports_function_calling': model.get('supports_function_calling'),
                'supports_vision': model.get('supports_vision'),
                'is_active': model.get('is_active'),
                'summary': summary
            }

            results.append(result)

        # Sort by total requests (most used first), then by model name
        results.sort(key=lambda x: (-x['summary']['total_requests'], x['model_name']))

        logger.info(
            f"Model search with chat stats: query='{query}', provider='{provider_name}', "
            f"found {len(results)} models"
        )

        return results

    except Exception as e:
        logger.error(
            f"Failed to search models with chat stats: query='{query}', provider='{provider_name}', error: {e}",
            exc_info=True
        )
        return []


def get_top_models_by_requests(limit: int = 3) -> list[dict[str, Any]]:
    """
    Get the top N models by request count.

    Args:
        limit: Number of top models to return (default: 3)

    Returns:
        List of dictionaries with model info and request counts:
        [
            {
                "id": int,
                "model_name": str,
                "provider": str,
                "requests": int,
                "total_tokens": int
            },
            ...
        ]
    """
    try:
        client = get_supabase_client()

        # Note: Ideally we'd use raw SQL like:
        # SELECT m.id, m.model_name, p.slug as provider,
        #        COUNT(ccr.id) as request_count,
        #        COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0) as total_tokens
        # FROM chat_completion_requests ccr
        # JOIN models m ON ccr.model_id = m.id
        # JOIN providers p ON m.provider_id = p.id
        # WHERE ccr.status = 'completed'
        # GROUP BY m.id, m.model_name, p.slug
        # ORDER BY request_count DESC LIMIT N
        # But using Supabase client API instead:
        result = client.table("models").select("*").execute()
        models = result.data or []

        # Get request counts for each model
        models_with_counts = []
        for model in models:
            model_id = model.get('id')

            # Get request count
            requests = client.table("chat_completion_requests").select("*", count='exact').eq(
                "model_id", model_id
            ).eq("status", "completed").execute()

            request_count = requests.count if hasattr(requests, 'count') else len(requests.data or [])

            # Get total tokens
            requests_data = (
                client.table("chat_completion_requests")
                .select("input_tokens, output_tokens")
                .eq("model_id", model_id)
                .eq("status", "completed")
                .execute()
            )

            total_tokens = sum(
                (r.get('input_tokens', 0) + r.get('output_tokens', 0))
                for r in (requests_data.data or [])
            )

            if request_count > 0:
                models_with_counts.append({
                    'id': model_id,
                    'model_name': model.get('model_name'),
                    'provider': model.get('providers', {}).get('slug', 'unknown') if isinstance(
                        model.get('providers'), dict
                    ) else 'unknown',
                    'requests': request_count,
                    'total_tokens': total_tokens
                })

        # Sort by request count and return top N
        models_with_counts.sort(key=lambda x: x['requests'], reverse=True)
        return models_with_counts[:limit]

    except Exception as e:
        logger.error(f"Failed to get top models by requests: {e}", exc_info=True)
        return []


def get_all_providers() -> list[str]:
    """
    Get all distinct provider slugs from the models table.

    Returns:
        List of provider slugs
    """
    try:
        client = get_supabase_client()

        # Get all models with provider info
        result = client.table("models").select("providers!inner(slug)").execute()

        providers = set()
        for model in (result.data or []):
            if isinstance(model.get('providers'), dict):
                provider_slug = model['providers'].get('slug')
                if provider_slug:
                    providers.add(provider_slug)

        return list(providers)

    except Exception as e:
        logger.error(f"Failed to get all providers: {e}", exc_info=True)
        return []


def calculate_tokens_per_second(
    model_id: int,
    provider_id: str,
    time_range: str | None = None
) -> dict[str, Any]:
    """
    Calculate tokens per second throughput for a specific model and provider.

    Args:
        model_id: Model ID to calculate for
        provider_id: Provider ID/slug
        time_range: Time range filter (hour, week, month, 1year, 2year) or None for all time

    Returns:
        Dictionary with:
        {
            "model_id": int,
            "model_name": str,
            "provider": str,
            "tokens_per_second": float,
            "request_count": int,
            "total_tokens": int,
            "time_range": str
        }
    """
    try:
        from datetime import UTC, datetime, timedelta

        client = get_supabase_client()

        # Get time range filter
        start_time = None
        if time_range:
            now = datetime.now(UTC)
            if time_range == "hour":
                start_time = now - timedelta(hours=1)
            elif time_range == "week":
                start_time = now - timedelta(days=7)
            elif time_range == "month":
                start_time = now - timedelta(days=30)
            elif time_range == "1year":
                start_time = now - timedelta(days=365)
            elif time_range == "2year":
                start_time = now - timedelta(days=730)

        # Query requests for this model
        query = (
            client.table("chat_completion_requests")
            .select("input_tokens, output_tokens, processing_time_ms, created_at")
            .eq("model_id", model_id)
            .eq("status", "completed")
        )

        if start_time:
            query = query.gte("created_at", start_time.isoformat())

        result = query.execute()
        requests = result.data or []

        # Get model name and provider
        model_result = client.table("models").select("model_name, providers!inner(slug)").eq(
            "id", model_id
        ).execute()

        model_name = "Unknown"
        provider = "unknown"
        if model_result.data and len(model_result.data) > 0:
            model_data = model_result.data[0]  # Already checked length > 0
            model_name = model_data.get('model_name', 'Unknown')
            if isinstance(model_data.get('providers'), dict):
                provider = model_data['providers'].get('slug', 'unknown')

        # Calculate tokens per second
        if requests:
            total_tokens = sum(
                r.get('input_tokens', 0) + r.get('output_tokens', 0) for r in requests
            )
            total_time_ms = sum(r.get('processing_time_ms', 0) for r in requests)
            total_time_seconds = total_time_ms / 1000 if total_time_ms > 0 else 1

            tokens_per_second = round(total_tokens / total_time_seconds, 2)
        else:
            total_tokens = 0
            tokens_per_second = 0.0

        return {
            'model_id': model_id,
            'model_name': model_name,
            'provider': provider,
            'tokens_per_second': tokens_per_second,
            'request_count': len(requests),
            'total_tokens': total_tokens,
            'time_range': time_range or 'all'
        }

    except Exception as e:
        logger.error(
            f"Failed to calculate tokens per second for model {model_id}: {e}",
            exc_info=True
        )
        return {
            'model_id': model_id,
            'model_name': 'Unknown',
            'provider': 'unknown',
            'tokens_per_second': 0.0,
            'request_count': 0,
            'total_tokens': 0,
            'time_range': time_range or 'all',
            'error': str(e)
        }


def get_models_with_min_one_per_provider(
    top_models: list[dict],
    all_providers: list[str]
) -> list[dict]:
    """
    Ensure at least one model per provider from top models list.
    If a provider is not in top models, adds their most popular model.

    Args:
        top_models: List of top models (from get_top_models_by_requests)
        all_providers: List of all provider slugs

    Returns:
        List of model dictionaries ensuring minimum 1 per provider
    """
    try:
        client = get_supabase_client()

        # Create a dictionary of top models by provider
        models_by_provider = {}
        for model in top_models:
            provider = model.get('provider', 'unknown')
            if provider not in models_by_provider:
                models_by_provider[provider] = model

        # For each provider not in top models, add their most popular model
        for provider in all_providers:
            if provider not in models_by_provider:
                # Get most popular model for this provider
                models = client.table("models").select("id, model_name, providers!inner(slug)").eq(
                    "providers.slug", provider
                ).eq("is_active", True).execute()

                if models.data:
                    # Find the one with most requests
                    best_model = None
                    max_requests = 0

                    for model in models.data:
                        requests = client.table("chat_completion_requests").select(
                            "*", count='exact'
                        ).eq("model_id", model.get('id')).eq("status", "completed").execute()

                        request_count = requests.count if hasattr(requests, 'count') else len(
                            requests.data or []
                        )

                        if request_count > max_requests:
                            max_requests = request_count
                            best_model = {
                                'id': model.get('id'),
                                'model_name': model.get('model_name'),
                                'provider': provider,
                                'requests': request_count,
                                'total_tokens': 0
                            }

                    if best_model:
                        models_by_provider[provider] = best_model

        return list(models_by_provider.values())

    except Exception as e:
        logger.error(
            f"Failed to get models with min one per provider: {e}",
            exc_info=True
        )
        return top_models
