"""
Pricing Service
Handles model pricing calculations and credit cost computation
"""

import logging
import time
from typing import Any

from src.services.model_transformations import apply_model_alias

logger = logging.getLogger(__name__)

# In-memory cache for pricing (live API + fallbacks)
# Structure: {model_id: {"data": dict, "timestamp": float}}
_pricing_cache: dict[str, dict[str, Any]] = {}
_pricing_cache_ttl = 900  # 15 minutes TTL for live pricing

# Track models that fall back to default pricing for monitoring/alerting
# Structure: {model_id: {"count": int, "first_seen": float, "last_seen": float, "errors": list}}
_default_pricing_tracker: dict[str, dict[str, Any]] = {}


def _track_default_pricing_usage(model_id: str, error: str | None = None) -> None:
    """
    Track when default pricing is used for a model.
    This helps identify models that need pricing data added to the database.

    Also sends Sentry alert for high-value model families (OpenAI, Anthropic, Google).
    """
    import time

    now = time.time()

    if model_id not in _default_pricing_tracker:
        _default_pricing_tracker[model_id] = {
            "count": 0,
            "first_seen": now,
            "last_seen": now,
            "errors": [],
        }

    tracker = _default_pricing_tracker[model_id]
    tracker["count"] += 1
    tracker["last_seen"] = now
    if error:
        tracker["errors"].append({"time": now, "error": error})
        # Keep only last 10 errors
        tracker["errors"] = tracker["errors"][-10:]

    # Alert for high-value model families that should have pricing
    high_value_prefixes = (
        "openai/", "anthropic/", "google/", "gpt-", "claude-", "gemini-",
        "o1", "o3", "o4",  # OpenAI reasoning models
    )
    model_lower = model_id.lower()

    if any(model_lower.startswith(prefix) or prefix in model_lower for prefix in high_value_prefixes):
        # Send Sentry alert for high-value models using default pricing
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                f"High-value model using default pricing: {model_id}",
                level="warning",
                extras={
                    "model_id": model_id,
                    "usage_count": tracker["count"],
                    "first_seen": tracker["first_seen"],
                    "last_seen": tracker["last_seen"],
                    "error": error,
                },
            )
        except Exception:
            # Sentry not configured or failed, just log
            logger.error(
                f"[BILLING_ALERT] High-value model '{model_id}' using default pricing! "
                f"Usage count: {tracker['count']}. Add pricing data to prevent under-billing."
            )

    # Log Prometheus metric if available
    try:
        from src.services.prometheus_metrics import default_pricing_usage_counter
        default_pricing_usage_counter.labels(model=model_id).inc()
    except (ImportError, AttributeError):
        pass  # Prometheus metrics not available


