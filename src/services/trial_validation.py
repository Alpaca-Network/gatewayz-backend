#!/usr/bin/env python3
"""
Simplified Trial Validation
Direct trial validation without complex service layer

PERF: Includes in-memory caching to reduce database queries by ~95%
"""

import logging
import time
import traceback
from datetime import datetime, timedelta, UTC
from typing import Any

from src.config.supabase_config import (
    get_supabase_client,
    is_connection_error,
    refresh_supabase_client,
)

logger = logging.getLogger(__name__)

# In-memory cache for trial validation results
# Structure: {api_key: {"result": dict, "timestamp": datetime}}
_trial_cache: dict[str, dict[str, Any]] = {}
_trial_cache_ttl = 60  # 60 seconds TTL - shorter than user cache since trial usage changes frequently
_trial_cache_ttl_expired = 3600  # 1 hour TTL for expired trials - they won't change, no need to recheck often


def clear_trial_cache(api_key: str | None = None) -> None:
    """Clear trial cache (for testing or explicit invalidation)"""
    global _trial_cache
    if api_key:
        if api_key in _trial_cache:
            del _trial_cache[api_key]
            logger.debug(f"Cleared trial cache for API key {api_key[:10]}...")
    else:
        _trial_cache.clear()
        logger.info("Cleared entire trial cache")


