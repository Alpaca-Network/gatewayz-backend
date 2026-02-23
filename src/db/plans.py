import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

# Default entitlements used when a plan cannot be resolved from the database.
DEFAULT_DAILY_REQUEST_LIMIT = 25000
DEFAULT_MONTHLY_REQUEST_LIMIT = 750000
DEFAULT_DAILY_TOKEN_LIMIT = 500_000
DEFAULT_MONTHLY_TOKEN_LIMIT = 15_000_000
DEFAULT_TRIAL_FEATURES = ["basic_models"]

# Admin tier constants
ADMIN_PLAN_TYPE = "admin"
ADMIN_BYPASS_LIMITS = True  # Flag to enable admin bypass of all checks

# PERF: In-memory cache for usage data to reduce database queries
# Short TTL since usage data changes frequently and is billing-critical
_usage_cache: dict[str, dict[str, Any]] = {}
_usage_cache_ttl = 10  # 10 seconds TTL - short to minimize over-usage window

# PERF: In-memory cache for user plan data to prevent concurrent calls
# that can trigger Cloudflare rate limiting (400 Bad Request with HTML response)
_user_plan_cache: dict[str, dict[str, Any]] = {}
_user_plan_cache_ttl = 30  # 30 seconds TTL - user plans change infrequently


def clear_usage_cache(user_id: int | None = None) -> None:
    """Clear usage cache (for testing or explicit invalidation)"""
    global _usage_cache
    if user_id:
        cache_key = f"usage:{user_id}"
        if cache_key in _usage_cache:
            del _usage_cache[cache_key]
            logger.debug(f"Cleared usage cache for user {user_id}")
    else:
        _usage_cache.clear()
        logger.info("Cleared entire usage cache")


def clear_user_plan_cache(user_id: int | None = None) -> None:
    """Clear user plan cache (for testing or explicit invalidation)"""
    global _user_plan_cache
    if user_id:
        cache_key = f"user_plan:{user_id}"
        if cache_key in _user_plan_cache:
            del _user_plan_cache[cache_key]
            logger.debug(f"Cleared user plan cache for user {user_id}")
    else:
        _user_plan_cache.clear()
        logger.info("Cleared entire user plan cache")