def get_default_pricing_stats() -> dict[str, Any]:
    """Get statistics about models using default pricing for monitoring."""
    return {
        "models_using_default": len(_default_pricing_tracker),
        "details": {
            model_id: {
                "count": data["count"],
                "first_seen": data["first_seen"],
                "last_seen": data["last_seen"],
                "error_count": len(data["errors"]),
            }
            for model_id, data in _default_pricing_tracker.items()
        },
    }


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
    Query database model_pricing table for pricing information via JOIN with models table.

    FIXED: Now queries the model_pricing table using the correct schema.
    - Uses model_pricing(price_per_input_token, price_per_output_token)
    - Joins via foreign key relationship
    - No longer queries deleted columns (pricing_prompt, pricing_completion)

    Args:
        model_id: Original model ID
        candidate_ids: Set of model ID variations to try

    Returns:
        Pricing dict with prompt/completion prices (per token), or None if not found
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
                    # Query models table with JOIN to model_pricing table
                    # PostgREST syntax: select model_pricing(*) creates a nested object
                    result = (
                        client.table("models")
                        .select("id, model_id, model_pricing(price_per_input_token, price_per_output_token)")
                        .eq("model_id", candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )

                if result.data and len(result.data) > 0:
                    row = result.data[0]

                    # Check if model_pricing relationship exists
                    if not row.get("model_pricing"):
                        logger.debug(f"[DB] Model {candidate} found but has no pricing entry")
                        continue

                    pricing_data = row["model_pricing"]

                    # Handle case where model_pricing is a list (PostgREST may return array for one-to-many)
                    if isinstance(pricing_data, list):
                        if not pricing_data:
                            logger.debug(f"[DB] Model {candidate} has empty pricing array")
                            continue
                        pricing_data = pricing_data[0]  # Take first pricing entry

                    # Extract pricing values (these are per-token prices)
                    prompt_price = pricing_data.get("price_per_input_token")
                    completion_price = pricing_data.get("price_per_output_token")

                    if prompt_price is None or completion_price is None:
                        logger.debug(f"[DB] Model {candidate} has incomplete pricing data")
                        continue

                    # Convert to float and ensure non-negative values
                    prompt_price = max(0.0, float(prompt_price))
                    completion_price = max(0.0, float(completion_price))

                    logger.info(
                        f"[DB SUCCESS] Found pricing for {model_id} (matched: {candidate}): "
                        f"prompt=${prompt_price}/token, completion=${completion_price}/token"
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
                        .select("id, model_id, model_pricing(price_per_input_token, price_per_output_token)")
                        .eq("provider_model_id", candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )

                if result.data and len(result.data) > 0:
                    row = result.data[0]

                    # Check if model_pricing relationship exists
                    if not row.get("model_pricing"):
                        logger.debug(f"[DB] Model {candidate} (provider_model_id) found but has no pricing entry")
                        continue

                    pricing_data = row["model_pricing"]

                    # Handle list case
                    if isinstance(pricing_data, list):
                        if not pricing_data:
                            continue
                        pricing_data = pricing_data[0]

                    # Extract pricing values
                    prompt_price = pricing_data.get("price_per_input_token")
                    completion_price = pricing_data.get("price_per_output_token")

                    if prompt_price is None or completion_price is None:
                        continue

                    # Convert to float and ensure non-negative values
                    prompt_price = max(0.0, float(prompt_price))
                    completion_price = max(0.0, float(completion_price))

                    logger.info(
                        f"[DB SUCCESS] Found pricing for {model_id} (matched provider_model_id: {candidate}): "
                        f"prompt=${prompt_price}/token, completion=${completion_price}/token"
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
        logger.debug(f"[DB MISS] No pricing found in database for {model_id} (tried {len(candidate_ids)} candidates)")
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

    Pricing lookup priority (UPDATED - PHASE 0):
    1. In-memory cache (15min TTL)
    2. Live API fetch from provider (OpenRouter, Featherless, Near AI, etc.)
    3. Database (model_pricing table) - PHASE 0 FIX
    4. Provider API cache / JSON fallback (manual_pricing.json)
    5. Default pricing ($0.00002 per token)

    Args:
        model_id: The model ID (e.g., "openai/gpt-4", "anthropic/claude-3-opus")

    Returns:
        Dictionary with pricing info:
        {
            "prompt": float,      # Cost per prompt token in USD
            "completion": float,  # Cost per completion token in USD
            "found": bool,        # Whether the model was found
            "source": str         # Source of pricing: live_api_*|cache_fallback|default
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

        # Step 2: Try live API fetch from provider
        # NOTE: When called from async context (most route handlers), we skip live fetch
        # here and rely on database/cache. The async version get_model_pricing_async()
        # should be used from async contexts for live pricing support.
        try:
            import asyncio
            import concurrent.futures
            from src.services.pricing_live_fetch import fetch_live_pricing

            live_pricing = None

            # Check if we're in an async context
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context - skip live fetch (use get_model_pricing_async instead)
                # Log once per model to avoid spam
                logger.debug(
                    f"[PRICING] Sync get_model_pricing called from async context for {model_id}. "
                    f"Consider using get_model_pricing_async() for live pricing support."
                )
            except RuntimeError:
                # No running loop - we can safely create one and run the async fetch
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        live_pricing = loop.run_until_complete(fetch_live_pricing(model_id))
                    finally:
                        loop.close()
                except Exception as e:
                    logger.debug(f"Live pricing fetch in new event loop failed for {model_id}: {e}")

            if live_pricing:
                # Cache the live result
                _pricing_cache[model_id] = {
                    "data": live_pricing,
                    "timestamp": time.time()
                }
                logger.info(
                    f"[LIVE API SUCCESS] Cached pricing for {model_id} from {live_pricing.get('source', 'unknown')}"
                )
                return live_pricing
            else:
                logger.debug(f"[LIVE API] No pricing available from provider API for {model_id}")

        except Exception as e:
            logger.warning(f"Live API pricing fetch failed for {model_id}: {e}")

        # Step 3: Try database pricing (PHASE 0 FIX)
        try:
            db_pricing = _get_pricing_from_database(model_id, candidate_ids)
            if db_pricing:
                # Cache the database result
                _pricing_cache[model_id] = {
                    "data": db_pricing,
                    "timestamp": time.time()
                }
                logger.info(f"[DB FALLBACK] Using database pricing for {model_id}")
                return db_pricing
        except Exception as e:
            logger.warning(f"Database pricing lookup failed for {model_id}: {e}")

        # Step 4: Fallback to provider API cache (for resilience)
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

        # Step 5: Use default pricing (last resort)
        # ALERT: Default pricing may significantly under-bill for expensive models
        # Track this metric for monitoring and alerting
        logger.warning(
            f"[DEFAULT_PRICING_ALERT] Model {model_id} not found in database or cache, "
            f"using default pricing ($0.00002/token). This may under-bill for expensive models."
        )
        _track_default_pricing_usage(model_id)
        default_pricing = {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default"
        }
        return default_pricing

    except Exception as e:
        logger.error(f"Error getting pricing for model {model_id}: {e}", exc_info=True)
        _track_default_pricing_usage(model_id, error=str(e))
        return {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default"
        }


async def get_model_pricing_async(model_id: str) -> dict[str, float]:
    """
    Get pricing information for a specific model (async version).

    This is the preferred method when called from async contexts (route handlers, etc.).
    Uses the same caching strategy as get_model_pricing but handles async operations natively.

    Pricing lookup priority (UPDATED - PHASE 0):
    1. In-memory cache (15min TTL)
    2. Live API fetch from provider (OpenRouter, Featherless, Near AI, etc.)
    3. Database (model_pricing table) - PHASE 0 FIX
    4. Provider API cache / JSON fallback (manual_pricing.json)
    5. Default pricing ($0.00002 per token)

    Args:
        model_id: The model ID (e.g., "openai/gpt-4", "anthropic/claude-3-opus")

    Returns:
        Dictionary with pricing info
    """
    try:
        # Import here to avoid circular imports
        from src.services.models import _is_building_catalog

        # If we're building the catalog, return default pricing to avoid circular dependency
        if _is_building_catalog():
            logger.debug(f"Returning default pricing for {model_id} (catalog building in progress)")
            return {"prompt": 0.00002, "completion": 0.00002, "found": False, "source": "default"}

        # Build candidate IDs for lookup
        candidate_ids = {model_id}

        # Strip provider-specific suffixes for matching
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

        # Remove any empty candidates
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

        # Step 2: Try live API fetch from provider
        try:
            from src.services.pricing_live_fetch import fetch_live_pricing

            live_pricing = await fetch_live_pricing(model_id)

            if live_pricing:
                # Cache the live result
                _pricing_cache[model_id] = {
                    "data": live_pricing,
                    "timestamp": time.time()
                }
                logger.info(
                    f"[LIVE API SUCCESS] Cached pricing for {model_id} from {live_pricing.get('source', 'unknown')}"
                )
                return live_pricing
            else:
                logger.debug(f"[LIVE API] No pricing available from provider API for {model_id}")

        except Exception as e:
            logger.warning(f"Live API pricing fetch failed for {model_id}: {e}")

        # Step 3: Try database pricing (PHASE 0 FIX)
        try:
            db_pricing = _get_pricing_from_database(model_id, candidate_ids)
            if db_pricing:
                # Cache the database result
                _pricing_cache[model_id] = {
                    "data": db_pricing,
                    "timestamp": time.time()
                }
                logger.info(f"[DB FALLBACK] Using database pricing for {model_id}")
                return db_pricing
        except Exception as e:
            logger.warning(f"Database pricing lookup failed for {model_id}: {e}")

        # Step 4: Fallback to provider API cache (for resilience)
        try:
            cache_pricing = _get_pricing_from_cache_fallback(model_id, candidate_ids)
            if cache_pricing:
                # Cache the fallback result
                _pricing_cache[model_id] = {
                    "data": cache_pricing,
                    "timestamp": time.time()
                }
                return cache_pricing
        except Exception as e:
            logger.warning(f"Cache fallback pricing lookup failed for {model_id}: {e}")

        # Step 5: Use default pricing (last resort)
        # ALERT: Default pricing may significantly under-bill for expensive models
        logger.warning(
            f"[DEFAULT_PRICING_ALERT] Model {model_id} not found via live API, database, or cache, "
            f"using default pricing ($0.00002/token). This may under-bill for expensive models."
        )
        _track_default_pricing_usage(model_id)
        default_pricing = {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default"
        }
        return default_pricing

    except Exception as e:
        logger.error(f"Error getting pricing for model {model_id}: {e}", exc_info=True)
        _track_default_pricing_usage(model_id, error=str(e))
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


async def calculate_cost_async(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculate the total cost for a chat completion based on model pricing (async version).

    This version uses get_model_pricing_async() which can fetch live pricing from
    provider APIs when called from async contexts.

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

        pricing = await get_model_pricing_async(model_id)

        # Pricing is per single token, so just multiply (no division)
        prompt_cost = prompt_tokens * pricing["prompt"]
        completion_cost = completion_tokens * pricing["completion"]
        total_cost = prompt_cost + completion_cost

        logger.info(
            f"Cost calculation (async) for {model_id}: "
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
