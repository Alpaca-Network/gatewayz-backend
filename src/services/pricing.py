"""
Pricing Service
Handles model pricing calculations and credit cost computation
"""

import logging
import time
from typing import Any

from src.services.model_transformations import apply_model_alias

logger = logging.getLogger(__name__)

# In-memory cache for database pricing queries
# Structure: {model_id: {"data": dict, "timestamp": float}}
_pricing_cache: dict[str, dict[str, Any]] = {}
_pricing_cache_ttl = 300  # 5 minutes TTL


def clear_pricing_cache(model_id: str | None = None) -> None:
    """Clear pricing cache (for testing or explicit invalidation)"""
    global _pricing_cache
    if model_id:
        if model_id in _pricing_cache:
            del _pricing_cache[model_id]
            logger.debug(f"Cleared pricing cache for model {model_id}")
    else:
        _pricing_cache.clear()
        logger.info("Cleared entire pricing cache")


def get_pricing_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring"""
    return {
        "cached_models": len(_pricing_cache),
        "ttl_seconds": _pricing_cache_ttl,
    }


def _get_pricing_from_database(model_id: str, candidate_ids: set[str]) -> dict[str, float] | None:
    """
    Query database models table for pricing information.

    Args:
        model_id: Original model ID
        candidate_ids: Set of model ID variations to try

    Returns:
        Pricing dict with prompt/completion prices, or None if not found
    """
    try:
        from src.config.supabase_config import get_supabase_client
        from src.services.prometheus_metrics import track_database_query

        client = get_supabase_client()

        # Try querying with each candidate ID
        for candidate in candidate_ids:
            if not candidate:
                continue

            try:
                with track_database_query(table="models", operation="select"):
                    # Query by model_id first
                    result = (
                        client.table("models")
                        .select("model_id, pricing_prompt, pricing_completion")
                        .eq("model_id", candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )

                if result.data and len(result.data) > 0:
                    row = result.data[0]
                    prompt_price = float(row.get("pricing_prompt") or 0)
                    completion_price = float(row.get("pricing_completion") or 0)

                    # Ensure non-negative values
                    prompt_price = max(0.0, prompt_price)
                    completion_price = max(0.0, completion_price)

                    logger.info(
                        f"[DB] Found pricing for {model_id} (matched: {candidate}): "
                        f"prompt=${prompt_price}, completion=${completion_price}"
                    )

                    return {
                        "prompt": prompt_price,
                        "completion": completion_price,
                        "found": True,
                        "source": "database"
                    }

                # Try provider_model_id if model_id didn't match
                with track_database_query(table="models", operation="select"):
                    result = (
                        client.table("models")
                        .select("model_id, pricing_prompt, pricing_completion")
                        .eq("provider_model_id", candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )

                if result.data and len(result.data) > 0:
                    row = result.data[0]
                    prompt_price = float(row.get("pricing_prompt") or 0)
                    completion_price = float(row.get("pricing_completion") or 0)

                    # Ensure non-negative values
                    prompt_price = max(0.0, prompt_price)
                    completion_price = max(0.0, completion_price)

                    logger.info(
                        f"[DB] Found pricing for {model_id} (matched provider_model_id: {candidate}): "
                        f"prompt=${prompt_price}, completion=${completion_price}"
                    )

                    return {
                        "prompt": prompt_price,
                        "completion": completion_price,
                        "found": True,
                        "source": "database"
                    }

            except Exception as e:
                logger.debug(f"Database query failed for candidate {candidate}: {e}")
                continue

        # No match found in database
        return None

    except Exception as e:
        logger.warning(f"Database pricing lookup error for {model_id}: {e}")
        return None


def _get_pricing_from_cache_fallback(model_id: str, candidate_ids: set[str]) -> dict[str, float] | None:
    """
    Fallback to in-memory cache from provider APIs (old behavior).

    Args:
        model_id: Original model ID
        candidate_ids: Set of model ID variations to try

    Returns:
        Pricing dict with prompt/completion prices, or None if not found
    """
    try:
        from src.services.models import get_cached_models

        models = get_cached_models("all")
        if not models:
            return None

        # Find the specific model - try both original and normalized IDs
        for model in models:
            model_catalog_id = model.get("id")
            model_slug = model.get("slug")

            # Match against candidate IDs
            if (model_catalog_id and model_catalog_id in candidate_ids) or (
                model_slug and model_slug in candidate_ids
            ):
                pricing = model.get("pricing", {})

                # Convert pricing strings to floats, handling None and empty strings
                prompt_price = float(pricing.get("prompt", "0") or "0")
                prompt_price = max(0.0, prompt_price)

                completion_price = float(pricing.get("completion", "0") or "0")
                completion_price = max(0.0, completion_price)

                logger.info(
                    f"[CACHE FALLBACK] Found pricing for {model_id}: "
                    f"prompt=${prompt_price}, completion=${completion_price}"
                )

                return {
                    "prompt": prompt_price,
                    "completion": completion_price,
                    "found": True,
                    "source": "cache_fallback"
                }

        return None

    except Exception as e:
        logger.warning(f"Cache fallback pricing lookup error for {model_id}: {e}")
        return None


def get_model_pricing(model_id: str) -> dict[str, float]:
    """
    Get pricing information for a specific model.

    Pricing lookup priority:
    1. In-memory cache (5min TTL)
    2. Database models table (primary source)
    3. Provider API cache (fallback for resilience)
    4. Default pricing ($0.00002 per token)

    Args:
        model_id: The model ID (e.g., "openai/gpt-4", "anthropic/claude-3-opus")

    Returns:
        Dictionary with pricing info:
        {
            "prompt": float,      # Cost per prompt token in USD
            "completion": float,  # Cost per completion token in USD
            "found": bool,        # Whether the model was found
            "source": str         # Source of pricing: database|cache_fallback|default
        }
    """
    try:
        # Import here to avoid circular imports
        from src.services.models import _is_building_catalog

        # If we're building the catalog, return default pricing to avoid circular dependency
        if _is_building_catalog():
            logger.debug(f"Returning default pricing for {model_id} (catalog building in progress)")
            return {"prompt": 0.00002, "completion": 0.00002, "found": False, "source": "default"}

        # Build candidate IDs for lookup (original + normalized variations)
        candidate_ids = {model_id}

        # Strip provider-specific suffixes for matching
        # HuggingFace adds :hf-inference, other providers may add similar suffixes
        normalized_model_id = model_id
        provider_suffixes = [":hf-inference", ":openai", ":anthropic"]
        for suffix in provider_suffixes:
            if normalized_model_id.endswith(suffix):
                normalized_model_id = normalized_model_id[: -len(suffix)]
                logger.debug(
                    f"Normalized model ID from '{model_id}' to '{normalized_model_id}' for pricing lookup"
                )
                break

        if normalized_model_id != model_id:
            candidate_ids.add(normalized_model_id)

        # Apply model aliases
        aliased_model_id = apply_model_alias(normalized_model_id)
        if aliased_model_id and aliased_model_id != normalized_model_id:
            logger.info(
                "Resolved pricing lookup alias: '%s' -> '%s'",
                normalized_model_id,
                aliased_model_id,
            )
            normalized_model_id = aliased_model_id
            candidate_ids.add(normalized_model_id)

        # Remove any empty candidates that might have been added
        candidate_ids = {cid for cid in candidate_ids if cid}

        # Step 1: Check in-memory cache (fastest)
        cache_entry = _pricing_cache.get(model_id)
        if cache_entry:
            age = time.time() - cache_entry["timestamp"]
            if age < _pricing_cache_ttl:
                logger.debug(f"[CACHE HIT] Pricing for {model_id} (age: {age:.1f}s)")
                return cache_entry["data"]
            else:
                # Cache expired, remove it
                del _pricing_cache[model_id]
                logger.debug(f"[CACHE EXPIRED] Pricing for {model_id} (age: {age:.1f}s)")

        # Step 2: Query database (primary source)
        try:
            db_pricing = _get_pricing_from_database(model_id, candidate_ids)
            if db_pricing:
                # Cache the result
                _pricing_cache[model_id] = {
                    "data": db_pricing,
                    "timestamp": time.time()
                }
                return db_pricing
        except Exception as e:
            logger.warning(f"Database pricing lookup failed for {model_id}: {e}")

        # Step 3: Fallback to provider API cache (for resilience)
        try:
            cache_pricing = _get_pricing_from_cache_fallback(model_id, candidate_ids)
            if cache_pricing:
                # Cache the fallback result (shorter TTL)
                _pricing_cache[model_id] = {
                    "data": cache_pricing,
                    "timestamp": time.time()
                }
                return cache_pricing
        except Exception as e:
            logger.warning(f"Cache fallback pricing lookup failed for {model_id}: {e}")

        # Step 4: Use default pricing (last resort)
        logger.warning(
            f"Model {model_id} not found in database or cache, using default pricing"
        )
        default_pricing = {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default"
        }
        return default_pricing

    except Exception as e:
        logger.error(f"Error getting pricing for model {model_id}: {e}", exc_info=True)
        return {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default"
        }


def calculate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculate the total cost for a chat completion based on model pricing

    Args:
        model_id: The model ID
        prompt_tokens: Number of prompt tokens used
        completion_tokens: Number of completion tokens used

    Returns:
        Total cost in USD
    """
    try:
        # Check if this is a free model first (OpenRouter free models end with :free)
        if model_id and model_id.endswith(":free"):
            logger.info(f"Free model detected: {model_id}, returning $0 cost")
            return 0.0

        pricing = get_model_pricing(model_id)

        # FIXED: Pricing is per single token, so just multiply (no division)
        prompt_cost = prompt_tokens * pricing["prompt"]
        completion_cost = completion_tokens * pricing["completion"]
        total_cost = prompt_cost + completion_cost

        logger.info(
            f"Cost calculation for {model_id}: "
            f"{prompt_tokens} prompt tokens (${prompt_cost:.6f}) + "
            f"{completion_tokens} completion tokens (${completion_cost:.6f}) = "
            f"${total_cost:.6f}"
        )

        return total_cost

    except Exception as e:
        logger.error(f"Error calculating cost for {model_id}: {e}")
        # Fallback: Check if free model before applying default pricing
        if model_id and model_id.endswith(":free"):
            logger.info(f"Free model detected in fallback: {model_id}, returning $0 cost")
            return 0.0
        # Fallback to simple calculation (assuming $0.00002 per token)
        total_tokens = prompt_tokens + completion_tokens
        return total_tokens * 0.00002
