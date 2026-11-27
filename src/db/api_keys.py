import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from postgrest import APIError

from src.config.supabase_config import execute_with_retry, get_supabase_client
from src.config.usage_limits import TRIAL_CREDITS_AMOUNT, TRIAL_DURATION_DAYS
from src.db.api_key_lookup import (
    find_key_row,
    key_label,
    new_key_storage_fields,
    plaintext_from_row,
    query_by_key,
    safe_db_error,
)
from src.db.plans import check_plan_entitlements
from src.db.postgrest_schema import is_schema_cache_error
from src.db.rate_limit_defaults import default_rate_limit_config
from src.utils.api_key_lookup import mask_api_key_for_logging
from src.utils.db_safety import DatabaseResultError, safe_get_first
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

SCHEMA_CACHE_ERROR_CODE = "PGRST204"


# near the top of the module
def _pct(used: int, limit: int | None) -> float | None:
    if not limit:
        return None
    return round(min(100.0, (used / float(limit)) * 100.0), 6)


def _is_schema_cache_error(error: Exception) -> bool:
    """Return True if Supabase/PostgREST reported a schema cache miss."""
    return isinstance(error, APIError) and getattr(error, "code", None) == SCHEMA_CACHE_ERROR_CODE


def refresh_postgrest_schema_cache(client) -> bool:
    """Trigger PostgREST to reload its schema cache via RPC."""
    try:
        if not hasattr(client, "rpc"):
            logger.debug(
                "Supabase client does not expose rpc(); skipping schema cache refresh attempt"
            )
            return False
        client.rpc("refresh_postgrest_schema_cache", {}).execute()
        logger.info("Triggered PostgREST schema cache refresh after API key insert failure")
        return True
    except Exception as refresh_error:
        logger.warning(
            "Failed to refresh PostgREST schema cache: %s",
            sanitize_for_logging(str(refresh_error)),
        )
        return False


def _insert_api_key_row(client, api_key_payload: dict[str, Any]):
    """Insert one api_keys_new row."""
    return client.table("api_keys_new").insert(dict(api_key_payload)).execute()


def _handle_schema_cache_insert_error(
    client, api_key_payload: dict[str, Any], original_error: APIError
):
    """Retry an API key insert once after a PostgREST schema cache refresh.

    The insert is never retried without key_hash/encrypted_key: the plaintext
    is no longer stored, so a row without them could not authenticate.
    """
    if not _is_schema_cache_error(original_error):
        raise original_error

    logger.warning(
        "API key insert failed due to PostgREST schema cache miss (%s); attempting refresh",
        safe_db_error(original_error),
    )

    if not refresh_postgrest_schema_cache(client):
        raise original_error
    return _insert_api_key_row(client, api_key_payload)


def check_key_name_uniqueness(
    user_id: int, key_name: str, exclude_key_id: int | None = None
) -> bool:
    """Check if a key name is unique within the user's scope"""
    try:
        client = get_supabase_client()

        # Build query to check for existing keys with same name
        query = (
            client.table("api_keys_new")
            .select("id")
            .eq("user_id", user_id)
            .eq("key_name", key_name)
        )

        # If we're editing an existing key, exclude it from the check
        if exclude_key_id:
            query = query.neq("id", exclude_key_id)

        result = query.execute()

        # If no results, the name is unique
        return len(result.data) == 0

    except Exception as e:
        logger.error("Error checking key name uniqueness: %s", sanitize_for_logging(str(e)))
        # In case of error, assume name is not unique to be safe
        return False


