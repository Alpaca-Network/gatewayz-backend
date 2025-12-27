"""
Chat Completion Requests Database Operations
Handles saving and retrieval of chat completion request metrics
"""

import logging
from typing import Any, Optional

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def get_model_id_by_name(model_name: str, provider_name: Optional[str] = None) -> Optional[int]:
    """
    Get the model ID from the models table by model name and provider.

    Lookup strategy when provider is specified (recommended):
    1. First, find provider_id from provider name
    2. Then search models with provider_id filter for better matching
    3. Try matching: model_id, provider_model_id, model_name (all case-insensitive)

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
            if provider_result.data:
                provider_id = provider_result.data[0].get("id")
                logger.debug(f"Found provider_id={provider_id} for provider={provider_name}")

        # Step 2: Search models with provider filter
        if provider_id:
            # Search with provider_id filter for more accurate matching
            # Try multiple fields: model_id, provider_model_id, model_name
            # Use prefix wildcard for "ends with" matching (e.g., "gpt-4o-mini" matches "openai/gpt-4o-mini")
            # This prevents matching longer variants like "gpt-4o-mini-2024-07-18"
            result = (
                client.table("models")
                .select("id, model_id, provider_model_id, model_name")
                .eq("provider_id", provider_id)
                .or_(
                    f"model_id.ilike.%{model_name},"
                    f"provider_model_id.ilike.%{model_name},"
                    f"model_name.ilike.%{model_name}"
                )
                .execute()
            )

            if result.data:
                # Prefer exact match, then case-insensitive match
                for row in result.data:
                    if (
                        row.get("model_id") == model_name
                        or row.get("provider_model_id") == model_name
                        or row.get("model_name") == model_name
                    ):
                        logger.debug(
                            f"Found model_id={row.get('id')} for model={model_name}, "
                            f"provider={provider_name} (exact match)"
                        )
                        return row.get("id")

                # Return first case-insensitive match
                logger.debug(
                    f"Found model_id={result.data[0].get('id')} for model={model_name}, "
                    f"provider={provider_name} (fuzzy match)"
                )
                return result.data[0].get("id")

        # Step 3: Fallback to search without provider filter (less reliable)
        # Use prefix wildcard for "ends with" matching
        result = (
            client.table("models")
            .select("id, model_id, provider_model_id, model_name")
            .or_(
                f"model_id.ilike.%{model_name},"
                f"provider_model_id.ilike.%{model_name},"
                f"model_name.ilike.%{model_name}"
            )
            .limit(1)
            .execute()
        )

        if result.data:
            logger.debug(
                f"Found model_id={result.data[0].get('id')} for model={model_name} "
                f"(no provider filter)"
            )
            return result.data[0].get("id")

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
    error_message: Optional[str] = None,
    user_id: Optional[str] = None,
    provider_name: Optional[str] = None,
    model_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
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
        user_id: Optional user identifier for the request
        provider_name: Optional provider name to help identify the model
        model_id: Optional model ID if already resolved (avoids lookup)

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
        }

        # Add optional fields if provided
        if error_message:
            request_data["error_message"] = error_message
        if user_id:
            request_data["user_id"] = user_id

        # Insert into database
        result = client.table("chat_completion_requests").insert(request_data).execute()

        if result.data:
            logger.debug(
                f"Chat completion request saved: request_id={request_id}, "
                f"model={model_name}, tokens={input_tokens}+{output_tokens}, "
                f"time={processing_time_ms}ms"
            )
            return result.data[0]
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
    model_id: Optional[int] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Get chat completion request statistics.

    Args:
        model_id: Optional model ID to filter by
        user_id: Optional user ID to filter by
        limit: Maximum number of records to return

    Returns:
        List of chat completion request records
    """
    try:
        client = get_supabase_client()

        query = client.table("chat_completion_requests").select("*")

        if model_id is not None:
            query = query.eq("model_id", model_id)
        if user_id is not None:
            query = query.eq("user_id", user_id)

        query = query.order("created_at", desc=True).limit(limit)

        result = query.execute()
        return result.data or []

    except Exception as e:
        logger.error(f"Failed to get chat completion stats: {e}", exc_info=True)
        return []


def search_models_with_chat_summary(
    query: str,
    provider_name: Optional[str] = None,
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
            result = {
                'model_id': model.get('id'),
                'model_name': model.get('model_name'),
                'model_identifier': model.get('model_id'),
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
