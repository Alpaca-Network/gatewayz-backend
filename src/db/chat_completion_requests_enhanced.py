"""
Enhanced Chat Completion Requests Database Operations
Extends chat_completion_requests with cost tracking functionality
"""

import logging
from typing import Any, Optional

from src.config.supabase_config import get_supabase_client
from src.db.chat_completion_requests import (
    get_model_id_by_name,
    save_chat_completion_request as save_base_request,
)

logger = logging.getLogger(__name__)


def save_chat_completion_request_with_cost(
    request_id: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    processing_time_ms: int,
    cost_usd: float,
    input_cost_usd: float,
    output_cost_usd: float,
    pricing_source: str = "calculated",
    status: str = "completed",
    error_message: Optional[str] = None,
    user_id: Optional[int] = None,
    provider_name: Optional[str] = None,
    model_id: Optional[int] = None,
    api_key_id: Optional[int] = None,
    is_anonymous: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """
    Save a chat completion request with cost tracking

    Args:
        request_id: Unique identifier for the request
        model_name: Model name used for the request
        input_tokens: Number of tokens in the input/prompt
        output_tokens: Number of tokens in the completion/response
        processing_time_ms: Total time to process the request in milliseconds
        cost_usd: Total cost in USD
        input_cost_usd: Cost for input tokens in USD
        output_cost_usd: Cost for output tokens in USD
        pricing_source: Source of pricing ('calculated', 'model_pricing', 'manual_pricing', etc.)
        status: Status of the request (completed, failed, partial)
        error_message: Error message if the request failed
        user_id: Optional user identifier (integer) for the request
        provider_name: Optional provider name to help identify the model
        model_id: Optional model ID if already resolved (avoids lookup)
        api_key_id: Optional API key identifier to track which key was used
        is_anonymous: Whether this request was made anonymously (default: False)
        metadata: Optional JSONB metadata (e.g., for Butter.dev cache tracking:
                  {"butter_cache_hit": true, "actual_cost_usd": 0.001})

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

        # Prepare the data with cost fields
        request_data = {
            "request_id": request_id,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "processing_time_ms": processing_time_ms,
            "status": status,
            "is_anonymous": is_anonymous,
            "cost_usd": round(cost_usd, 6),
            "input_cost_usd": round(input_cost_usd, 6),
            "output_cost_usd": round(output_cost_usd, 6),
            "pricing_source": pricing_source,
        }

        # Add optional fields if provided
        if error_message:
            request_data["error_message"] = error_message
        if user_id:
            request_data["user_id"] = user_id
        if api_key_id:
            request_data["api_key_id"] = api_key_id
        if metadata:
            request_data["metadata"] = metadata

        # Insert into database
        result = client.table("chat_completion_requests").insert(request_data).execute()

        if result.data and len(result.data) > 0:
            logger.debug(
                f"Chat completion request saved with cost: request_id={request_id}, "
                f"model={model_name}, tokens={input_tokens}+{output_tokens}, "
                f"cost=${cost_usd:.6f}, time={processing_time_ms}ms"
            )
            return result.data[0]
        else:
            logger.error(
                f"Failed to save chat completion request: insert returned no data. "
                f"Request ID: {request_id}, Model: {model_name}"
            )
            return None

    except Exception as e:
        logger.error(
            f"Failed to save chat completion request {request_id} for model {model_name}: {e}",
            exc_info=True
        )
        # Don't raise - request tracking should not break the main flow
        return None


def update_request_cost(
    request_id: str,
    cost_usd: float,
    input_cost_usd: float,
    output_cost_usd: float,
    pricing_source: str = "updated"
) -> bool:
    """
    Update cost information for an existing request

    Useful for backfilling or correcting cost data.

    Args:
        request_id: Request ID to update
        cost_usd: Total cost in USD
        input_cost_usd: Input cost in USD
        output_cost_usd: Output cost in USD
        pricing_source: Source of the updated pricing

    Returns:
        True if successful, False otherwise
    """
    try:
        client = get_supabase_client()

        update_data = {
            "cost_usd": round(cost_usd, 6),
            "input_cost_usd": round(input_cost_usd, 6),
            "output_cost_usd": round(output_cost_usd, 6),
            "pricing_source": pricing_source,
        }

        result = (
            client.table("chat_completion_requests")
            .update(update_data)
            .eq("request_id", request_id)
            .execute()
        )

        if result.data:
            logger.info(f"Updated cost for request {request_id}: ${cost_usd:.6f}")
            return True
        else:
            logger.warning(f"No request found with ID {request_id} for cost update")
            return False

    except Exception as e:
        logger.error(f"Failed to update cost for request {request_id}: {e}", exc_info=True)
        return False


def backfill_request_costs(
    limit: int = 1000,
    offset: int = 0
) -> dict[str, Any]:
    """
    Backfill cost calculations for requests that don't have cost data

    Useful for filling in historical data.

    Args:
        limit: Maximum number of requests to process
        offset: Offset for batch processing

    Returns:
        Dict with statistics about the backfill operation
    """
    try:
        client = get_supabase_client()

        # Get requests without cost data - now using model_pricing table
        result = (
            client.table("chat_completion_requests")
            .select("request_id, model_id, input_tokens, output_tokens")
            .is_("cost_usd", "null")
            .eq("status", "completed")
            .range(offset, offset + limit - 1)
            .execute()
        )

        requests = result.data or []
        updated_count = 0
        total_cost_calculated = 0.0

        for req in requests:
            model_id = req.get("model_id")
            if not model_id:
                continue

            # Fetch pricing from model_pricing table
            pricing_result = (
                client.table("model_pricing")
                .select("price_per_input_token, price_per_output_token")
                .eq("model_id", model_id)
                .single()
                .execute()
            )

            if not pricing_result.data:
                logger.debug(f"No pricing data found for model_id={model_id}, skipping cost backfill")
                continue

            pricing_data = pricing_result.data
            input_tokens = req.get("input_tokens", 0)
            output_tokens = req.get("output_tokens", 0)

            # Get per-token pricing from model_pricing table
            prompt_price = float(pricing_data.get("price_per_input_token", 0) or 0)
            completion_price = float(pricing_data.get("price_per_output_token", 0) or 0)

            # Calculate costs
            input_cost = input_tokens * prompt_price
            output_cost = output_tokens * completion_price
            total_cost = input_cost + output_cost

            # Update the request
            if update_request_cost(
                request_id=req.get("request_id"),
                cost_usd=total_cost,
                input_cost_usd=input_cost,
                output_cost_usd=output_cost,
                pricing_source="backfilled"
            ):
                updated_count += 1
                total_cost_calculated += total_cost

        return {
            "processed": len(requests),
            "updated": updated_count,
            "total_cost_calculated": round(total_cost_calculated, 6),
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Error during cost backfill: {e}", exc_info=True)
        return {
            "processed": 0,
            "updated": 0,
            "total_cost_calculated": 0.0,
            "error": str(e)
        }


def get_requests_with_cost(
    model_id: Optional[int] = None,
    user_id: Optional[int] = None,
    provider_slug: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> dict[str, Any]:
    """
    Get chat completion requests with cost information

    Args:
        model_id: Filter by model ID
        user_id: Filter by user ID
        provider_slug: Filter by provider slug
        start_date: Filter by start date (ISO format)
        end_date: Filter by end date (ISO format)
        limit: Maximum results
        offset: Offset for pagination

    Returns:
        Dict with requests and cost summary
    """
    try:
        client = get_supabase_client()

        # Build query
        query = client.table("chat_completion_requests").select(
            "*, models(model_name, provider_id, providers(name, slug))"
        )

        if model_id:
            query = query.eq("model_id", model_id)
        if user_id:
            query = query.eq("user_id", user_id)
        if start_date:
            query = query.gte("created_at", start_date)
        if end_date:
            query = query.lte("created_at", end_date)

        # Filter by provider if specified (requires join)
        # This is more complex, we'll handle it in post-processing

        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)

        result = query.execute()
        requests = result.data or []

        # Filter by provider slug if needed
        if provider_slug:
            requests = [
                r for r in requests
                if r.get("models", {}).get("providers", {}).get("slug") == provider_slug
            ]

        # Calculate summary
        total_cost = sum(float(r.get("cost_usd", 0) or 0) for r in requests)
        total_input_cost = sum(float(r.get("input_cost_usd", 0) or 0) for r in requests)
        total_output_cost = sum(float(r.get("output_cost_usd", 0) or 0) for r in requests)
        total_tokens = sum(
            r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in requests
        )

        return {
            "requests": requests,
            "summary": {
                "total_requests": len(requests),
                "total_cost_usd": round(total_cost, 6),
                "total_input_cost_usd": round(total_input_cost, 6),
                "total_output_cost_usd": round(total_output_cost, 6),
                "total_tokens": total_tokens,
                "avg_cost_per_request": round(total_cost / len(requests), 6) if requests else 0,
            },
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Error getting requests with cost: {e}", exc_info=True)
        return {
            "requests": [],
            "summary": {
                "total_requests": 0,
                "total_cost_usd": 0,
                "total_input_cost_usd": 0,
                "total_output_cost_usd": 0,
                "total_tokens": 0,
                "avg_cost_per_request": 0,
            },
            "limit": limit,
            "offset": offset,
            "error": str(e)
        }