def create_api_key(
    user_id: int,
    key_name: str,
    environment_tag: str = "live",
    scope_permissions: dict[str, Any] | None = None,
    expiration_days: int | None = None,
    max_requests: int | None = None,
    ip_allowlist: list[str] | None = None,
    domain_referrers: list[str] | None = None,
    is_primary: bool = False,
    subscription_status: str = "trial",
    purpose: str | None = None,
) -> tuple[str, int]:
    """Create a new API key for a user

    ``purpose`` must already be normalized (src/services/key_purpose.py): None
    for a general key, 'validator' for no-logging mode. It is only sent when
    set, so a general key's insert payload is unchanged -- and a validator key
    on a database without the column fails loudly instead of being created as
    a general key.
    """
    try:
        client = get_supabase_client()

        # Check for name uniqueness within user scope
        if not check_key_name_uniqueness(user_id, key_name):
            raise ValueError(
                f"Key name '{key_name}' already exists for this user. Please choose a different name."
            )

        # Enforce plan limits on key creation
        entitlements = check_plan_entitlements(user_id)
        monthly_limit = entitlements.get("monthly_request_limit")
        if max_requests and monthly_limit is not None and max_requests > monthly_limit:
            logger.warning(
                f"User {user_id} attempted to create key with max_requests {max_requests} exceeding plan limit {monthly_limit}"
            )
            max_requests = monthly_limit

        # Generate new API key with environment tag
        if environment_tag == "test":
            prefix = "gw_test_"
        elif environment_tag == "staging":
            prefix = "gw_staging_"
        elif environment_tag == "development":
            prefix = "gw_dev_"
        else:
            prefix = "gw_live_"

        # Generate random part (32 characters for security)
        random_part = secrets.token_urlsafe(32)
        api_key = prefix + random_part

        # Calculate expiration date if specified
        expiration_date = None
        if expiration_days:
            expiration_date = (datetime.now(UTC) + timedelta(days=expiration_days)).isoformat()

        # Set default permissions if none provided
        if scope_permissions is None:
            scope_permissions = {"read": ["*"], "write": ["*"], "admin": ["*"]}

        # Create the API key record with new fields
        # Set up trial for new users (if this is their first key)
        trial_data = {}
        if is_primary:
            trial_start = datetime.now(UTC)
            trial_end = trial_start + timedelta(days=TRIAL_DURATION_DAYS)
            # Use the provided subscription_status (e.g., "bot" for temp emails, "trial" for normal)
            is_trial = subscription_status == "trial"
            trial_data = {
                "is_trial": is_trial,
                "trial_start_date": trial_start.isoformat(),
                "trial_end_date": trial_end.isoformat(),
                "trial_used_tokens": 0,
                "trial_used_requests": 0,
                "trial_used_credits": 0.0,
                "trial_max_tokens": 100000 if is_trial else 0,
                "trial_max_requests": 1000 if is_trial else 0,
                "trial_credits": TRIAL_CREDITS_AMOUNT if is_trial else 0.0,
                "trial_converted": False,
                "subscription_status": subscription_status,
                "subscription_plan": "free_trial" if is_trial else "bot",
            }

        # Stored as key_hash (lookup) + encrypted_key (shown again to the owner)
        # + last4. The plaintext is never written. KEY_HASH_SALT and the
        # encryption keyring are both required: raises ValueError otherwise.
        storage_fields = new_key_storage_fields(api_key)

        # Combine base data with trial data
        base_api_key_data = {
            "user_id": user_id,
            "key_name": key_name,
            "is_active": True,
            "is_primary": is_primary,
            "expiration_date": expiration_date,
            "max_requests": max_requests,
            "requests_used": 0,
            "environment_tag": environment_tag,
            "scope_permissions": scope_permissions,
            "ip_allowlist": ip_allowlist or [],
            "domain_referrers": domain_referrers or [],
            "last_used_at": datetime.now(UTC).isoformat(),
        }

        if purpose is not None:
            base_api_key_data["purpose"] = purpose

        # Add trial data if this is a primary key
        base_api_key_data.update(trial_data)

        api_key_data = {**base_api_key_data, **storage_fields}

        try:
            result = _insert_api_key_row(client, api_key_data)
        except APIError as api_error:
            result = _handle_schema_cache_insert_error(client, api_key_data, api_error)

        # Safely get the created API key record
        try:
            api_key_record = safe_get_first(
                result, error_message="Failed to create API key", validate_keys=["id"]
            )
        except DatabaseResultError as e:
            raise ValueError(str(e))

        # Create rate limit configuration for the new key
        try:
            rate_limit_config = default_rate_limit_config(api_key_record["id"], max_requests)

            client.table("rate_limit_configs").insert(rate_limit_config).execute()

        except Exception as rate_limit_error:
            # Only log if it's NOT a missing table error (PGRST205)
            # Missing tables are expected until migration is applied - don't pollute logs
            if is_schema_cache_error(rate_limit_error):
                logger.debug(
                    "rate_limit_configs table not found - skipping rate limit config creation (migration pending)"
                )
            else:
                logger.warning(
                    "Failed to create rate limit config for API key id %s: %s",
                    api_key_record["id"],
                    safe_db_error(rate_limit_error),
                )

        # Create audit log entry
        try:
            client.table("api_key_audit_logs").insert(
                {
                    "user_id": user_id,
                    "action": "create",
                    "api_key_id": api_key_record["id"],
                    "details": {
                        "key_name": key_name,
                        "environment_tag": environment_tag,
                        "scope_permissions": scope_permissions,
                        "expiration_days": expiration_days,
                        "max_requests": max_requests,
                        "is_primary": is_primary,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ).execute()
        except Exception as audit_error:
            # Only log if it's NOT a missing table error (PGRST205)
            # Missing tables are expected until migration is applied - don't pollute logs
            if is_schema_cache_error(audit_error):
                logger.debug(
                    "api_key_audit_logs table not found - skipping audit log creation (migration pending)"
                )
            else:
                logger.warning(
                    "Failed to create audit log for API key id %s: %s",
                    api_key_record["id"],
                    safe_db_error(audit_error),
                )

        return api_key, api_key_record["id"]

    except Exception as e:
        # Never str() a PostgREST error here: its `details` carry the failing
        # row, which used to include the plaintext key.
        error_text = sanitize_for_logging(safe_db_error(e))
        logger.error("Failed to create API key: %s", error_text)
        raise RuntimeError(f"Failed to create API key: {error_text}") from e


def get_user_api_keys(user_id: int) -> list[dict[str, Any]]:
    """Get all API keys for a user"""
    try:
        client = get_supabase_client()

        # Query the new api_keys_new table
        result = (
            client.table("api_keys_new")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )

        if not result.data:
            return []

        keys = []
        for key in result.data:
            try:
                # Calculate days remaining
                days_remaining = None
                if key.get("expiration_date"):
                    try:
                        expiration_str = key["expiration_date"]
                        if expiration_str:
                            # Handle different datetime formats
                            if "Z" in expiration_str:
                                expiration_str = expiration_str.replace("Z", "+00:00")
                            elif expiration_str.endswith("+00:00"):
                                pass  # Already timezone-aware
                            else:
                                # Make naive datetime timezone-aware
                                expiration_str = expiration_str + "+00:00"

                            expiration = datetime.fromisoformat(expiration_str)
                            now = datetime.now(UTC).replace(tzinfo=expiration.tzinfo)
                            days_remaining = max(0, (expiration - now).days)
                    except Exception as date_error:
                        logger.warning(
                            f"Error calculating days remaining for key {key.get('id', 'unknown')}: {date_error}"
                        )
                        days_remaining = None

                # Calculate requests remaining
                requests_remaining = None
                if key.get("max_requests"):
                    requests_remaining = max(0, key["max_requests"] - key["requests_used"])

                # Calculate usage percentage (rounded)
                usage_percentage = None
                if key.get("max_requests") and key.get("requests_used"):
                    usage_percentage = _pct(key.get("requests_used", 0), key["max_requests"])

                key_data = {
                    "id": key["id"],
                    "key_name": key["key_name"],
                    "api_key": plaintext_from_row(key),
                    "environment_tag": key.get("environment_tag") or "live",
                    "scope_permissions": key.get("scope_permissions") or {},
                    "is_active": key["is_active"],
                    "is_primary": key.get("is_primary", False),
                    "expiration_date": key.get("expiration_date"),
                    "days_remaining": days_remaining,
                    "max_requests": key.get("max_requests"),
                    "requests_used": key.get("requests_used", 0),
                    "requests_remaining": requests_remaining,
                    "usage_percentage": usage_percentage,
                    "ip_allowlist": key.get("ip_allowlist") or [],
                    "domain_referrers": key.get("domain_referrers") or [],
                    "created_at": key.get("created_at"),
                    "updated_at": key.get("updated_at"),
                    "last_used_at": key.get("last_used_at"),
                    "purpose": key.get("purpose") or "general",
                }

                keys.append(key_data)

            except Exception as e:
                logger.error(
                    "Error processing API key %s: %s",
                    sanitize_for_logging(str(key.get("id", "unknown"))),
                    sanitize_for_logging(str(e)),
                )
                continue

        return keys

    except Exception as e:
        logger.error("Error getting user API keys: %s", sanitize_for_logging(str(e)))
        return []


def delete_api_key(api_key: str, user_id: int) -> bool:
    """Delete an API key for a user"""
    # Lazy import to avoid circular dependency (src.db.users imports this module)
    from src.db.users import invalidate_user_cache

    try:
        client = get_supabase_client()

        # Delete from the api_keys_new table (matched by key_hash)
        def _delete(column: str, value: str):
            return client.table("api_keys_new").delete().eq(column, value).eq("user_id", user_id)

        result = query_by_key(api_key, _delete, operation="delete")

        # Safely extract deleted key data
        try:
            deleted_key = safe_get_first(result, error_message="API key not found for deletion")
        except DatabaseResultError:
            return False

        # Also delete associated rate limit configs
        try:
            client.table("rate_limit_configs").delete().eq(
                "api_key_id", deleted_key["id"]
            ).execute()
        except Exception as e:
            # Only log if it's NOT a missing table error
            if not is_schema_cache_error(e):
                logger.warning(
                    "Failed to delete rate limit configs for key id %s: %s",
                    deleted_key["id"],
                    sanitize_for_logging(str(e)),
                )
            else:
                logger.debug("rate_limit_configs table not found - skipping deletion")

        # Create audit log entry
        try:
            client.table("api_key_audit_logs").insert(
                {
                    "user_id": user_id,
                    "action": "delete",
                    "api_key_id": deleted_key["id"],
                    "details": {
                        "deleted_at": datetime.now(UTC).isoformat(),
                        "key_name": deleted_key.get("key_name", "Unknown"),
                        "environment_tag": deleted_key.get("environment_tag", "unknown"),
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ).execute()
        except Exception as e:
            # Only log if it's NOT a missing table error
            if not is_schema_cache_error(e):
                logger.warning(
                    "Failed to create audit log for key deletion: %s",
                    sanitize_for_logging(str(e)),
                )
            else:
                logger.debug("api_key_audit_logs table not found - skipping audit log")

        # A deleted key must stop authenticating immediately, not up to
        # _user_cache_ttl seconds later -- see the is_active revocation fix
        # in _get_user_uncached/get_user (src/db/users.py).
        invalidate_user_cache(api_key)

        return True

    except Exception as e:
        logger.error("Failed to delete API key: %s", sanitize_for_logging(str(e)))
        return False


def get_api_key_by_hash(key_hash: str) -> dict[str, Any] | None:
    """Look up an API key record by its SHA-256 hash (constant-time, no decryption needed).

    Args:
        key_hash: The hex-encoded SHA-256 hash of the API key.

    Returns:
        Dictionary with API key data if found, None otherwise.
    """

    def _fetch(client):
        result = client.table("api_keys_new").select("*").eq("key_hash", key_hash).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    try:
        return execute_with_retry(
            _fetch, max_retries=2, retry_delay=0.2, operation_name="get_api_key_by_hash"
        )
    except Exception as e:
        logger.error(
            "Error getting API key by hash: %s",
            sanitize_for_logging(str(e)),
        )
        return None


def validate_api_key(api_key: str) -> dict[str, Any] | None:
    """Validate an API key and return user info if valid"""
    # Lazy import to avoid circular dependency
    from src.db.users import get_user

    try:
        client = get_supabase_client()

        # Check if key exists in api_keys_new table
        try:
            # Hash lookup; plaintext fallback only behind API_KEY_PLAINTEXT_FALLBACK
            key_data = find_key_row(client, api_key)

            if key_data:

                # Check if key is active
                if not key_data.get("is_active", True):
                    logger.warning("API key %s is inactive", mask_api_key_for_logging(api_key))
                    return None

                # Check expiration date
                if key_data.get("expiration_date"):
                    try:
                        expiration_str = key_data["expiration_date"]
                        if expiration_str:
                            if "Z" in expiration_str:
                                expiration_str = expiration_str.replace("Z", "+00:00")
                            elif not expiration_str.endswith("+00:00"):
                                expiration_str = expiration_str + "+00:00"

                            expiration = datetime.fromisoformat(expiration_str)
                            now = datetime.now(UTC).replace(tzinfo=expiration.tzinfo)

                            if expiration < now:
                                logger.warning(
                                    "API key %s has expired",
                                    mask_api_key_for_logging(api_key),
                                )
                                return None
                    except Exception as date_error:
                        logger.warning(
                            "Error checking expiration for key %s: %s",
                            mask_api_key_for_logging(api_key),
                            sanitize_for_logging(str(date_error)),
                        )

                # EARLY TRIAL EXPIRATION CHECK: Reject expired trials immediately
                # This prevents unnecessary database queries for bot requests with expired trial keys
                if key_data.get("is_trial", False) and key_data.get("trial_end_date"):
                    try:
                        trial_end_str = key_data["trial_end_date"]
                        if trial_end_str:
                            # Parse trial end date
                            if "Z" in trial_end_str:
                                trial_end_str = trial_end_str.replace("Z", "+00:00")
                            elif "+" not in trial_end_str and "-" not in trial_end_str[-6:]:
                                trial_end_str = trial_end_str + "+00:00"

                            trial_end = datetime.fromisoformat(trial_end_str)
                            now = datetime.now(UTC)
                            if trial_end.tzinfo is None:
                                trial_end = trial_end.replace(tzinfo=UTC)

                            if trial_end < now:
                                logger.warning(
                                    "Trial expired for API key %s (expired: %s)",
                                    mask_api_key_for_logging(api_key),
                                    trial_end_str,
                                )
                                return None
                    except Exception as trial_date_error:
                        logger.warning(
                            "Error checking trial expiration for key %s: %s",
                            mask_api_key_for_logging(api_key),
                            sanitize_for_logging(str(trial_date_error)),
                        )

                # Check request limits
                if key_data.get("max_requests"):
                    if key_data["requests_used"] >= key_data["max_requests"]:
                        logger.warning(
                            "API key %s has reached request limit",
                            mask_api_key_for_logging(api_key),
                        )
                        return None

                # Get user info
                user = get_user(api_key)
                if user:
                    return {
                        "user_id": user["id"],
                        "api_key": api_key,
                        "key_id": key_data["id"],
                        "key_name": key_data["key_name"],
                        "is_active": key_data["is_active"],
                        "expiration_date": key_data["expiration_date"],
                        "max_requests": key_data["max_requests"],
                        "requests_used": key_data["requests_used"],
                    }

        except Exception as e:
            logger.warning(
                "API key validation failed: %s",
                sanitize_for_logging(str(e)),
            )

        # Fallback: Check if key exists in the users table (for backward compatibility)
        user = get_user(api_key)
        if user:
            # This is a legacy key - return legacy key info
            logger.info(
                "Using legacy key for user %s",
                sanitize_for_logging(str(user["id"])),
            )
            return {
                "user_id": user["id"],
                "api_key": api_key,
                "key_id": 0,  # Legacy key
                "key_name": "Legacy Key",
                "is_active": True,
                "expiration_date": None,
                "max_requests": None,
                "requests_used": 0,
            }

        return None

    except Exception as e:
        logger.error("Failed to validate API key: %s", sanitize_for_logging(str(e)))
        return None


def increment_api_key_usage(api_key: str) -> None:
    """Increment the request count for an API key"""
    # Lazy import to avoid circular dependency

    try:
        client = get_supabase_client()

        # Try to increment in the new system first (api_keys_new table)
        try:
            # Check if key exists in api_keys_new table
            existing_key = find_key_row(client, api_key, "id, requests_used")

            if existing_key:
                # Update existing entry in new system
                current_usage = existing_key.get("requests_used") or 0
                client.table("api_keys_new").update(
                    {
                        "requests_used": current_usage + 1,
                        "last_used_at": datetime.now(UTC).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ).eq("id", existing_key["id"]).execute()
                return

        except Exception as e:
            logger.warning(
                "Failed to update usage in api_keys_new table: %s", sanitize_for_logging(str(e))
            )

    except Exception as e:
        logger.error("Failed to increment API key usage: %s", sanitize_for_logging(str(e)))


def get_api_key_usage_stats(api_key: str) -> dict[str, Any]:
    """Get usage statistics for a specific API key"""
    try:
        client = get_supabase_client()

        # Query the api_keys_new table
        key_data = find_key_row(client, api_key)

        if not key_data:
            logger.warning(
                "API key not found in api_keys_new table: %s",
                mask_api_key_for_logging(api_key),
            )
            return {
                "api_key": api_key,
                "key_name": "Unknown",
                "is_active": False,
                "requests_used": 0,
                "max_requests": None,
                "requests_remaining": None,
                "usage_percentage": None,
                "environment_tag": "unknown",
                "created_at": None,
                "last_used_at": None,
            }

        # Calculate requests remaining and usage percentage
        requests_remaining = None
        usage_percentage = None

        if key_data.get("max_requests"):
            requests_remaining = max(0, key_data["max_requests"] - key_data.get("requests_used", 0))
            usage_percentage = _pct(key_data.get("requests_used", 0), key_data["max_requests"])

        return {
            "api_key": api_key,
            "key_name": key_data.get("key_name", "Unnamed Key"),
            "is_active": key_data.get("is_active", False),
            "requests_used": key_data.get("requests_used", 0),
            "max_requests": key_data.get("max_requests"),
            "requests_remaining": requests_remaining,
            "usage_percentage": usage_percentage,
            "environment_tag": key_data.get("environment_tag", "live"),
            "created_at": key_data.get("created_at"),
            "last_used_at": key_data.get("last_used_at"),
        }

    except Exception as e:
        logger.error(
            "Error getting API key usage stats for %s: %s",
            mask_api_key_for_logging(api_key),
            sanitize_for_logging(str(e)),
        )
        return {
            "api_key": api_key,
            "key_name": "Error",
            "is_active": False,
            "requests_used": 0,
            "max_requests": None,
            "requests_remaining": None,
            "usage_percentage": None,
            "environment_tag": "error",
            "created_at": None,
            "last_used_at": None,
        }


def update_api_key(api_key: str, user_id: int, updates: dict[str, Any]) -> bool:
    """Update an API key's details"""
    # Lazy import to avoid circular dependency (src.db.users imports this module)
    from src.db.users import invalidate_user_cache

    try:
        client = get_supabase_client()

        # First, get the key to verify ownership and get key_id
        key_data = find_key_row(client, api_key, filters={"user_id": user_id})
        if not key_data or "id" not in key_data:
            raise ValueError("API key not found or not owned by user")

        key_id = key_data["id"]

        # Prepare update data
        update_data = {}
        allowed_fields = [
            "key_name",
            "scope_permissions",
            "expiration_days",
            "max_requests",
            "ip_allowlist",
            "domain_referrers",
            "is_active",
            "purpose",
        ]

        for field, value in updates.items():
            if field in allowed_fields:
                update_data[field] = value

        # Special handling for key_name uniqueness
        if "key_name" in update_data:
            if not check_key_name_uniqueness(
                user_id, update_data["key_name"], exclude_key_id=key_id
            ):
                raise ValueError(
                    f"Key name '{update_data['key_name']}' already exists for this user. Please choose a different name."
                )

        # Special handling for expiration_days
        if "expiration_days" in update_data:
            if update_data["expiration_days"] is not None:
                update_data["expiration_date"] = (
                    datetime.now(UTC) + timedelta(days=update_data["expiration_days"])
                ).isoformat()
            else:
                update_data["expiration_date"] = None
            # Remove the days field as we store the actual date
            del update_data["expiration_days"]

        # Add timestamp
        update_data["updated_at"] = datetime.now(UTC).isoformat()

        # Update the API key
        result = client.table("api_keys_new").update(update_data).eq("id", key_id).execute()

        # Validate the update succeeded
        try:
            safe_get_first(result, error_message="Failed to update API key")
        except DatabaseResultError as e:
            raise ValueError(str(e))

        # Update rate limit config if max_requests changed
        if "max_requests" in updates and updates["max_requests"] is not None:
            try:
                client.table("rate_limit_configs").update(
                    {
                        "max_requests": updates["max_requests"],
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ).eq("api_key_id", key_id).execute()
            except Exception as e:
                # Only log if it's NOT a missing table error
                if not is_schema_cache_error(e):
                    logger.warning(
                        "Failed to update rate limit config: %s", sanitize_for_logging(str(e))
                    )
                else:
                    logger.debug("rate_limit_configs table not found - skipping rate limit update")

        # Create audit log entry
        try:
            client.table("api_key_audit_logs").insert(
                {
                    "user_id": user_id,
                    "action": "update",
                    "api_key_id": key_id,
                    "details": {
                        "updated_fields": list(updates.keys()),
                        "old_values": {k: key_data.get(k) for k in updates.keys() if k in key_data},
                        "new_values": updates,
                        "update_timestamp": datetime.now(UTC).isoformat(),
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ).execute()
        except Exception as e:
            # Only log if it's NOT a missing table error
            if not is_schema_cache_error(e):
                logger.warning(
                    "Failed to create audit log for key update: %s", sanitize_for_logging(str(e))
                )
            else:
                logger.debug("api_key_audit_logs table not found - skipping audit log")

        # is_active flips (deactivation, or reactivation) must be reflected
        # immediately, not up to _user_cache_ttl seconds later -- same
        # revocation-must-be-real fix as delete_api_key.
        # Same for a purpose flip: turning validator (no-logging) mode on must
        # not keep logging this key from a cached user dict.
        if "is_active" in update_data or "purpose" in update_data:
            invalidate_user_cache(api_key)

        return True

    except Exception as e:
        logger.error("Failed to update API key: %s", sanitize_for_logging(str(e)))
        raise RuntimeError(f"Failed to update API key: {e}") from e


def validate_api_key_permissions(api_key: str, required_permission: str, resource: str) -> bool:
    """Validate if an API key has the required permission for a resource"""
    try:
        logger.info(
            "Validating permissions for API key %s - Required: %s on %s",
            mask_api_key_for_logging(api_key),
            sanitize_for_logging(required_permission),
            sanitize_for_logging(resource),
        )

        # Temporary session keys (gw_temp_*) have full permissions
        if api_key.startswith("gw_temp_"):
            logger.info(
                "Granting full permissions to session key: %s",
                mask_api_key_for_logging(api_key),
            )
            return True

        client = get_supabase_client()

        # Get the API key record with is_primary flag
        key_data = find_key_row(client, api_key, "scope_permissions, is_active, is_primary")

        if key_data:
            logger.info(
                "Found in api_keys_new - is_active: %s, is_primary: %s",
                key_data.get("is_active"),
                key_data.get("is_primary", False),
            )
        else:
            logger.warning(
                "API key not found in api_keys_new table: %s",
                mask_api_key_for_logging(api_key),
            )
            return False

        # Check if key is active
        if not key_data.get("is_active", True):
            logger.warning("API key is inactive: %s", mask_api_key_for_logging(api_key))
            return False

        # Primary keys (auto-generated for new users) have full permissions
        # This is the most important check - primary keys should ALWAYS have full access
        if key_data.get("is_primary", False):
            logger.info(
                "Granting full permissions to primary key: %s",
                mask_api_key_for_logging(api_key),
            )
            return True
        else:
            logger.info(
                "Not a primary key, checking scope_permissions: %s",
                sanitize_for_logging(str(key_data.get("scope_permissions", {}))),
            )

        # Get scope permissions
        scope_permissions = key_data.get("scope_permissions", {})

        # If no permissions set, grant default access (for backward compatibility)
        if not scope_permissions or scope_permissions == {}:
            logger.info(
                "No scope permissions set, granting default access for %s",
                mask_api_key_for_logging(api_key),
            )
            return True

        # Check if the required permission exists
        if required_permission in scope_permissions:
            # Check if the resource is in the allowed list for this permission
            allowed_resources = scope_permissions[required_permission]
            if isinstance(allowed_resources, list):
                # Check if resource is in the allowed list or if wildcard (*) is allowed
                has_permission = "*" in allowed_resources or resource in allowed_resources
                logger.info(
                    "Permission check result for %s: %s",
                    mask_api_key_for_logging(api_key),
                    has_permission,
                )
                return has_permission
            elif isinstance(allowed_resources, str):
                # Single resource or wildcard
                has_permission = allowed_resources == "*" or allowed_resources == resource
                logger.info(
                    "Permission check result for %s: %s",
                    mask_api_key_for_logging(api_key),
                    has_permission,
                )
                return has_permission

        logger.warning(
            "Permission denied for %s - required: %s on %s, available: %s",
            mask_api_key_for_logging(api_key),
            sanitize_for_logging(required_permission),
            sanitize_for_logging(resource),
            sanitize_for_logging(str(scope_permissions)),
        )
        return False

    except Exception as e:
        logger.error("Error validating API key permissions: %s", sanitize_for_logging(str(e)))
        return False


def get_api_key_by_id(key_id: int, user_id: int) -> dict[str, Any] | None:
    """Get API key details by ID, ensuring user ownership"""
    try:
        client = get_supabase_client()

        result = (
            client.table("api_keys_new")
            .select("*")
            .eq("id", key_id)
            .eq("user_id", user_id)
            .execute()
        )

        if not result.data:
            return None

        key_data = result.data[0]

        # Calculate days remaining
        days_remaining = None
        if key_data.get("expiration_date"):
            try:
                expiration_str = key_data["expiration_date"]
                if expiration_str:
                    # Handle different datetime formats
                    if "Z" in expiration_str:
                        expiration_str = expiration_str.replace("Z", "+00:00")
                    elif expiration_str.endswith("+00:00"):
                        pass  # Already timezone-aware
                    else:
                        # Make naive datetime timezone-aware
                        expiration_str = expiration_str + "+00:00"

                    expiration = datetime.fromisoformat(expiration_str)
                    now = datetime.now(UTC).replace(tzinfo=expiration.tzinfo)
                    days_remaining = max(0, (expiration - now).days)
            except Exception as date_error:
                logger.warning(
                    "Error calculating days remaining for key %s: %s",
                    sanitize_for_logging(str(key_id)),
                    sanitize_for_logging(str(date_error)),
                )
                days_remaining = None

        # Calculate requests remaining
        requests_remaining = None
        if key_data.get("max_requests"):
            requests_remaining = max(0, key_data["max_requests"] - key_data.get("requests_used", 0))

        # Calculate usage percentage (rounded)
        usage_percentage = None
        if key_data.get("max_requests") and key_data.get("requests_used"):
            usage_percentage = _pct(key_data.get("requests_used", 0), key_data["max_requests"])

        return {
            "id": key_data["id"],
            "key_name": key_data["key_name"],
            "api_key": plaintext_from_row(key_data),
            "environment_tag": key_data.get("environment_tag") or "live",
            "scope_permissions": key_data.get("scope_permissions") or {},
            "is_active": key_data["is_active"],
            "is_primary": key_data.get("is_primary", False),
            "expiration_date": key_data.get("expiration_date"),
            "days_remaining": days_remaining,
            "max_requests": key_data.get("max_requests"),
            "requests_used": key_data.get("requests_used", 0),
            "requests_remaining": requests_remaining,
            "usage_percentage": usage_percentage,
            "ip_allowlist": key_data.get("ip_allowlist") or [],
            "domain_referrers": key_data.get("domain_referrers") or [],
            "created_at": key_data.get("created_at"),
            "updated_at": key_data.get("updated_at"),
            "last_used_at": key_data.get("last_used_at"),
            "purpose": key_data.get("purpose") or "general",
        }

    except Exception as e:
        logger.error("Error getting API key by ID: %s", sanitize_for_logging(str(e)))
        return None


def get_arrivals_by_key(user_id: int) -> dict[int, dict[str, Any]] | None:
    """Per-key arrivals and failures from the request ledger, keyed by api_key_id.

    Returns None on failure rather than {} so the caller can tell "nothing
    arrived" from "the rollup did not run". Rendering those the same way is how
    an unread counter becomes a confident zero -- the failure mode this whole
    feature exists to remove.
    """
    try:
        client = get_supabase_client()
        result = client.rpc("gatewayz_arrivals_by_key", {"p_user_id": user_id}).execute()
        return {row["api_key_id"]: row for row in (result.data or []) if row.get("api_key_id")}
    except Exception as e:
        logger.error(
            "arrivals-by-key rollup failed for user %s: %s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(str(e)),
        )
        return None


def get_user_all_api_keys_usage(user_id: int) -> dict[str, Any]:
    """Get usage statistics for all API keys of a user.

    Each key carries TWO different counts, because they answer two different
    questions and conflating them cost a day of a partner integration:

      requests_used  -- what consumed the key's CAP. A rejected call does not.
      arrivals       -- what actually REACHED the key, failures included.

    `arrivals` / `failed` come from the request ledger via
    gatewayz_arrivals_by_key. When that read fails they are OMITTED and
    `arrivals_measured` is false -- never reported as 0, which would be the
    same lie in a new place.
    """
    try:
        client = get_supabase_client()

        # Get all API keys for the user
        keys_result = client.table("api_keys_new").select("*").eq("user_id", user_id).execute()

        if not keys_result.data:
            return {"user_id": user_id, "total_keys": 0, "keys": [], "arrivals_measured": True}

        arrivals = get_arrivals_by_key(user_id)
        measured = arrivals is not None

        keys_usage = []
        for key_data in keys_result.data:
            # Calculate requests remaining and usage percentage
            requests_remaining = None
            usage_percentage = None

            if key_data.get("max_requests"):
                requests_remaining = max(
                    0, key_data["max_requests"] - key_data.get("requests_used", 0)
                )
                usage_percentage = _pct(key_data.get("requests_used", 0), key_data["max_requests"])

            key_usage = {
                "key_id": key_data["id"],
                "api_key": key_label(key_data),  # Non-secret label, e.g. "...a1b2"
                "key_name": key_data.get("key_name", "Unnamed Key"),
                "is_active": key_data.get("is_active", False),
                "is_primary": key_data.get("is_primary", False),
                "requests_used": key_data.get("requests_used", 0),
                "max_requests": key_data.get("max_requests"),
                "requests_remaining": requests_remaining,
                "usage_percentage": usage_percentage,
                "environment_tag": key_data.get("environment_tag", "live"),
                "created_at": key_data.get("created_at"),
                "last_used_at": key_data.get("last_used_at"),
            }

            # Arrivals sit beside requests_used rather than replacing it. The cap
            # counter is correct for what it meters; it is simply not an answer to
            # "did anything reach this key?". A caller that only ever fails leaves
            # requests_used frozen forever.
            if measured:
                row = arrivals.get(key_data["id"]) or {}
                key_usage["arrivals"] = int(row.get("arrivals") or 0)
                key_usage["failed"] = int(row.get("failed") or 0)
                key_usage["first_arrival_at"] = row.get("first_arrival_at")
                key_usage["last_arrival_at"] = row.get("last_arrival_at")
                key_usage["last_failure_at"] = row.get("last_failure_at")

            keys_usage.append(key_usage)

        return {
            "user_id": user_id,
            "total_keys": len(keys_usage),
            "keys": keys_usage,
            # False means the arrivals rollup could not be read, and the
            # arrivals/failed fields are ABSENT rather than zero. UNREACHABLE IS
            # NOT ABSENT.
            "arrivals_measured": measured,
        }

    except Exception as e:
        logger.error(
            "Error getting all API keys usage for user %s: %s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(str(e)),
        )
        return None


def get_api_key_by_key(api_key: str) -> dict[str, Any] | None:
    """
    Get API key data by the API key value (for admin use).

    Args:
        api_key: The API key string to look up

    Returns:
        Dictionary with API key data if found, None otherwise
    """

    def _fetch_key(client):
        # Hash lookup; plaintext fallback only behind API_KEY_PLAINTEXT_FALLBACK
        return find_key_row(client, api_key)

    try:
        # Use execute_with_retry to handle HTTP/2 connection errors
        return execute_with_retry(
            _fetch_key, max_retries=2, retry_delay=0.2, operation_name="get_api_key_by_key"
        )

    except Exception as e:
        logger.error(
            "Error getting API key by value: %s",
            sanitize_for_logging(str(e)),
        )
        return None