def get_trial_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring"""
    return {
        "cached_trials": len(_trial_cache),
        "ttl_seconds": _trial_cache_ttl,
    }


def invalidate_trial_cache(api_key: str) -> None:
    """Invalidate cache for a specific trial (e.g., after usage update)"""
    clear_trial_cache(api_key)
    logger.debug(f"Invalidated trial cache for API key {api_key[:10]}...")


def _parse_trial_end_utc(s: str) -> datetime:
    s = s.strip()
    if "T" not in s:
        # Date-only -> use end of that day timezone.utc (friendliest interpretation)
        d = datetime.fromisoformat(s)
        return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=UTC)
    # Full datetime
    if s.endswith("Z"):
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(s)
    # Ensure timezone.utc-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt


def _validate_trial_access_uncached(api_key: str, retry_count: int = 0) -> dict[str, Any]:
    """Internal function: Validate trial access from database (no caching)

    Args:
        api_key: The API key to validate
        retry_count: Number of retries attempted (for internal use)
    """
    MAX_RETRIES = 2

    try:
        client = get_supabase_client()

        # Get API key data from api_keys_new table
        result = client.table("api_keys_new").select("*").eq("api_key", api_key).execute()

        # ADMIN BYPASS: Check if user has admin plan (bypass all trial checks)
        if result.data:
            user_id = result.data[0].get("user_id")
            if user_id:
                try:
                    from src.db.plans import is_admin_tier_user
                    if is_admin_tier_user(user_id):
                        logger.info("Admin tier user - bypassing trial validation")
                        return {
                            "is_valid": True,
                            "is_trial": False,
                            "message": "Admin tier - unlimited access"
                        }
                except Exception as e:
                    logger.warning(f"Error checking admin tier status: {e}")

        if not result.data:
            # Fallback to legacy users table for basic trial info
            logger.info(
                f"API key not found in api_keys_new, checking legacy users table: {api_key[:20]}..."
            )
            legacy_result = (
                client.table("users")
                .select("subscription_status, trial_expires_at")
                .eq("api_key", api_key)
                .execute()
            )

            if not legacy_result.data:
                return {
                    "is_valid": False,
                    "is_trial": False,
                    "error": "Access forbidden. Your API key may be invalid, expired, or failed to migrate to the new system. Please check your API key or contact support if this issue persists.",
                }

            # For legacy keys, map users table fields to api_keys_new structure
            # Note: tokens_used, requests_used, credits_used are not stored in users table anymore
            # These are tracked in usage_records table or api_keys_new table
            user_data = legacy_result.data[0]
            key_data = {
                "is_trial": user_data.get("subscription_status") == "trial",
                "trial_end_date": user_data.get("trial_expires_at"),
                # Use default values for trial usage tracking (these columns don't exist in users table)
                "trial_used_tokens": 0,
                "trial_used_requests": 0,
                "trial_used_credits": 0.0,
            }
        else:
            key_data = result.data[0]

        # Debug logging
        logger.info(f"Trial validation for key: {api_key[:20]}...")
        logger.info(
            f"Key data: is_trial={key_data.get('is_trial')}, trial_end_date={key_data.get('trial_end_date')}"
        )

        # Check if it's a trial key
        if not key_data.get("is_trial", False):
            return {"is_valid": True, "is_trial": False, "message": "Not a trial key - full access"}

        # Check if trial is expired
        trial_end_date = key_data.get("trial_end_date")
        if trial_end_date:
            try:
                trial_end = _parse_trial_end_utc(trial_end_date)
                now = datetime.now(UTC)
                if trial_end <= now:
                    return {
                        "is_valid": False,
                        "is_trial": True,
                        "is_expired": True,
                        "error": "Trial has expired. Please upgrade to a paid plan to continue using the API.",
                        "trial_end_date": trial_end_date,
                    }
            except Exception as e:
                logger.warning(f"Error parsing trial end date '{trial_end_date}': {e}")
                # Keep previous behavior: assume not expired on parse failure

        # Check trial limits
        trial_used_tokens = key_data.get("trial_used_tokens", 0)
        trial_used_requests = key_data.get("trial_used_requests", 0)
        trial_used_credits = key_data.get("trial_used_credits", 0.0)

        trial_max_tokens = key_data.get("trial_max_tokens", 100000)
        trial_max_requests = key_data.get("trial_max_requests", 1000)
        trial_credits = key_data.get("trial_credits", 5.0)

        # Check if any limits are exceeded
        if trial_used_tokens >= trial_max_tokens:
            return {
                "is_valid": False,
                "is_trial": True,
                "is_expired": False,
                "error": "Trial token limit exceeded. Please upgrade to a paid plan.",
                "remaining_tokens": 0,
                "remaining_requests": max(0, trial_max_requests - trial_used_requests),
                "remaining_credits": max(0, trial_credits - trial_used_credits),
            }

        if trial_used_requests >= trial_max_requests:
            return {
                "is_valid": False,
                "is_trial": True,
                "is_expired": False,
                "error": "Trial request limit exceeded. Please upgrade to a paid plan.",
                "remaining_tokens": max(0, trial_max_tokens - trial_used_tokens),
                "remaining_requests": 0,
                "remaining_credits": max(0, trial_credits - trial_used_credits),
            }

        if trial_used_credits >= trial_credits:
            return {
                "is_valid": False,
                "is_trial": True,
                "is_expired": False,
                "error": "Trial credit limit exceeded. Please upgrade to a paid plan.",
                "remaining_tokens": max(0, trial_max_tokens - trial_used_tokens),
                "remaining_requests": max(0, trial_max_requests - trial_used_requests),
                "remaining_credits": 0,
            }

        # Trial is valid
        return {
            "is_valid": True,
            "is_trial": True,
            "is_expired": False,
            "remaining_tokens": trial_max_tokens - trial_used_tokens,
            "remaining_requests": trial_max_requests - trial_used_requests,
            "remaining_credits": trial_credits - trial_used_credits,
            "trial_end_date": trial_end_date,
        }

    except Exception as e:
        error_str = str(e)

        # Check for transient SSL/connection errors that may benefit from retry
        # is_connection_error() already covers: connectionterminated, connection reset,
        # connection refused, broken pipe, stream reset, etc.
        # Here we add HTTP/2 specific errors not covered by is_connection_error()
        is_transient_error = is_connection_error(e) or any(
            msg in error_str
            for msg in [
                "EOF occurred in violation of protocol",
                "timed out",
                # HTTP/2 specific errors not covered by is_connection_error
                "LocalProtocolError",
                "RemoteProtocolError",
                "StreamID",
                "SEND_HEADERS",
                "ConnectionState.CLOSED",
            ]
        )

        if is_transient_error and retry_count < MAX_RETRIES:
            logger.warning(
                f"Transient error validating trial access (attempt {retry_count + 1}/{MAX_RETRIES}): {e}"
            )
            # Refresh the Supabase client to get a fresh HTTP/2 connection
            try:
                refresh_supabase_client()
                logger.info("Refreshed Supabase client after HTTP/2 connection error")
            except Exception as refresh_error:
                logger.warning(f"Failed to refresh Supabase client: {refresh_error}")

            # Brief pause before retry to allow connection to reset
            time.sleep(0.1 * (retry_count + 1))  # 0.1s, 0.2s backoff
            return _validate_trial_access_uncached(api_key, retry_count + 1)

        logger.error(f"Error validating trial access: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {
            "is_valid": False,
            "is_trial": False,
            "error": f"Access forbidden. An error occurred while validating your API key: {str(e)}. Please contact support if this issue persists.",
        }


def validate_trial_access(api_key: str) -> dict[str, Any]:
    """Validate trial access for an API key with caching (saves ~30-50ms per request)"""
    # PERF: Check cache first to avoid database queries
    if api_key in _trial_cache:
        entry = _trial_cache[api_key]
        cache_time = entry["timestamp"]
        cached_result = entry["result"]

        # Use longer TTL for expired/invalid trials - they won't change
        # This significantly reduces DB load from bot traffic with expired trials
        is_expired_or_invalid = (
            not cached_result.get("is_valid", False) or
            cached_result.get("is_expired", False)
        )
        ttl = _trial_cache_ttl_expired if is_expired_or_invalid else _trial_cache_ttl

        if datetime.now(UTC) - cache_time < timedelta(seconds=ttl):
            logger.debug(f"Trial cache hit for API key {api_key[:10]}... (age: {(datetime.now(UTC) - cache_time).total_seconds():.1f}s, ttl: {ttl}s)")
            return cached_result
        else:
            # Cache expired, remove it
            del _trial_cache[api_key]
            logger.debug(f"Trial cache expired for API key {api_key[:10]}...")

    # Cache miss - fetch from database
    logger.debug(f"Trial cache miss for API key {api_key[:10]}... - fetching from database")
    result = _validate_trial_access_uncached(api_key)

    # Cache the result
    _trial_cache[api_key] = {
        "result": result,
        "timestamp": datetime.now(UTC),
    }

    return result


def track_trial_usage(
    api_key: str,
    tokens_used: int,
    requests_used: int = 1,
    model_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> bool:
    """
    Track trial usage with accurate model-based pricing.

    Args:
        api_key: The API key to track usage for
        tokens_used: Total tokens used (fallback for credit calculation)
        requests_used: Number of requests made (default 1)
        model_id: The model ID used (for accurate pricing calculation)
        prompt_tokens: Number of prompt tokens (for accurate pricing)
        completion_tokens: Number of completion tokens (for accurate pricing)

    Returns:
        True if usage was tracked successfully, False otherwise
    """
    try:
        client = get_supabase_client()

        # Calculate credit cost using model-specific pricing when available
        if model_id and prompt_tokens is not None and completion_tokens is not None:
            # Use accurate per-model pricing from the pricing service
            try:
                from src.services.pricing import get_model_pricing

                pricing = get_model_pricing(model_id)

                if pricing.get("found", False):
                    # FIXED: Pricing is per single token, so just multiply (no division)
                    prompt_cost = prompt_tokens * pricing["prompt"]
                    completion_cost = completion_tokens * pricing["completion"]
                    credit_cost = prompt_cost + completion_cost
                    logger.info(
                        f"Trial usage: Using model-specific pricing for {model_id}: "
                        f"{prompt_tokens} prompt + {completion_tokens} completion = ${credit_cost:.6f}"
                    )
                else:
                    # Model not found in catalog - use flat rate to avoid near-zero charges
                    credit_cost = tokens_used * 0.00002
                    logger.info(
                        f"Trial usage: Model {model_id} not in catalog, using flat rate: "
                        f"{tokens_used} tokens = ${credit_cost:.6f}"
                    )
            except Exception as e:
                # Fallback to flat rate if pricing lookup fails
                logger.warning(f"Failed to get model pricing for {model_id}, using flat rate: {e}")
                credit_cost = tokens_used * 0.00002
        else:
            # Fallback: standard pricing ($0.00002 per token)
            credit_cost = tokens_used * 0.00002
            logger.info(
                f"Trial usage: Using flat-rate pricing (no model info): "
                f"{tokens_used} tokens = ${credit_cost:.6f}"
            )

        logger.info(
            f"Tracking usage: {tokens_used} tokens, {requests_used} requests, ${credit_cost:.6f} credits"
        )

        # Get current usage first from api_keys_new
        current_result = (
            client.table("api_keys_new")
            .select("trial_used_tokens, trial_used_requests, trial_used_credits")
            .eq("api_key", api_key)
            .execute()
        )

        if not current_result.data:
            # Fallback to legacy users table - but only for verification
            logger.info(
                f"API key not found in api_keys_new, checking legacy users table for usage tracking: {api_key[:20]}..."
            )
            legacy_result = client.table("users").select("id").eq("api_key", api_key).execute()

            if not legacy_result.data:
                logger.warning(
                    f"API key not found in either table for usage tracking: {api_key[:20]}..."
                )
                return False

            # Legacy keys don't have a dedicated trial usage table
            # We can't track trial usage for legacy keys in users table (columns don't exist)
            logger.warning(
                f"Cannot track trial usage for legacy API key {api_key[:20]}... - no trial tracking columns in users table"
            )
            return False
        else:
            current_data = current_result.data[0]
            old_tokens = current_data.get("trial_used_tokens", 0)
            old_requests = current_data.get("trial_used_requests", 0)
            old_credits = current_data.get("trial_used_credits", 0.0)

        new_tokens = old_tokens + tokens_used
        new_requests = old_requests + requests_used
        new_credits = old_credits + credit_cost

        logger.info(
            f"Usage update: tokens {old_tokens} -> {new_tokens}, requests {old_requests} -> {new_requests}, credits {old_credits:.6f} -> {new_credits:.6f}"
        )

        # Update trial usage in api_keys_new table
        result = (
            client.table("api_keys_new")
            .update(
                {
                    "trial_used_tokens": new_tokens,
                    "trial_used_requests": new_requests,
                    "trial_used_credits": new_credits,
                }
            )
            .eq("api_key", api_key)
            .execute()
        )

        success = len(result.data) > 0 if result.data else False
        logger.info(f"Usage tracking result: {success}")

        # Invalidate cache after usage update to ensure fresh data on next validation
        if success:
            invalidate_trial_cache(api_key)

        return success

    except Exception as e:
        logger.error(f"Error tracking trial usage: {e}")
        return False