def get_usage_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring"""
    return {
        "cached_users": len(_usage_cache),
        "ttl_seconds": _usage_cache_ttl,
    }


def get_user_plan_cache_stats() -> dict[str, Any]:
    """Get user plan cache statistics for monitoring"""
    return {
        "cached_users": len(_user_plan_cache),
        "ttl_seconds": _user_plan_cache_ttl,
    }


def invalidate_usage_cache(user_id: int) -> None:
    """Invalidate cache for a specific user (e.g., after usage recorded)"""
    clear_usage_cache(user_id)
    logger.debug(f"Invalidated usage cache for user {user_id}")


def invalidate_user_plan_cache(user_id: int) -> None:
    """Invalidate user plan cache for a specific user (e.g., after plan change)"""
    clear_user_plan_cache(user_id)
    logger.debug(f"Invalidated user plan cache for user {user_id}")


def get_all_plans() -> list[dict[str, Any]]:
    """Get all available subscription plans"""
    try:
        logger.info("Getting all plans from database...")
        client = get_supabase_client()
        logger.info("Supabase client obtained successfully")

        result = (
            client.table("plans")
            .select("*")
            .eq("is_active", True)
            .order("price_per_month")
            .execute()
        )
        logger.info(f"Database query executed, got {len(result.data) if result.data else 0} plans")

        if result.data:
            logger.info(f"First plan sample: {result.data[0] if result.data else 'None'}")

        return result.data or []
    except Exception as e:
        logger.error(f"Error getting plans: {e}", exc_info=True)
        return []


def get_plan_by_id(plan_id: int) -> dict[str, Any] | None:
    """Get a specific plan by ID"""
    try:
        client = get_supabase_client()
        result = client.table("plans").select("*").eq("id", plan_id).eq("is_active", True).execute()

        if not result.data:
            return None

        plan = result.data[0]

        # Handle features field - convert from dict to list if needed
        features = plan.get("features", [])
        if isinstance(features, dict):
            # Convert dict to list of feature names
            features = list(features.keys())
        elif not isinstance(features, list):
            features = []

        # Return plan with converted features
        plan["features"] = features
        return plan

    except Exception as e:
        logger.error(f"Error getting plan {plan_id}: {e}")
        return None


def get_plan_id_by_tier(tier: str) -> int | None:
    """Get plan ID for a given tier name (pro, max, etc.)"""
    try:
        client = get_supabase_client()
        # Query plans table for a plan matching the tier name
        result = (
            client.table("plans")
            .select("id")
            .ilike("name", f"%{tier}%")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if result.data:
            plan_id = result.data[0]["id"]
            logger.info(f"Found plan ID {plan_id} for tier: {tier}")
            return plan_id
        else:
            logger.warning(f"No plan found for tier: {tier}")
            return None

    except Exception as e:
        logger.error(f"Error getting plan ID for tier {tier}: {e}")
        return None


def _get_user_plan_uncached(user_id: int) -> dict[str, Any] | None:
    """Internal function: Get user plan from database (no caching)"""
    try:
        client = get_supabase_client()

        user_plan_result = (
            client.table("user_plans")
            .select("*")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )
        if not user_plan_result.data:
            return None

        user_plan = user_plan_result.data[0]

        # Reuse helper so feature normalization is consistent
        plan = get_plan_by_id(user_plan["plan_id"])

        if not plan:
            # Fallback: still surface the existence of an active user_plan
            return {
                "user_plan_id": user_plan["id"],
                "user_id": user_id,
                "plan_id": user_plan["plan_id"],
                "plan_name": "Unknown",
                "plan_description": "",
                "daily_request_limit": DEFAULT_DAILY_REQUEST_LIMIT,
                "monthly_request_limit": DEFAULT_MONTHLY_REQUEST_LIMIT,
                "daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
                "monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
                "price_per_month": 0,
                "features": [],
                "start_date": user_plan["started_at"],
                "end_date": user_plan["expires_at"],
                "is_active": True,
            }

        features = plan.get("features", [])
        if isinstance(features, dict):
            features = list(features.keys())
        elif not isinstance(features, list):
            features = []

        logger.info("get_user_plan: user=%s", user_id)
        logger.info(" -> found active user_plans: %s", bool(user_plan_result.data))
        if user_plan_result.data:
            logger.info(" -> plan lookup id=%s", user_plan_result.data[0]["plan_id"])
            logger.info(" -> plan found: %s", bool(plan))
        return {
            "user_plan_id": user_plan["id"],
            "user_id": user_id,
            "plan_id": plan["id"],
            "plan_name": plan["name"],
            "plan_description": plan.get("description", ""),
            "daily_request_limit": plan["daily_request_limit"],
            "monthly_request_limit": plan["monthly_request_limit"],
            "daily_token_limit": plan["daily_token_limit"],
            "monthly_token_limit": plan["monthly_token_limit"],
            "price_per_month": plan["price_per_month"],
            "features": features,
            "start_date": user_plan["started_at"],
            "end_date": user_plan["expires_at"],
            "is_active": user_plan["is_active"],
        }
    except Exception as e:
        logger.error(f"Error getting user plan for user {user_id}: {e}")
        return None


def get_user_plan(user_id: int) -> dict[str, Any] | None:
    """Get current active plan for user with caching.

    Caching prevents concurrent Supabase calls that can trigger Cloudflare
    rate limiting (400 Bad Request with HTML response instead of JSON).
    """
    cache_key = f"user_plan:{user_id}"

    # PERF: Check cache first to avoid database queries and rate limiting
    if cache_key in _user_plan_cache:
        entry = _user_plan_cache[cache_key]
        cache_time = entry["timestamp"]
        if datetime.now(UTC) - cache_time < timedelta(seconds=_user_plan_cache_ttl):
            logger.debug(
                f"User plan cache hit for user {user_id} "
                f"(age: {(datetime.now(UTC) - cache_time).total_seconds():.1f}s)"
            )
            return entry["data"]
        else:
            # Cache expired, remove it
            del _user_plan_cache[cache_key]
            logger.debug(f"User plan cache expired for user {user_id}")

    # Cache miss - fetch from database
    logger.debug(f"User plan cache miss for user {user_id} - fetching from database")
    result = _get_user_plan_uncached(user_id)

    # Cache the result (even if None, to avoid repeated DB queries for users without plans)
    _user_plan_cache[cache_key] = {
        "data": result,
        "timestamp": datetime.now(UTC),
    }

    return result


def assign_user_plan(user_id: int, plan_id: int, duration_months: int = 1) -> bool:
    """Assign a plan to a user"""
    try:
        client = get_supabase_client()

        # Verify plan exists
        plan = get_plan_by_id(plan_id)
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")

        # Deactivate existing plans
        client.table("user_plans").update({"is_active": False}).eq("user_id", user_id).execute()

        # Create new plan assignment
        start_date = datetime.now(UTC)
        end_date = start_date + timedelta(days=30 * duration_months)

        user_plan_data = {
            "user_id": user_id,
            "plan_id": plan_id,
            "started_at": start_date.isoformat(),
            "expires_at": end_date.isoformat(),
            "is_active": True,
        }

        result = client.table("user_plans").insert(user_plan_data).execute()

        if not result.data:
            raise ValueError("Failed to assign plan to user")

        # Update user subscription status
        client.table("users").update(
            {"subscription_status": "active", "updated_at": datetime.now(UTC).isoformat()}
        ).eq("id", user_id).execute()

        # Invalidate user plan cache since we just changed the plan
        invalidate_user_plan_cache(user_id)

        return True

    except Exception as e:
        logger.error(f"Error assigning plan {plan_id} to user {user_id}: {e}")
        raise RuntimeError(f"Failed to assign plan: {e}") from e


def check_plan_entitlements(user_id: int, required_feature: str = None) -> dict[str, Any]:
    """Check if user's current plan allows certain usage"""
    try:
        # ADMIN BYPASS: Admin tier users have unlimited entitlements
        if is_admin_tier_user(user_id):
            logger.debug(f"Admin tier user {user_id} - returning unlimited entitlements")
            return {
                "has_plan": True,
                "plan_name": "Admin",
                "daily_request_limit": 2147483647,  # Max int
                "monthly_request_limit": 2147483647,
                "daily_token_limit": 2147483647,
                "monthly_token_limit": 2147483647,
                "features": ["unlimited_access", "priority_support", "admin_features", "all_models"],
                "can_access_feature": True,  # Admin can access any feature
                "plan_expires": None,  # Admin plans don't expire
            }

        user_plan = get_user_plan(user_id)

        # If get_user_plan() failed, inspect user_plans directly to avoid dropping to trial by mistake
        if not user_plan:
            client = get_supabase_client()
            up_rs = (
                client.table("user_plans")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )

            if up_rs.data:
                up = up_rs.data[0]
                end_str = up.get("expires_at")
                end_dt = None
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    except Exception:
                        end_dt = None

                now = datetime.now(UTC)

                # If expired, mark expired and return the expired payload
                if end_dt and end_dt < now:
                    client.table("user_plans").update({"is_active": False}).eq(
                        "id", up["id"]
                    ).execute()
                    client.table("users").update({"subscription_status": "expired"}).eq(
                        "id", user_id
                    ).execute()
                    # Invalidate cache since plan status changed
                    invalidate_user_plan_cache(user_id)
                    return {
                        "has_plan": False,
                        "plan_expired": True,
                        "daily_request_limit": DEFAULT_DAILY_REQUEST_LIMIT,
                        "monthly_request_limit": DEFAULT_MONTHLY_REQUEST_LIMIT,
                        "daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
                        "monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
                        "features": DEFAULT_TRIAL_FEATURES.copy(),
                        "plan_name": "Expired",
                        "can_access_feature": (
                            required_feature in DEFAULT_TRIAL_FEATURES if required_feature else True
                        ),
                    }

                # ACTIVE PLAN FALLBACK: try to load the plan and return has_plan=True
                plan = get_plan_by_id(up["plan_id"])
                if plan:
                    features = plan.get("features", [])
                    if isinstance(features, dict):
                        features = list(features.keys())
                    elif not isinstance(features, list):
                        features = []

                    return {
                        "has_plan": True,
                        "plan_name": plan["name"],
                        "daily_request_limit": plan["daily_request_limit"],
                        "monthly_request_limit": plan["monthly_request_limit"],
                        "daily_token_limit": plan["daily_token_limit"],
                        "monthly_token_limit": plan["monthly_token_limit"],
                        "features": features,
                        "can_access_feature": (
                            (required_feature in features) if required_feature else True
                        ),
                        "plan_expires": up["expires_at"],
                    }

                # If we still can't get the plan row, assume an active-but-unknown plan with conservative defaults
                return {
                    "has_plan": True,
                    "plan_name": "Unknown",
                    "daily_request_limit": DEFAULT_DAILY_REQUEST_LIMIT,
                    "monthly_request_limit": DEFAULT_MONTHLY_REQUEST_LIMIT,
                    "daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
                    "monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
                    "features": [],
                    "can_access_feature": (required_feature is None),  # no features to gate
                    "plan_expires": up.get("expires_at"),
                }

            # Truly no active plan → trial defaults
            return {
                "has_plan": False,
                "daily_request_limit": DEFAULT_DAILY_REQUEST_LIMIT,
                "monthly_request_limit": DEFAULT_MONTHLY_REQUEST_LIMIT,
                "daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
                "monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
                "features": DEFAULT_TRIAL_FEATURES.copy(),
                "plan_name": "Trial",
                "can_access_feature": (
                    required_feature in DEFAULT_TRIAL_FEATURES if required_feature else True
                ),
            }

        # We have a combined user_plan (happy path)
        # Check expiration only if end_date is set
        if user_plan.get("end_date"):
            end_date = datetime.fromisoformat(user_plan["end_date"].replace("Z", "+00:00"))
            now = datetime.now(UTC)
            if end_date < now:
                client = get_supabase_client()
                client.table("user_plans").update({"is_active": False}).eq(
                    "id", user_plan["user_plan_id"]
                ).execute()
                client.table("users").update({"subscription_status": "expired"}).eq(
                    "id", user_id
                ).execute()
                # Invalidate cache since plan status changed
                invalidate_user_plan_cache(user_id)
                return {
                    "has_plan": False,
                    "plan_expired": True,
                    "daily_request_limit": DEFAULT_DAILY_REQUEST_LIMIT,
                    "monthly_request_limit": DEFAULT_MONTHLY_REQUEST_LIMIT,
                    "daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
                    "monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
                    "features": DEFAULT_TRIAL_FEATURES.copy(),
                    "plan_name": "Expired",
                    "can_access_feature": (
                        required_feature in DEFAULT_TRIAL_FEATURES if required_feature else True
                    ),
                }

        features = user_plan.get("features", [])
        if isinstance(features, dict):
            features = list(features.keys())
        elif not isinstance(features, list):
            features = []

        return {
            "has_plan": True,
            "plan_name": user_plan["plan_name"],
            "daily_request_limit": user_plan["daily_request_limit"],
            "monthly_request_limit": user_plan["monthly_request_limit"],
            "daily_token_limit": user_plan["daily_token_limit"],
            "monthly_token_limit": user_plan["monthly_token_limit"],
            "features": features,
            "can_access_feature": (required_feature in features) if required_feature else True,
            "plan_expires": user_plan["end_date"],
        }

    except Exception as e:
        logger.error(f"Error checking plan entitlements for user {user_id}: {e}")
        # Safe defaults on error
        return {
            "has_plan": False,
            "daily_request_limit": DEFAULT_DAILY_REQUEST_LIMIT,
            "monthly_request_limit": DEFAULT_MONTHLY_REQUEST_LIMIT,
            "daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
            "monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
            "features": DEFAULT_TRIAL_FEATURES.copy(),
            "plan_name": "Error",
            "can_access_feature": False,
        }


