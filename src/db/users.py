import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.db.api_keys import create_api_key
from src.services.prometheus_metrics import track_database_query
from src.utils.db_safety import DatabaseResultError, safe_get_first, safe_get_value
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

# PERF: In-memory cache for user lookups to reduce database queries
# Structure: {api_key: {"user": dict, "timestamp": float}}
_user_cache: dict[str, dict[str, Any]] = {}
_user_cache_ttl = 300  # 5 minutes TTL - increased for better cache hit rate
# Note: Cache is invalidated on credit/profile updates, so longer TTL is safe


def clear_user_cache(api_key: str | None = None) -> None:
    """Clear user cache (for testing or explicit invalidation)"""
    global _user_cache
    if api_key:
        if api_key in _user_cache:
            del _user_cache[api_key]
            logger.debug(f"Cleared user cache for API key {api_key[:10]}...")
    else:
        _user_cache.clear()
        logger.info("Cleared entire user cache")


def get_user_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring"""
    return {
        "cached_users": len(_user_cache),
        "ttl_seconds": _user_cache_ttl,
    }


def invalidate_user_cache(api_key: str) -> None:
    """Invalidate cache for a specific user (e.g., after profile update)"""
    clear_user_cache(api_key)
    logger.debug(f"Invalidated user cache for API key {api_key[:10]}...")


def invalidate_user_cache_by_id(user_id: int) -> None:
    """Invalidate cache entries for a specific user by user ID.

    Since cache is keyed by api_key, this scans the cache to find entries
    matching the user_id and removes them.
    """
    global _user_cache
    keys_to_remove = [
        api_key
        for api_key, entry in _user_cache.items()
        if entry.get("user", {}).get("id") == user_id
    ]
    for api_key in keys_to_remove:
        del _user_cache[api_key]
    if keys_to_remove:
        logger.debug(f"Invalidated {len(keys_to_remove)} cache entries for user ID {user_id}")


def _is_temporary_api_key(api_key: str) -> bool:
    """Check if an API key is a temporary key that should be replaced.

    Temporary keys are created during user registration with a shorter token:
    - Format: "gw_live_" (8 chars) + token_urlsafe(16) (22 chars) = 30 chars total

    Proper keys from create_api_key use a longer token:
    - Format: "gw_live_" (8 chars) + token_urlsafe(32) (43 chars) = 51 chars total

    Threshold: Any gw_live_ key shorter than 40 chars is considered temporary.
    """
    if not api_key:
        return False

    if api_key.startswith("gw_live_"):
        return len(api_key) < 40

    return False


def _migrate_legacy_api_key(client, user: dict[str, Any], api_key: str) -> bool:
    """Migrate a legacy API key from users.api_key to api_keys_new table.

    This function is called automatically when a legacy key is detected during
    authentication. It creates a corresponding entry in api_keys_new with
    appropriate defaults, allowing the user to benefit from the new key features.

    IMPORTANT: Temporary keys (short keys created during user registration that
    should have been replaced) are NOT migrated. Instead, they will be handled
    by the auth flow which creates a new proper key.

    Args:
        client: Supabase client instance
        user: User dictionary containing at least 'id' and optionally 'created_at'
        api_key: The legacy API key to migrate

    Returns:
        True if migration succeeded, False otherwise
    """
    try:
        user_id = user.get("id")
        if not user_id:
            logger.error("Cannot migrate legacy key: user has no ID")
            return False

        # Don't migrate temporary keys - they should be replaced with proper keys
        if _is_temporary_api_key(api_key):
            logger.warning(
                "Detected temporary API key for user %s (length: %d). "
                "Skipping migration - this key should be replaced with a proper key.",
                sanitize_for_logging(str(user_id)),
                len(api_key),
            )
            return False

        # Check if key already exists in api_keys_new (race condition protection)
        with track_database_query(table="api_keys_new", operation="select"):
            existing = client.table("api_keys_new").select("id").eq("api_key", api_key).execute()

        if existing.data:
            logger.debug("Legacy key already exists in api_keys_new, skipping migration")
            return True  # Already migrated

        # Determine environment tag from key prefix
        if api_key.startswith("gw_test_"):
            environment_tag = "test"
        elif api_key.startswith("gw_staging_"):
            environment_tag = "staging"
        elif api_key.startswith("gw_dev_"):
            environment_tag = "development"
        else:
            environment_tag = "live"

        # Prepare migration payload matching the SQL migration
        migration_payload = {
            "user_id": user_id,
            "api_key": api_key,
            "key_name": "Legacy Primary Key (auto-migrated)",
            "environment_tag": environment_tag,
            "is_primary": True,
            "is_active": True,
            "scope_permissions": {"read": ["*"], "write": ["*"], "admin": ["*"]},
            "ip_allowlist": [],
            "domain_referrers": [],
            "created_at": user.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Insert the migrated key
        with track_database_query(table="api_keys_new", operation="insert"):
            result = client.table("api_keys_new").insert(migration_payload).execute()

        if result.data:
            logger.info(
                "Auto-migrated legacy API key for user %s to api_keys_new (env: %s)",
                sanitize_for_logging(str(user_id)),
                environment_tag,
            )
            return True
        else:
            logger.warning(
                "Migration insert returned no data for user %s",
                sanitize_for_logging(str(user_id)),
            )
            return False

    except Exception as e:
        # Log but don't raise - migration failure shouldn't block authentication
        logger.error(
            "Failed to auto-migrate legacy API key for user %s: %s",
            sanitize_for_logging(str(user.get("id", "unknown"))),
            sanitize_for_logging(str(e)),
        )
        return False


def create_enhanced_user(
    username: str,
    email: str,
    auth_method: str,
    credits: float = 5.0,
    privy_user_id: str | None = None,
    subscription_status: str = "trial",
) -> dict[str, Any]:
    """Create a new user with automatic 3-day trial and $5 credits (limited to $1/day usage).

    Args:
        username: User's username
        email: User's email address
        auth_method: Authentication method used (e.g., 'privy', 'email')
        credits: Initial credit amount (default $5)
        privy_user_id: Optional Privy user ID for Privy-authenticated users
        subscription_status: Initial subscription status (default 'trial', use 'bot' for temp emails)
    """
    try:
        client = get_supabase_client()

        # Prepare user data with trial setup
        trial_start = datetime.now(timezone.utc)
        trial_end = trial_start + timedelta(days=3)

        user_data = {
            "username": username,
            "email": email,
            "credits": credits,  # $5 trial credits with $1/day usage limit enforced
            "is_active": True,
            "registration_date": trial_start.isoformat(),
            "auth_method": auth_method,
            "subscription_status": subscription_status,
            "trial_expires_at": trial_end.isoformat(),
            "welcome_email_sent": False,  # New users haven't received welcome email yet
            "tier": "basic",
        }

        # Add privy_user_id if provided
        if privy_user_id:
            user_data["privy_user_id"] = privy_user_id

        # Create user account with a temporary API key (will be replaced)
        user_data["api_key"] = f"gw_live_{secrets.token_urlsafe(16)}"
        user_result = client.table("users").insert(user_data).execute()

        # Use safe_get_first to safely extract user data with validation
        try:
            user = safe_get_first(
                user_result,
                error_message="Failed to create user account - no data returned",
                validate_keys=["id"]
            )
            user_id = user["id"]
        except (DatabaseResultError, KeyError) as e:
            logger.error(f"User creation failed: {e}")
            raise ValueError(f"Failed to create user account: {e}")

        # Generate primary API key with the appropriate subscription status
        primary_key, _ = create_api_key(
            user_id=user_id,
            key_name="Primary Key",
            environment_tag="live",
            is_primary=True,
            subscription_status=subscription_status,
        )

        # Update user with the actual API key
        update_result = (
            client.table("users").update({"api_key": primary_key}).eq("id", user_id).execute()
        )

        if not update_result.data:
            logger.warning(
                "Failed to update users.api_key for user %s, but primary key created successfully in api_keys_new",
                sanitize_for_logging(str(user_id)),
            )

        logger.info(
            "User %s created successfully with primary API key: %s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(primary_key[:15] + "..."),
        )

        # Return user info with primary key
        return {
            "user_id": user_id,
            "username": username,
            "email": email,
            "credits": credits,
            "primary_api_key": primary_key,
            "subscription_status": subscription_status,
            "trial_expires_at": trial_end.isoformat(),
            "tier": "basic",
        }

    except Exception as e:
        logger.error("Failed to create enhanced user: %s", sanitize_for_logging(str(e)))
        raise RuntimeError(f"Failed to create enhanced user: {e}")


def _schedule_background_migration(client, user: dict[str, Any], api_key: str) -> None:
    """Schedule legacy API key migration to run in background (non-blocking).

    This allows legacy keys to authenticate immediately without waiting for migration.
    Migration happens asynchronously after the auth response is sent.
    """
    import threading

    def do_migration():
        try:
            migrated = _migrate_legacy_api_key(client, user, api_key)
            if migrated:
                logger.info(
                    "Background migration completed for user %s",
                    sanitize_for_logging(str(user.get("id"))),
                )
            else:
                logger.debug(
                    "Background migration skipped/failed for user %s (may already exist)",
                    sanitize_for_logging(str(user.get("id"))),
                )
        except Exception as e:
            logger.warning(
                "Background migration error for user %s: %s",
                sanitize_for_logging(str(user.get("id"))),
                sanitize_for_logging(str(e)),
            )

    # Run migration in background thread - don't block auth
    thread = threading.Thread(target=do_migration, daemon=True)
    thread.start()


def _get_user_uncached(api_key: str) -> dict[str, Any] | None:
    """Internal function: Get user by API key from database (no caching)

    PERF OPTIMIZATIONS:
    1. Legacy keys authenticate immediately without blocking on migration
    2. Migration runs in background thread after auth succeeds
    3. Reduced logging for common paths
    """
    try:
        client = get_supabase_client()

        # First, try to get user from the new api_keys table
        with track_database_query(table="api_keys_new", operation="select"):
            key_result = client.table("api_keys_new").select("*").eq("api_key", api_key).execute()

        if key_result.data and len(key_result.data) > 0:
            try:
                key_data = safe_get_first(
                    key_result,
                    error_message="API key found but data structure invalid",
                    validate_keys=["id", "user_id"]
                )
                user_id = key_data["user_id"]

                # Get user info from users table
                with track_database_query(table="users", operation="select"):
                    user_result = client.table("users").select("*").eq("id", user_id).execute()

                if user_result.data and len(user_result.data) > 0:
                    user = safe_get_first(
                        user_result,
                        error_message=f"User {user_id} not found",
                        validate_keys=["id"]
                    )
                    # Add key information to user data
                    user["key_id"] = key_data["id"]
                    user["key_name"] = key_data.get("key_name", "")
                    user["environment_tag"] = key_data.get("environment_tag", "live")
                    user["scope_permissions"] = key_data.get("scope_permissions")
                    user["is_primary"] = key_data.get("is_primary", False)
                    return user
            except (DatabaseResultError, KeyError) as e:
                logger.warning(f"Error extracting API key data: {e}. Falling back to legacy lookup.")

        # Fallback: Check if this is a legacy key (for backward compatibility during migration)
        # PERF: Legacy keys are allowed to authenticate immediately - no blocking
        with track_database_query(table="users", operation="select"):
            legacy_result = client.table("users").select("*").eq("api_key", api_key).execute()
        if legacy_result.data and len(legacy_result.data) > 0:
            try:
                user = safe_get_first(
                    legacy_result,
                    error_message="Legacy key found but user data invalid",
                    validate_keys=["id"]
                )
            except (DatabaseResultError, KeyError) as e:
                logger.error(f"Error extracting legacy user data: {e}")
                return None

            # Check if this is a temporary key that should be replaced
            if _is_temporary_api_key(api_key):
                logger.debug(
                    "Temporary API key detected for user %s (length: %d) - allowing auth",
                    user.get("id"),
                    len(api_key),
                )
                # Mark the user as having an unmigrated temporary key
                user["_has_temporary_key"] = True
                # Allow auth to proceed - temporary keys work fine
                return user

            # PERF: Allow legacy key auth immediately, migrate in background
            # This removes the blocking migration from the auth hot path
            logger.debug(
                "Legacy API key detected for user %s - scheduling background migration",
                user.get("id"),
            )

            # Schedule non-blocking background migration
            _schedule_background_migration(client, user, api_key)

            # Return user immediately - don't wait for migration
            return user

        return None

    except Exception as e:
        logger.error("Error getting user: %s", sanitize_for_logging(str(e)))
        return None


def get_user(api_key: str) -> dict[str, Any] | None:
    """Get user by API key from unified system with caching (saves ~50ms per request)

    PERF: Uses in-memory cache with 60s TTL to reduce database queries.
    On cache hit, returns immediately without any DB calls.
    """
    # PERF: Check cache first to avoid database queries
    if api_key in _user_cache:
        entry = _user_cache[api_key]
        cache_time = entry["timestamp"]
        if time.time() - cache_time < _user_cache_ttl:
            logger.debug(f"User cache hit for API key {api_key[:10]}... (age: {time.time() - cache_time:.1f}s)")
            return entry["user"]
        else:
            # Cache expired, remove it
            del _user_cache[api_key]
            logger.debug(f"User cache expired for API key {api_key[:10]}...")

    # Cache miss - fetch from database
    logger.debug(f"User cache miss for API key {api_key[:10]}... - fetching from database")
    user = _get_user_uncached(api_key)

    # Cache the result (even if None, to avoid repeated DB queries for invalid keys)
    # Only cache valid users to avoid caching None for typos/invalid keys
    if user is not None:
        _user_cache[api_key] = {
            "user": user,
            "timestamp": time.time(),
        }

    return user


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """
    Get user by user ID (primary key)

    Args:
        user_id: User's numeric ID

    Returns:
        User dictionary if found, None otherwise
    """
    try:
        client = get_supabase_client()

        result = client.table("users").select("*").eq("id", user_id).execute()

        if result.data and len(result.data) > 0:
            return result.data[0]

        return None

    except Exception as e:
        logger.error(
            "Error getting user by ID %s: %s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(str(e)),
        )
        return None


def get_user_by_privy_id(privy_user_id: str) -> dict[str, Any] | None:
    """Get user by Privy user ID"""
    try:
        client = get_supabase_client()

        result = client.table("users").select("*").eq("privy_user_id", privy_user_id).execute()

        if result.data:
            return result.data[0]

        return None

    except Exception as e:
        logger.error("Error getting user by Privy ID: %s", sanitize_for_logging(str(e)))
        return None


def get_user_by_username(username: str) -> dict[str, Any] | None:
    """Get user by username"""
    try:
        client = get_supabase_client()

        result = client.table("users").select("*").eq("username", username).execute()

        if result.data:
            return result.data[0]

        return None

    except Exception as e:
        logger.error("Error getting user by username: %s", sanitize_for_logging(str(e)))
        return None


def add_credits_to_user(
    user_id: int,
    credits: float,
    transaction_type: str = "admin_credit",
    description: str = "Credits added",
    payment_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Add credits to user account by user ID and log the transaction.
    For purchases, credits go to purchased_credits field.
    For other types, credits go to subscription_allowance field (or purchased_credits if not a subscription).

    Args:
        user_id: User ID
        credits: Amount of credits to add
        transaction_type: Type of transaction (trial, purchase, admin_credit, etc.)
        description: Description of the transaction
        payment_id: Optional payment ID if this is from a payment
        metadata: Optional metadata dictionary
    """
    if credits <= 0:
        raise ValueError("Credits must be positive")

    try:
        from src.db.credit_transactions import log_credit_transaction

        client = get_supabase_client()

        # Get current balances
        user_result = client.table("users").select(
            "subscription_allowance, purchased_credits"
        ).eq("id", user_id).execute()

        try:
            user_data = safe_get_first(
                user_result,
                error_message=f"User with ID {user_id} not found",
                validate_keys=[]
            )
            allowance_before = safe_get_value(user_data, "subscription_allowance", default=0.0, expected_type=float)
            purchased_before = safe_get_value(user_data, "purchased_credits", default=0.0, expected_type=float)
            balance_before = allowance_before + purchased_before
        except (DatabaseResultError, KeyError, TypeError) as e:
            logger.error(f"Error getting user balance for user {user_id}: {e}")
            raise ValueError(f"Failed to get user balance: {e}")

        # Determine which field to update based on transaction type
        # Purchases go to purchased_credits, everything else uses current logic
        if transaction_type == "purchase":
            # Credit purchases go to purchased_credits field
            purchased_after = purchased_before + credits
            allowance_after = allowance_before
            update_data = {
                "purchased_credits": purchased_after,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
        else:
            # Other types (admin_credit, trial, etc.) - add to purchased for simplicity
            # Could be enhanced to add to allowance for subscription-related credits
            purchased_after = purchased_before + credits
            allowance_after = allowance_before
            update_data = {
                "purchased_credits": purchased_after,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

        balance_after = allowance_after + purchased_after

        # Update user credits
        result = (
            client.table("users")
            .update(update_data)
            .eq("id", user_id)
            .execute()
        )

        if not result.data:
            raise ValueError(f"User with ID {user_id} not found")

        # Log the transaction
        log_credit_transaction(
            user_id=user_id,
            amount=credits,
            transaction_type=transaction_type,
            description=description,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_id=payment_id,
            metadata={
                **(metadata or {}),
                "allowance_before": allowance_before,
                "allowance_after": allowance_after,
                "purchased_before": purchased_before,
                "purchased_after": purchased_after,
            },
        )

        logger.info(
            "Added %s credits to user %s. Balance: %s → %s (allowance: %s → %s, purchased: %s → %s)",
            sanitize_for_logging(str(credits)),
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(str(balance_before)),
            sanitize_for_logging(str(balance_after)),
            sanitize_for_logging(str(allowance_before)),
            sanitize_for_logging(str(allowance_after)),
            sanitize_for_logging(str(purchased_before)),
            sanitize_for_logging(str(purchased_after)),
        )

        # Invalidate cache to ensure fresh credit balance on next get_user call
        invalidate_user_cache_by_id(user_id)

    except Exception as e:
        logger.error("Failed to add credits: %s", sanitize_for_logging(str(e)))
        raise RuntimeError(f"Failed to add credits: {e}")


def add_credits(api_key: str, credits: int) -> None:
    """Legacy function for backward compatibility"""
    user = get_user(api_key)
    if not user:
        raise ValueError(f"User with API key {api_key} not found")

    add_credits_to_user(
        user_id=user["id"],
        credits=credits,
        transaction_type="admin_credit",
        description="Legacy credit addition",
    )
    # Invalidate cache to ensure fresh credit balance on next get_user call
    invalidate_user_cache(api_key)


def log_api_usage_transaction(
    api_key: str,
    cost: float,
    description: str = "API usage",
    metadata: dict | None = None,
    is_trial: bool = False,
) -> None:
    """
    Log API usage transaction (for both trial and non-trial users)
    For trial users, logs with $0 cost. For non-trial users, deducts credits and logs transaction.

    Args:
        api_key: User's API key
        cost: Cost of the API call (will be 0 for trial users)
        description: Description of the usage
        metadata: Optional metadata (model used, tokens, etc.)
        is_trial: Whether user is on trial (if True, cost should be 0 and no credits deducted)
    """
    try:
        from src.db.credit_transactions import TransactionType, log_credit_transaction

        user = get_user(api_key)
        if not user:
            logger.warning(f"User with API key {api_key[:20]}... not found for transaction logging")
            return

        user_id = user["id"]
        balance_before = user.get("credits", 0.0) or 0.0
        balance_after = balance_before - cost if not is_trial else balance_before

        # Log the transaction (negative amount for usage)
        transaction_result = log_credit_transaction(
            user_id=user_id,
            amount=-cost,  # Negative for API usage
            transaction_type=TransactionType.API_USAGE,
            description=description,
            balance_before=balance_before,
            balance_after=balance_after,
            metadata={
                **(metadata or {}),
                "is_trial": is_trial,
            },
        )

        if not transaction_result:
            logger.error(
                f"Failed to log API usage transaction for user {user_id}. "
                f"Cost: ${cost}, Is Trial: {is_trial}"
            )
        else:
            logger.info(
                f"Logged API usage transaction for user {user_id}. "
                f"Cost: ${cost}, Is Trial: {is_trial}, Transaction ID: {transaction_result.get('id', 'unknown')}"
            )

    except Exception as e:
        logger.error(f"Failed to log API usage transaction: {e}", exc_info=True)


def deduct_credits(
    api_key: str, tokens: float, description: str = "API usage", metadata: dict | None = None
) -> None:
    """
    Deduct credits from user account by API key and log the transaction.
    Deducts from subscription_allowance first, then purchased_credits.

    Args:
        api_key: User's API key
        tokens: Amount of credits to deduct
        description: Description of the usage
        metadata: Optional metadata (model used, tokens, etc.)
    """
    # Allow very small amounts but not exactly 0 or negative
    if tokens < 0:
        raise ValueError("Credits cannot be negative")

    # If tokens is 0 or very small (less than $0.000001), skip deduction but log usage
    if tokens < 0.000001:
        logger.info(
            "Skipping credit deduction for minimal amount: $%s",
            sanitize_for_logging(f"{tokens:.10f}"),
        )
        return

    try:
        from src.db.credit_transactions import TransactionType, log_credit_transaction
        from src.services.daily_usage_limiter import (
            DailyUsageLimitExceeded,
            enforce_daily_usage_limit,
        )

        client = get_supabase_client()

        # CRITICAL FIX: Bypass cache and fetch fresh balance from database
        # This prevents race conditions from stale cached balance data
        # Use proper API key lookup to support both api_keys_new and legacy users.api_key

        # First, try api_keys_new table (primary key store)
        with track_database_query(table="api_keys_new", operation="select"):
            key_result = client.table("api_keys_new").select("user_id").eq("api_key", api_key).execute()

        if key_result.data:
            # Found in api_keys_new - get user data with both allowance and purchased credits
            user_id = key_result.data[0]["user_id"]
            with track_database_query(table="users", operation="select"):
                user_lookup = client.table("users").select(
                    "id, subscription_allowance, purchased_credits, tier"
                ).eq("id", user_id).execute()
        else:
            # Fallback: try legacy users.api_key column
            with track_database_query(table="users", operation="select"):
                user_lookup = client.table("users").select(
                    "id, subscription_allowance, purchased_credits, tier"
                ).eq("api_key", api_key).execute()

        if not user_lookup.data:
            raise ValueError(f"User with API key not found")

        user_id = user_lookup.data[0]["id"]

        # ADMIN BYPASS: Admin tier users don't consume credits
        try:
            from src.db.plans import is_admin_tier_user
            if is_admin_tier_user(user_id):
                logger.info(f"Admin tier user {user_id} - skipping credit deduction of ${tokens:.6f}")
                return
        except Exception as e:
            logger.warning(f"Error checking admin tier status for credit deduction: {e}")

        # CHECK DAILY USAGE LIMIT (NEW - 2026-01-05)
        # Enforce $1/day usage limit for all users (except admins who already bypassed above)
        # NOTE: Known race condition - concurrent requests may bypass limit by reading
        # before either writes. This is acceptable as:
        # 1. Fail-safe design (availability > strict enforcement)
        # 2. Optimistic lock below prevents balance corruption
        # 3. Most abuse is sequential, not concurrent
        # 4. Over-limit is tracked and can be flagged for review
        # Future: Consider advisory locks or atomic daily usage table for strict enforcement
        try:
            enforce_daily_usage_limit(user_id, tokens)
        except DailyUsageLimitExceeded as e:
            logger.warning(f"Daily usage limit exceeded for user {user_id}: {e}")
            raise ValueError(str(e)) from e

        # Get current balances
        allowance_before = float(user_lookup.data[0].get("subscription_allowance") or 0)
        purchased_before = float(user_lookup.data[0].get("purchased_credits") or 0)
        balance_before = allowance_before + purchased_before

        # Check sufficiency
        if balance_before < tokens:
            # SECURITY: Sanitize exact credit amounts from error messages
            # Round to nearest $0.01 to avoid exposing precise financial data in logs
            balance_rounded = round(balance_before, 2)
            required_rounded = round(tokens, 2)

            raise ValueError(
                f"Insufficient credits. Current balance: ~${balance_rounded:.2f}, "
                f"Required: ~${required_rounded:.2f}. Please add credits to continue."
            )

        # Calculate deduction breakdown: deduct from allowance first, then purchased
        from_allowance = min(allowance_before, tokens)
        from_purchased = tokens - from_allowance

        allowance_after = allowance_before - from_allowance
        purchased_after = purchased_before - from_purchased
        balance_after = allowance_after + purchased_after

        # Use optimistic locking on both fields: update only if neither has changed
        # This prevents race conditions where multiple requests deduct simultaneously
        with track_database_query(table="users", operation="update"):
            result = (
                client.table("users")
                .update({
                    "subscription_allowance": allowance_after,
                    "purchased_credits": purchased_after,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                .eq("id", user_id)
                .eq("subscription_allowance", allowance_before)  # Optimistic lock
                .eq("purchased_credits", purchased_before)  # Optimistic lock
                .execute()
            )

        if not result.data:
            # Balance changed between our read and update (concurrent modification)
            # Fetch current balance and fail with accurate error
            with track_database_query(table="users", operation="select"):
                current = client.table("users").select(
                    "subscription_allowance, purchased_credits"
                ).eq("id", user_id).execute()

            if current.data and len(current.data) > 0:
                current_allowance = float(current.data[0].get("subscription_allowance") or 0)
                current_purchased = float(current.data[0].get("purchased_credits") or 0)
                current_balance = current_allowance + current_purchased
            else:
                current_balance = "unknown"

            # SECURITY: Sanitize credit amounts in concurrent modification errors too
            current_rounded = round(float(current_balance), 2) if isinstance(current_balance, (int, float)) else "unknown"
            required_rounded = round(tokens, 2)

            raise ValueError(
                f"Failed to update balance due to concurrent modification. "
                f"Current balance: ~${current_rounded:.2f if isinstance(current_rounded, float) else ''}{current_rounded if not isinstance(current_rounded, float) else ''}, "
                f"Required: ~${required_rounded:.2f}. Please retry."
            )

        # Log the transaction with breakdown (negative amount for deduction)
        transaction_metadata = {
            **(metadata or {}),
            "from_allowance": from_allowance,
            "from_purchased": from_purchased,
            "allowance_before": allowance_before,
            "allowance_after": allowance_after,
            "purchased_before": purchased_before,
            "purchased_after": purchased_after,
        }

        transaction_result = log_credit_transaction(
            user_id=user_id,
            amount=-tokens,  # Negative for deduction
            transaction_type=TransactionType.API_USAGE,
            description=description,
            balance_before=balance_before,
            balance_after=balance_after,
            metadata=transaction_metadata,
        )

        if not transaction_result:
            logger.error(
                f"Failed to log credit transaction for user {user_id}. "
                f"Credits were deducted but transaction not logged. "
                f"Amount: -${tokens:.6f}, Balance: ${balance_before:.6f} → ${balance_after:.6f}"
            )
            # Don't raise here - credits were already deducted, just log the error
        else:
            logger.info(
                "Deducted $%s from user %s. Balance: $%s → $%s "
                "(allowance: $%s → $%s, purchased: $%s → $%s). Transaction logged: %s",
                sanitize_for_logging(f"{tokens:.6f}"),
                sanitize_for_logging(str(user_id)),
                sanitize_for_logging(f"{balance_before:.6f}"),
                sanitize_for_logging(f"{balance_after:.6f}"),
                sanitize_for_logging(f"{allowance_before:.6f}"),
                sanitize_for_logging(f"{allowance_after:.6f}"),
                sanitize_for_logging(f"{purchased_before:.6f}"),
                sanitize_for_logging(f"{purchased_after:.6f}"),
                transaction_result.get("id", "unknown"),
            )

        # Invalidate cache to ensure fresh credit balance on next get_user call
        invalidate_user_cache(api_key)

    except Exception as e:
        logger.error("Failed to deduct credits: %s", sanitize_for_logging(str(e)), exc_info=True)
        # Wrap exceptions in RuntimeError for consistency with test expectations
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Failed to deduct credits: {e}") from e


def reset_subscription_allowance(user_id: int, allowance_amount: float, tier: str) -> bool:
    """
    Reset subscription allowance on subscription creation or renewal.
    Old allowance is forfeited (no carry-over).

    Args:
        user_id: User ID
        allowance_amount: New allowance amount based on tier
        tier: User's subscription tier

    Returns:
        True if successful, False otherwise
    """
    try:
        from src.db.credit_transactions import TransactionType, log_credit_transaction

        client = get_supabase_client()
        now = datetime.now(timezone.utc).isoformat()

        # Get current allowance (will be forfeited)
        user_result = client.table("users").select(
            "subscription_allowance, purchased_credits"
        ).eq("id", user_id).execute()

        if not user_result.data:
            logger.error(f"User {user_id} not found for allowance reset")
            return False

        old_allowance = float(user_result.data[0].get("subscription_allowance") or 0)
        purchased = float(user_result.data[0].get("purchased_credits") or 0)

        # Reset allowance to new amount
        result = (
            client.table("users")
            .update({
                "subscription_allowance": allowance_amount,
                "allowance_reset_date": now,
                "updated_at": now,
            })
            .eq("id", user_id)
            .execute()
        )

        if not result.data:
            logger.error(f"Failed to reset allowance for user {user_id}")
            return False

        # Log the transaction
        log_credit_transaction(
            user_id=user_id,
            amount=allowance_amount,  # Positive amount for reset
            transaction_type=TransactionType.SUBSCRIPTION_RENEWAL,
            description=f"Monthly allowance reset - {tier.upper()} tier (${allowance_amount})",
            balance_before=old_allowance + purchased,
            balance_after=allowance_amount + purchased,
            metadata={
                "tier": tier,
                "forfeited_allowance": old_allowance,
                "new_allowance": allowance_amount,
                "purchased_credits_unchanged": purchased,
            },
        )

        # Invalidate cache
        invalidate_user_cache_by_id(user_id)

        logger.info(
            f"Reset allowance for user {user_id}: ${old_allowance} -> ${allowance_amount} ({tier} tier)"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to reset subscription allowance for user {user_id}: {e}", exc_info=True)
        return False


def forfeit_subscription_allowance(user_id: int, raise_on_error: bool = False) -> float:
    """
    Forfeit remaining subscription allowance on cancellation.
    Purchased credits are preserved.

    Args:
        user_id: User ID
        raise_on_error: If True, raises exception on database errors instead of returning 0.0

    Returns:
        Amount of allowance that was forfeited (0.0 if user had no allowance)

    Raises:
        RuntimeError: If raise_on_error is True and database operation fails
    """
    try:
        from src.db.credit_transactions import TransactionType, log_credit_transaction

        client = get_supabase_client()
        now = datetime.now(timezone.utc).isoformat()

        # Get current balances
        user_result = client.table("users").select(
            "subscription_allowance, purchased_credits"
        ).eq("id", user_id).execute()

        if not user_result.data:
            error_msg = f"User {user_id} not found for allowance forfeiture"
            logger.error(error_msg)
            if raise_on_error:
                raise RuntimeError(error_msg)
            return 0.0

        forfeited_amount = float(user_result.data[0].get("subscription_allowance") or 0)
        purchased = float(user_result.data[0].get("purchased_credits") or 0)

        if forfeited_amount > 0:
            # Set allowance to 0
            update_result = client.table("users").update({
                "subscription_allowance": 0,
                "updated_at": now,
            }).eq("id", user_id).execute()

            # Check if update succeeded
            if not update_result.data:
                error_msg = f"Failed to update subscription_allowance to 0 for user {user_id}"
                logger.error(error_msg)
                if raise_on_error:
                    raise RuntimeError(error_msg)
                return 0.0

            # Log forfeiture
            log_credit_transaction(
                user_id=user_id,
                amount=-forfeited_amount,
                transaction_type=TransactionType.SUBSCRIPTION_CANCELLATION,
                description="Subscription allowance forfeited on cancellation",
                balance_before=forfeited_amount + purchased,
                balance_after=purchased,
                metadata={
                    "forfeited_allowance": forfeited_amount,
                    "purchased_credits_preserved": purchased,
                },
            )

            # Invalidate cache
            invalidate_user_cache_by_id(user_id)

            logger.info(f"Forfeited ${forfeited_amount} allowance for user {user_id}")

        return forfeited_amount

    except Exception as e:
        logger.error(
            f"Failed to forfeit subscription allowance for user {user_id}: {e}",
            exc_info=True,
        )
        if raise_on_error:
            raise RuntimeError(
                f"Failed to forfeit subscription allowance for user {user_id}: {e}"
            ) from e
        return 0.0


def get_all_users() -> list[dict[str, Any]]:
    try:
        client = get_supabase_client()
        result = client.table("users").select("*").execute()
        return result.data

    except Exception as e:
        logger.error("Error getting all users: %s", sanitize_for_logging(str(e)))
        return []


def delete_user(api_key: str) -> None:
    try:
        client = get_supabase_client()
        result = client.table("users").delete().eq("api_key", api_key).execute()

        if not result.data:
            raise ValueError(f"User with API key {api_key} not found")

    except Exception as e:
        logger.error("Failed to delete user: %s", sanitize_for_logging(str(e)))
        raise RuntimeError(f"Failed to delete user: {e}")


def get_user_count() -> int:
    try:
        client = get_supabase_client()
        result = client.table("users").select("*", count="exact").execute()
        return result.count or 0

    except Exception as e:
        logger.error("Error getting user count: %s", sanitize_for_logging(str(e)))
        return 0


def record_usage(
    user_id: int,
    api_key: str,
    model: str,
    tokens_used: int,
    cost: float = 0.0,
    latency_ms: int = None,
) -> None:
    """
    Record usage in the usage_records table.
    Note: latency_ms is accepted for backward compatibility but not stored (column doesn't exist in DB).
    Use activity_log for detailed metrics including latency and gateway info.
    """
    try:
        client = get_supabase_client()

        # Ensure timestamp is timezone-aware
        timestamp = datetime.now(timezone.utc).replace(tzinfo=timezone.utc).isoformat()

        # Only include columns that exist in the schema
        usage_data = {
            "user_id": user_id,
            "api_key": api_key,
            "model": model,
            "tokens_used": tokens_used,
            "cost": cost,
            "timestamp": timestamp,
        }

        client.table("usage_records").insert(usage_data).execute()

        logger.info(
            "Usage recorded successfully: user_id=%s, api_key=%s, model=%s, tokens=%s, cost=%s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(api_key[:20] + "..."),
            sanitize_for_logging(model),
            sanitize_for_logging(str(tokens_used)),
            sanitize_for_logging(str(cost)),
        )

    except Exception as e:
        logger.error("Failed to record usage: %s", sanitize_for_logging(str(e)))
        # Don't raise the exception to avoid breaking the main flow


def get_user_usage_metrics(api_key: str) -> dict[str, Any]:
    try:
        client = get_supabase_client()

        # Get user info from api_keys_new table first
        key_result = client.table("api_keys_new").select("user_id").eq("api_key", api_key).execute()
        if not key_result.data:
            # Fallback to legacy users table
            user_result = (
                client.table("users").select("id, credits").eq("api_key", api_key).execute()
            )
            if not user_result.data:
                return None
            user_id = user_result.data[0]["id"]
        else:
            user_id = key_result.data[0]["user_id"]

        # Get user credits
        user_result = client.table("users").select("credits").eq("id", user_id).execute()
        if not user_result.data:
            return None

        current_credits = user_result.data[0]["credits"]

        # Use the database function to get usage metrics
        result = client.rpc("get_user_usage_metrics", {"user_api_key": api_key}).execute()

        if not result.data:
            # If no usage data, return empty metrics
            return {
                "user_id": user_id,
                "current_credits": current_credits,
                "usage_metrics": {
                    "total_requests": 0,
                    "total_tokens": 0,
                    "total_cost": 0.0,
                    "requests_today": 0,
                    "tokens_today": 0,
                    "cost_today": 0.0,
                    "requests_this_month": 0,
                    "tokens_this_month": 0,
                    "cost_this_month": 0.0,
                    "average_tokens_per_request": 0.0,
                    "most_used_model": "No models used",
                    "last_request_time": None,
                },
            }

        metrics = result.data[0]

        return {
            "user_id": user_id,
            "current_credits": current_credits,
            "usage_metrics": {
                "total_requests": metrics["total_requests"],
                "total_tokens": metrics["total_tokens"],
                "total_cost": float(metrics["total_cost"]),
                "requests_today": metrics["requests_today"],
                "tokens_today": metrics["tokens_today"],
                "cost_today": float(metrics["cost_today"]),
                "requests_this_month": metrics["requests_this_month"],
                "tokens_this_month": metrics["tokens_this_month"],
                "cost_this_month": float(metrics["cost_this_month"]),
                "average_tokens_per_request": float(metrics["average_tokens_per_request"]),
                "most_used_model": metrics["most_used_model"] or "No models used",
                "last_request_time": metrics["last_request_time"],
            },
        }

    except Exception as e:
        logger.error("Error getting user usage metrics: %s", sanitize_for_logging(str(e)))
        return None


def get_admin_monitor_data() -> dict[str, Any]:
    """Get admin monitoring data with robust error handling

    NOTE: This function uses count queries and date-filtered queries to avoid
    hitting Supabase's default 1000 row limit. For accurate counts, we use
    count="exact" which performs a COUNT(*) query on the server side.
    """
    try:
        client = get_supabase_client()

        # Calculate time boundaries for filtering
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(days=1)
        month_ago = now - timedelta(days=30)

        # Get ACCURATE total user count using count="exact" (server-side COUNT(*))
        # This avoids the 1000 row limit issue
        total_users = 0
        users = []
        try:
            # First get accurate count
            count_result = client.table("users").select("id", count="exact").execute()
            total_users = count_result.count or 0
            logger.info(f"Total users count (exact): {total_users}")

            # Then get user data for credit calculations (limited to avoid memory issues)
            users_result = client.table("users").select("id, credits, api_key").limit(10000).execute()
            users = users_result.data or []
        except Exception as e:
            logger.error("Error retrieving users: %s", sanitize_for_logging(str(e)))
            users = []
            total_users = 0

        # Get activity_log data for TODAY only (primary source - actively updated)
        # This is the main source of truth for API usage tracking
        # Filter by date to avoid hitting the 1000 row limit
        activity_logs_today = []
        activity_logs_month = []
        total_activity_count = 0
        try:
            # Get accurate total count of all activity logs
            total_count_result = client.table("activity_log").select("id", count="exact").execute()
            total_activity_count = total_count_result.count or 0

            # Get today's activity logs (should be much less than 1000 typically)
            day_ago_iso = day_ago.isoformat()
            activity_today_result = (
                client.table("activity_log")
                .select("*")
                .gte("timestamp", day_ago_iso)
                .order("timestamp", desc=True)
                .limit(10000)  # Higher limit for today's data
                .execute()
            )
            activity_logs_today = activity_today_result.data or []
            logger.info(f"Retrieved {len(activity_logs_today)} activity log records for today")

            # Get this month's activity logs for monthly stats
            month_ago_iso = month_ago.isoformat()
            activity_month_result = (
                client.table("activity_log")
                .select("*")
                .gte("timestamp", month_ago_iso)
                .order("timestamp", desc=True)
                .limit(50000)  # Higher limit for monthly data
                .execute()
            )
            activity_logs_month = activity_month_result.data or []
            logger.info(f"Retrieved {len(activity_logs_month)} activity log records for this month")

        except Exception as e:
            logger.error(f"Error retrieving activity_log: {e}", exc_info=True)
            activity_logs_today = []
            activity_logs_month = []
            total_activity_count = 0  # Reset count on error for consistency

        # Get usage records data as fallback (legacy table, may not be updated)
        # Get both today's and month's records for complete data
        usage_records_legacy_today = []
        usage_records_legacy_month = []
        try:
            # Get today's legacy records
            day_ago_iso = day_ago.isoformat()
            usage_today_result = (
                client.table("usage_records")
                .select("*")
                .gte("timestamp", day_ago_iso)
                .limit(10000)
                .execute()
            )
            usage_records_legacy_today = usage_today_result.data or []
            logger.debug(f"Retrieved {len(usage_records_legacy_today)} legacy usage_records for today")

            # Get month's legacy records for complete monthly stats
            month_ago_iso = month_ago.isoformat()
            usage_month_result = (
                client.table("usage_records")
                .select("*")
                .gte("timestamp", month_ago_iso)
                .limit(50000)
                .execute()
            )
            usage_records_legacy_month = usage_month_result.data or []
            logger.debug(f"Retrieved {len(usage_records_legacy_month)} legacy usage_records for month")
        except Exception as e:
            logger.warning(f"Error retrieving usage_records (legacy): {e}")
            usage_records_legacy_today = []
            usage_records_legacy_month = []

        # Create user_id -> api_key mapping for efficient lookup
        # Only fetch mappings for users that appear in activity logs to avoid
        # hitting row limits on systems with many users
        active_user_ids = set()
        for activity in activity_logs_today:
            user_id = activity.get("user_id")
            if user_id:
                active_user_ids.add(user_id)
        for activity in activity_logs_month:
            user_id = activity.get("user_id")
            if user_id:
                active_user_ids.add(user_id)

        # Create mappings for both directions:
        # - user_id_to_api_key: for resolving api_key from activity_log user_ids
        # - api_key_to_user_id: for deduplication with legacy usage_records that only have api_key
        user_id_to_api_key = {}
        api_key_to_user_id = {}

        # First, build mappings from already-fetched users data
        for user in users:
            user_id = user.get("id")
            api_key = user.get("api_key")
            if user_id and api_key:
                user_id_to_api_key[user_id] = api_key
                api_key_to_user_id[api_key] = user_id

        # Also check api_keys_new table for users who might not have api_key in users table
        try:
            api_keys_result = (
                client.table("api_keys_new").select("user_id, api_key, is_primary").limit(10000).execute()
            )
            if api_keys_result.data:
                for key_data in api_keys_result.data:
                    user_id = key_data.get("user_id")
                    api_key = key_data.get("api_key")
                    is_primary = key_data.get("is_primary", False)
                    # Prefer primary keys, but update if user_id not in mapping
                    if user_id and api_key:
                        if user_id not in user_id_to_api_key or is_primary:
                            user_id_to_api_key[user_id] = api_key
                        # Always add to reverse mapping (needed for deduplication)
                        api_key_to_user_id[api_key] = user_id
        except Exception as e:
            logger.warning(f"Error retrieving api_keys_new for user mapping: {e}")

        # Convert activity_log to usage_records format for compatibility
        # activity_log has: user_id, model, provider, tokens, cost, timestamp, metadata
        # usage_records format: user_id, api_key, model, tokens_used, cost, timestamp

        # Process TODAY's activity logs
        day_usage = []
        for activity in activity_logs_today:
            user_id = activity.get("user_id")
            api_key = user_id_to_api_key.get(user_id, "unknown")
            usage_record = {
                "user_id": user_id,
                "api_key": api_key,
                "model": activity.get("model", "unknown"),
                "tokens_used": activity.get("tokens", 0),
                "cost": activity.get("cost", 0.0),
                "timestamp": activity.get("timestamp", ""),
            }
            day_usage.append(usage_record)

        # Add legacy usage_records for today that might not be in activity_log
        # Use composite key (user_id + timestamp + model) for deduplication since:
        # 1. activity_log and usage_records have different ID spaces
        # 2. Timestamps alone are not unique (concurrent requests can share timestamps)
        # A composite key provides a reasonable approximation of uniqueness
        def make_composite_key(record):
            """Create a composite key from user_id, timestamp, and model.

            Uses user_id as the consistent identifier across both activity_log and
            usage_records tables. For legacy records that only have api_key,
            we look up the corresponding user_id from the api_key_to_user_id mapping.
            """
            # Always prefer user_id for consistent deduplication
            user_id = record.get("user_id")
            if not user_id:
                # Legacy records may only have api_key - look up user_id
                api_key = record.get("api_key")
                if api_key:
                    user_id = api_key_to_user_id.get(api_key)
            # Convert to string for consistent key format
            user_key = str(user_id) if user_id else ""
            timestamp = record.get("timestamp") or ""
            model = record.get("model") or ""
            return f"{user_key}|{timestamp}|{model}"

        activity_keys_today = {make_composite_key(r) for r in activity_logs_today}
        for legacy_record in usage_records_legacy_today:
            legacy_key = make_composite_key(legacy_record)
            # Only include legacy record if it's not already represented in activity_log
            if legacy_key not in activity_keys_today:
                day_usage.append(legacy_record)

        # Sort day_usage by timestamp descending to ensure correct order for recent activity
        day_usage.sort(key=lambda r: r.get("timestamp", ""), reverse=True)


        # Process MONTH's activity logs
        month_usage = []
        for activity in activity_logs_month:
            user_id = activity.get("user_id")
            api_key = user_id_to_api_key.get(user_id, "unknown")
            usage_record = {
                "user_id": user_id,
                "api_key": api_key,
                "model": activity.get("model", "unknown"),
                "tokens_used": activity.get("tokens", 0),
                "cost": activity.get("cost", 0.0),
                "timestamp": activity.get("timestamp", ""),
            }
            month_usage.append(usage_record)

        # Add legacy usage_records for month that might not be in activity_log
        # Use composite key for deduplication (same approach as daily data)
        activity_keys_month = {make_composite_key(r) for r in activity_logs_month}
        for legacy_record in usage_records_legacy_month:
            legacy_key = make_composite_key(legacy_record)
            if legacy_key not in activity_keys_month:
                month_usage.append(legacy_record)

        # Calculate totals with safe aggregation
        def safe_sum(records, field):
            return sum(record.get(field, 0) or 0 for record in records)

        total_tokens_day = safe_sum(day_usage, "tokens_used")
        total_tokens_month = safe_sum(month_usage, "tokens_used")

        total_cost_day = safe_sum(day_usage, "cost")
        total_cost_month = safe_sum(month_usage, "cost")

        requests_day = len(day_usage)
        requests_month = len(month_usage)

        # For total requests, use the accurate count from server
        requests_all = total_activity_count

        # Use month_usage for top users and recent activity calculations
        # (this gives us a reasonable sample without fetching all historical data)

        # Get top users by usage (based on this month's data)
        user_usage = {}
        for record in month_usage:
            api_key = record.get("api_key", "unknown")
            if api_key not in user_usage:
                user_usage[api_key] = {"tokens_used": 0, "cost": 0, "requests": 0}
            user_usage[api_key]["tokens_used"] += record.get("tokens_used", 0) or 0
            user_usage[api_key]["cost"] += record.get("cost", 0) or 0
            user_usage[api_key]["requests"] += 1

        top_users = sorted(user_usage.items(), key=lambda x: x[1]["tokens_used"], reverse=True)[:10]
        top_users_data = [{"api_key": k, **v} for k, v in top_users]

        # Get recent activity (from today's usage, already sorted by timestamp desc)
        recent_activity_data = [
            {
                "api_key": record.get("api_key", "unknown"),
                "model": record.get("model", "unknown"),
                "tokens_used": record.get("tokens_used", 0),
                "timestamp": record.get("timestamp", ""),
            }
            for record in day_usage[:20]
        ]

        # Calculate most used model safely (based on month's data)
        # Exclude "auth" entries as they are authentication events, not model usage
        most_used_model = "No models used"
        if month_usage:
            model_counts = {}
            for record in month_usage:
                model = record.get("model", "unknown")
                # Skip auth events - they're not actual model usage
                if model.lower() == "auth":
                    continue
                model_counts[model] = model_counts.get(model, 0) + 1

            if model_counts:
                most_used_model = max(model_counts.items(), key=lambda x: x[1])[0]

        # Calculate last request time safely (prefer today's data, fall back to month)
        last_request_time = None
        if day_usage:
            timestamps = [
                record.get("timestamp", "") for record in day_usage if record.get("timestamp")
            ]
            if timestamps:
                last_request_time = max(timestamps)
        # Fall back to month_usage if no activity today
        if last_request_time is None and month_usage:
            timestamps = [
                record.get("timestamp", "") for record in month_usage if record.get("timestamp")
            ]
            if timestamps:
                last_request_time = max(timestamps)

        # Build response data
        # Note: For "all time" metrics, we use monthly data as a proxy since
        # fetching all historical data would be too expensive. The total_requests
        # count is accurate thanks to the count="exact" query.
        response_data = {
            "total_users": total_users,
            "active_users_today": len({record.get("api_key", "") for record in day_usage}),
            "total_requests_today": requests_day,
            "total_tokens_today": total_tokens_day,
            "total_cost_today": total_cost_day,
            "system_usage_metrics": {
                "total_requests": requests_all,
                "total_tokens": total_tokens_month,  # Use monthly as proxy for "all"
                "total_cost": total_cost_month,  # Use monthly as proxy for "all"
                "requests_today": requests_day,
                "tokens_today": total_tokens_day,
                "cost_today": total_cost_day,
                "requests_this_month": requests_month,
                "tokens_this_month": total_tokens_month,
                "cost_this_month": total_cost_month,
                "average_tokens_per_request": (
                    total_tokens_month / requests_month if requests_month > 0 else 0
                ),
                "most_used_model": most_used_model,
                "last_request_time": last_request_time,
            },
            "top_users_by_usage": top_users_data,
            "recent_activity": recent_activity_data,
        }

        return response_data

    except Exception as e:
        logger.error(f"Error getting admin monitor data: {e}")

        # Return a minimal response with error information
        return {
            "total_users": 0,
            "active_users_today": 0,
            "total_requests_today": 0,
            "total_tokens_today": 0,
            "total_cost_today": 0.0,
            "system_usage_metrics": {
                "total_requests": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "requests_today": 0,
                "tokens_today": 0,
                "cost_today": 0.0,
                "requests_this_month": 0,
                "tokens_this_month": 0,
                "cost_this_month": 0.0,
                "average_tokens_per_request": 0.0,
                "most_used_model": "No models used",
                "last_request_time": None,
            },
            "top_users_by_usage": [],
            "recent_activity": [],
            "error": str(e),
        }


def update_user_profile(api_key: str, profile_data: dict[str, Any]) -> dict[str, Any]:
    """Update user profile information"""
    try:
        client = get_supabase_client()

        # Get current user
        user = get_user(api_key)
        if not user:
            raise ValueError(f"User with API key {api_key} not found")

        # Prepare update data
        update_data = {}
        allowed_fields = ["name", "email", "preferences", "settings"]

        for field, value in profile_data.items():
            if field in allowed_fields:
                update_data[field] = value

        if not update_data:
            raise ValueError("No valid profile fields to update")

        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Update user profile
        result = client.table("users").update(update_data).eq("api_key", api_key).execute()

        if not result.data:
            raise ValueError("Failed to update user profile")

        # Return updated user data
        updated_user = get_user(api_key)
        return updated_user

    except Exception as e:
        logger.error(f"Failed to update user profile: {e}")
        raise RuntimeError(f"Failed to update user profile: {e}")


def get_user_profile(api_key: str) -> dict[str, Any]:
    """Get user profile information"""
    try:
        logger.info(f"get_user_profile called for API key: {api_key[:10]}...")
        user = get_user(api_key)
        if not user:
            logger.warning(f"get_user returned None for API key: {api_key[:10]}...")
            return None

        logger.info(f"Building profile for user {user.get('id')}")

        # Map tier to display-friendly name
        tier = user.get("tier")
        tier_display_map = {"basic": "Basic", "pro": "Pro", "max": "MAX"}
        tier_display_name = tier_display_map.get(tier) if tier else None

        # Calculate credit breakdown
        # Database stores values in dollars, but frontend expects cents for consistency with TIER_CONFIG
        subscription_allowance_dollars = float(user.get("subscription_allowance") or 0)
        purchased_credits_dollars = float(user.get("purchased_credits") or 0)
        legacy_credits_dollars = float(user.get("credits") or 0)

        # If tiered fields are empty but legacy credits exist, use legacy credits
        # For Pro/Max users with active subscription, legacy credits should go to subscription_allowance
        # For basic users or purchased credits, they should go to purchased_credits
        if subscription_allowance_dollars == 0 and purchased_credits_dollars == 0 and legacy_credits_dollars > 0:
            if tier in ("pro", "max") and user.get("subscription_status") == "active":
                # Active Pro/Max subscriber - legacy credits are subscription allowance
                subscription_allowance_dollars = legacy_credits_dollars
            else:
                # Basic user or no active subscription - legacy credits are purchased credits
                purchased_credits_dollars = legacy_credits_dollars

        total_credits_dollars = subscription_allowance_dollars + purchased_credits_dollars

        # Convert to cents for frontend (multiply by 100)
        subscription_allowance_cents = int(subscription_allowance_dollars * 100)
        purchased_credits_cents = int(purchased_credits_dollars * 100)
        total_credits_cents = int(total_credits_dollars * 100)

        # Return profile data
        profile = {
            "user_id": user["id"],
            "api_key": f"{api_key[:10]}...",
            "credits": total_credits_cents,  # Total credits in cents (sum for backward compatibility)
            "subscription_allowance": subscription_allowance_cents,  # Monthly subscription allowance in cents
            "purchased_credits": purchased_credits_cents,  # One-time purchased credits in cents
            "total_credits": total_credits_cents,  # Explicit sum of both in cents
            "allowance_reset_date": user.get("allowance_reset_date"),  # When allowance was last reset
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
            "username": user.get("username"),
            "email": user.get("email"),
            "auth_method": user.get("auth_method"),
            "subscription_status": user.get("subscription_status"),
            "tier": tier,  # Include subscription tier (basic, pro, max)
            "tier_display_name": tier_display_name,  # Display-friendly name (Basic, Pro, MAX)
            "trial_expires_at": user.get("trial_expires_at"),
            "subscription_end_date": user.get("subscription_end_date"),  # Unix timestamp
            "is_active": user.get("is_active"),
            "registration_date": user.get("registration_date"),
            "settings": user.get("settings"),  # User settings including auto top-up configuration
        }

        logger.info(f"Profile built successfully for user {user.get('id')}")
        return profile

    except Exception as e:
        logger.error(f"Failed to get user profile: {e}", exc_info=True)
        return None


def mark_welcome_email_sent(user_id: int) -> bool:
    """Mark welcome email as sent for a user"""
    try:
        client = get_supabase_client()

        result = (
            client.table("users")
            .update(
                {"welcome_email_sent": True, "updated_at": datetime.now(timezone.utc).isoformat()}
            )
            .eq("id", user_id)
            .execute()
        )

        if not result.data:
            raise ValueError(f"User with ID {user_id} not found")

        logger.info(f"Welcome email marked as sent for user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to mark welcome email as sent: {e}")
        raise RuntimeError(f"Failed to mark welcome email as sent: {e}")


def delete_user_account(api_key: str) -> bool:
    """Delete user account and all associated data"""
    try:
        client = get_supabase_client()

        # Get user first to verify existence
        user = get_user(api_key)
        if not user:
            raise ValueError(f"User with API key {api_key} not found")

        user_id = user["id"]

        # Delete user (this will cascade delete all related records due to CASCADE constraints)
        result = client.table("users").delete().eq("api_key", api_key).execute()

        if not result.data:
            raise ValueError("Failed to delete user account")

        logger.info(f"Successfully deleted user account {user_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete user account: {e}")
        raise RuntimeError(f"Failed to delete user account: {e}")
