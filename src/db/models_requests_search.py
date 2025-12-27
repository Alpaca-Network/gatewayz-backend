"""
Chat Completion Requests Search
Dedicated module for searching and retrieving chat completion request data
"""

import logging
from typing import Any, Optional

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def search_chat_requests(
    query: str,
    provider_name: Optional[str] = None,
    requests_limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Search for models and return their chat completion requests (raw data for graphing).

    This function is specifically designed for retrieving request-level data for:
    - Plotting graphs and visualizations
    - Analyzing individual request patterns
    - Comparing performance across providers

    Examples:
        # Get all GPT-4 requests across all providers (up to 500 per model)
        results = search_chat_requests(query="gpt 4", requests_limit=500)

        # Get GPT-4 requests from OpenRouter only
        results = search_chat_requests(query="gpt 4", provider_name="openrouter", requests_limit=1000)

    Args:
        query: Search query for model name (handles spacing/hyphen variations)
        provider_name: Optional provider slug or name to filter results
        requests_limit: Maximum number of requests to return per model (default: 500)
        offset: Offset for pagination (default: 0)

    Returns:
        List of dictionaries, one per model, containing:
        {
            "model_id": int,
            "model_name": str,
            "model_identifier": str,
            "provider": {"id": int, "name": str, "slug": str, ...},
            "pricing_prompt": float,
            "pricing_completion": float,
            "requests": [
                {
                    "id": uuid,
                    "request_id": str,
                    "input_tokens": int,
                    "output_tokens": int,
                    "total_tokens": int,
                    "processing_time_ms": int,
                    "status": str,
                    "error_message": str or None,
                    "user_id": uuid or None,
                    "created_at": str (ISO datetime)
                },
                ...
            ],
            "total_requests": int,
            "returned_requests": int
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
        or_conditions = []
        for variant in search_variations:
            or_conditions.extend([
                f"model_name.ilike.%{variant}%",
                f"model_id.ilike.%{variant}%",
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

        # For each model, get chat completion requests
        results = []
        for model in unique_models:
            model_id = model.get('id')

            # Get total count
            try:
                count_result = (
                    client.table("chat_completion_requests")
                    .select("*", count="exact")
                    .eq("model_id", model_id)
                    .execute()
                )
                total_requests = count_result.count if count_result.count else 0

                # Get individual requests (ordered by most recent first)
                requests_data = []
                if total_requests > 0:
                    requests_result = (
                        client.table("chat_completion_requests")
                        .select("*")
                        .eq("model_id", model_id)
                        .order("created_at", desc=True)
                        .limit(requests_limit)
                        .range(offset, offset + requests_limit - 1)
                        .execute()
                    )
                    requests_data = requests_result.data or []

            except Exception as requests_error:
                logger.warning(f"Failed to get chat requests for model {model_id}: {requests_error}")
                total_requests = 0
                requests_data = []

            # Build result dictionary
            result = {
                'model_id': model.get('id'),
                'model_name': model.get('model_name'),
                'model_identifier': model.get('model_id'),
                'provider_model_id': model.get('provider_model_id'),
                'provider': model.get('providers', {}),
                'pricing_prompt': float(model.get('pricing_prompt', 0)) if model.get('pricing_prompt') else None,
                'pricing_completion': float(model.get('pricing_completion', 0)) if model.get('pricing_completion') else None,
                'context_length': model.get('context_length'),
                'requests': requests_data,
                'total_requests': total_requests,
                'returned_requests': len(requests_data)
            }

            results.append(result)

        # Sort by total requests (most used first), then by model name
        results.sort(key=lambda x: (-x['total_requests'], x['model_name']))

        logger.info(
            f"Chat requests search: query='{query}', provider='{provider_name}', "
            f"found {len(results)} models with request data"
        )

        return results

    except Exception as e:
        logger.error(
            f"Failed to search chat requests: query='{query}', provider='{provider_name}', error: {e}",
            exc_info=True
        )
        return []


def get_model_requests_by_id(
    model_id: int,
    limit: int = 500,
    offset: int = 0,
    status_filter: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get chat completion requests for a specific model by model ID.

    Args:
        model_id: The model ID
        limit: Maximum number of requests to return
        offset: Offset for pagination
        status_filter: Optional status filter ('completed', 'failed', 'partial')

    Returns:
        Dictionary containing model info and requests
    """
    try:
        client = get_supabase_client()

        # Get model info
        model_result = (
            client.table("models")
            .select("*, providers!inner(*)")
            .eq("id", model_id)
            .single()
            .execute()
        )

        if not model_result.data:
            logger.warning(f"Model not found: {model_id}")
            return {
                'error': 'Model not found',
                'model_id': model_id,
                'requests': [],
                'total_requests': 0
            }

        model = model_result.data

        # Build query for requests
        query = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .eq("model_id", model_id)
        )

        # Apply status filter if provided
        if status_filter:
            query = query.eq("status", status_filter)

        # Get total count
        count_result = query.execute()
        total_requests = count_result.count if count_result.count else 0

        # Get requests with pagination
        requests_result = (
            client.table("chat_completion_requests")
            .select("*")
            .eq("model_id", model_id)
        )

        if status_filter:
            requests_result = requests_result.eq("status", status_filter)

        requests_result = (
            requests_result
            .order("created_at", desc=True)
            .limit(limit)
            .range(offset, offset + limit - 1)
            .execute()
        )

        requests_data = requests_result.data or []

        return {
            'model_id': model.get('id'),
            'model_name': model.get('model_name'),
            'model_identifier': model.get('model_id'),
            'provider': model.get('providers', {}),
            'pricing_prompt': float(model.get('pricing_prompt', 0)) if model.get('pricing_prompt') else None,
            'pricing_completion': float(model.get('pricing_completion', 0)) if model.get('pricing_completion') else None,
            'requests': requests_data,
            'total_requests': total_requests,
            'returned_requests': len(requests_data),
            'offset': offset,
            'limit': limit
        }

    except Exception as e:
        logger.error(f"Failed to get requests for model {model_id}: {e}", exc_info=True)
        return {
            'error': str(e),
            'model_id': model_id,
            'requests': [],
            'total_requests': 0
        }