def _get_user_usage_within_plan_limits_uncached(user_id: int) -> dict[str, Any]:
    """Internal function: Get user usage from database (no caching)"""
    try:
        client = get_supabase_client()
        entitlements = check_plan_entitlements(user_id)

        # Get usage for today and this month
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Get daily usage
        daily_usage_result = (
            client.table("usage_records")
            .select("tokens_used")
            .eq("user_id", user_id)
            .gte("timestamp", today_start.isoformat())
            .execute()
        )
        daily_tokens = sum(record["tokens_used"] for record in (daily_usage_result.data or []))

        # Get monthly usage
        monthly_usage_result = (
            client.table("usage_records")
            .select("tokens_used")
            .eq("user_id", user_id)
            .gte("timestamp", month_start.isoformat())
            .execute()
        )
        monthly_tokens = sum(record["tokens_used"] for record in (monthly_usage_result.data or []))

        # Get daily requests
        daily_requests = len(daily_usage_result.data or [])
        monthly_requests = len(monthly_usage_result.data or [])

        return {
            "plan_name": entitlements["plan_name"],
            "usage": {
                "daily_requests": daily_requests,
                "daily_tokens": daily_tokens,
                "monthly_requests": monthly_requests,
                "monthly_tokens": monthly_tokens,
            },
            "limits": {
                "daily_request_limit": entitlements["daily_request_limit"],
                "daily_token_limit": entitlements["daily_token_limit"],
                "monthly_request_limit": entitlements["monthly_request_limit"],
                "monthly_token_limit": entitlements["monthly_token_limit"],
            },
            "remaining": {
                "daily_requests": max(0, entitlements["daily_request_limit"] - daily_requests),
                "daily_tokens": max(0, entitlements["daily_token_limit"] - daily_tokens),
                "monthly_requests": max(
                    0, entitlements["monthly_request_limit"] - monthly_requests
                ),
                "monthly_tokens": max(0, entitlements["monthly_token_limit"] - monthly_tokens),
            },
            "at_limit": {
                "daily_requests": daily_requests >= entitlements["daily_request_limit"],
                "daily_tokens": daily_tokens >= entitlements["daily_token_limit"],
                "monthly_requests": monthly_requests >= entitlements["monthly_request_limit"],
                "monthly_tokens": monthly_tokens >= entitlements["monthly_token_limit"],
            },
        }

    except Exception as e:
        logger.error(f"Error getting usage within plan limits for user {user_id}: {e}")
        return None


def get_user_usage_within_plan_limits(user_id: int) -> dict[str, Any]:
    """Get user's current usage against their plan limits with caching (saves ~50-80ms per request)"""
    cache_key = f"usage:{user_id}"

    # PERF: Check cache first to avoid database queries
    if cache_key in _usage_cache:
        entry = _usage_cache[cache_key]
        cache_time = entry["timestamp"]
        if datetime.now(UTC) - cache_time < timedelta(seconds=_usage_cache_ttl):
            logger.debug(f"Usage cache hit for user {user_id} (age: {(datetime.now(UTC) - cache_time).total_seconds():.1f}s)")
            return entry["data"]
        else:
            # Cache expired, remove it
            del _usage_cache[cache_key]
            logger.debug(f"Usage cache expired for user {user_id}")

    # Cache miss - fetch from database
    logger.debug(f"Usage cache miss for user {user_id} - fetching from database")
    result = _get_user_usage_within_plan_limits_uncached(user_id)

    # Cache the result (even if None, to avoid repeated DB queries for invalid users)
    _usage_cache[cache_key] = {
        "data": result,
        "timestamp": datetime.now(UTC),
    }

    return result


def enforce_plan_limits(
    user_id: int, tokens_requested: int = 0, environment_tag: str = "live"
) -> dict[str, Any]:
    """Check if user can make a request within their plan limits"""
    try:
        # ADMIN BYPASS: Admin tier users have unlimited access
        if is_admin_tier_user(user_id):
            logger.debug(f"Admin tier user {user_id} - bypassing plan limit checks")
            return {"allowed": True, "reason": "Admin tier - unlimited access"}

        usage_data = get_user_usage_within_plan_limits(user_id)
        if not usage_data:
            return {"allowed": False, "reason": "Unable to check plan limits"}

        # Apply environment-specific limits (test environments get lower limits)
        env_multiplier = 1.0
        if environment_tag in ["test", "staging", "development"]:
            env_multiplier = 0.5  # Test environments get 50% of plan limits

        effective_daily_request_limit = int(
            usage_data["limits"]["daily_request_limit"] * env_multiplier
        )
        effective_monthly_request_limit = int(
            usage_data["limits"]["monthly_request_limit"] * env_multiplier
        )
        effective_daily_token_limit = int(
            usage_data["limits"]["daily_token_limit"] * env_multiplier
        )
        effective_monthly_token_limit = int(
            usage_data["limits"]["monthly_token_limit"] * env_multiplier
        )

        if environment_tag == "live" and effective_daily_token_limit < 25_000:
            effective_daily_token_limit = 25_000

        # Check if adding this request would exceed limits
        new_daily_tokens = usage_data["usage"]["daily_tokens"] + tokens_requested
        new_monthly_tokens = usage_data["usage"]["monthly_tokens"] + tokens_requested
        new_daily_requests = usage_data["usage"]["daily_requests"] + 1
        new_monthly_requests = usage_data["usage"]["monthly_requests"] + 1

        if new_daily_requests > effective_daily_request_limit:
            return {
                "allowed": False,
                "reason": f"Daily request limit exceeded ({effective_daily_request_limit}) for {environment_tag} environment",
            }

        if new_monthly_requests > effective_monthly_request_limit:
            return {
                "allowed": False,
                "reason": f"Monthly request limit exceeded ({effective_monthly_request_limit}) for {environment_tag} environment",
            }

        if new_daily_tokens > effective_daily_token_limit:
            return {
                "allowed": False,
                "reason": f"Daily token limit exceeded ({effective_daily_token_limit}) for {environment_tag} environment",
            }

        if new_monthly_tokens > effective_monthly_token_limit:
            return {
                "allowed": False,
                "reason": f"Monthly token limit exceeded ({effective_monthly_token_limit}) for {environment_tag} environment",
            }

        return {"allowed": True, "reason": "Within plan limits"}

    except Exception as e:
        logger.error(f"Error enforcing plan limits for user {user_id}: {e}")
        return {"allowed": False, "reason": "Error checking plan limits"}


def get_subscription_plans() -> list[dict[str, Any]]:
    """Get available subscription plans"""
    try:
        client = get_supabase_client()
        result = client.table("subscription_plans").select("*").eq("is_active", True).execute()
        return result.data if result.data else []

    except Exception as e:
        logger.error(f"Error getting subscription plans: {e}")
        return []


def is_admin_tier_user(user_id: int) -> bool:
    """
    Check if a user has an active admin tier plan

    Admin tier users bypass all resource limits, credit checks, and rate limiting.

    Args:
        user_id: The user ID to check

    Returns:
        True if user has active admin plan, False otherwise
    """
    if not ADMIN_BYPASS_LIMITS:
        return False

    try:
        client = get_supabase_client()

        # Check if user has an active admin plan
        result = (
            client.table("user_plans")
            .select("plans!inner(plan_type)")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )

        if result.data:
            for row in result.data:
                plan = row.get("plans", {})
                if isinstance(plan, dict) and plan.get("plan_type") == ADMIN_PLAN_TYPE:
                    logger.info(f"User {user_id} has active admin tier - bypassing all limits")
                    return True

        return False

    except Exception as e:
        logger.error(f"Error checking admin tier for user {user_id}: {e}")
        return False
