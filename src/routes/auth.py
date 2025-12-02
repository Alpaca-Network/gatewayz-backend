import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from postgrest import APIError

import src.config.supabase_config as supabase_config
import src.db.users as users_module
import src.enhanced_notification_service as notif_module
from src.db.activity import log_activity
from src.schemas import (
    AuthMethod,
    PrivyAuthRequest,
    PrivyAuthResponse,
    SubscriptionStatus,
    UserRegistrationRequest,
    UserRegistrationResponse,
)
from src.services.auth_cache import (
    cache_user_by_privy_id,
    cache_user_by_username,
    get_cached_user_by_privy_id,
    get_cached_user_by_username,
    invalidate_user_cache,
)
from src.services.query_timeout import (
    AUTH_QUERY_TIMEOUT,
    USER_LOOKUP_TIMEOUT,
    QueryTimeoutError,
    safe_query_with_timeout,
)
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_account_email(account) -> str | None:
    """Return the first valid email value exposed by a Privy linked account."""
    possible_values = [
        getattr(account, "email", None),
        getattr(account, "address", None),
    ]
    for value in possible_values:
        if value and "@" in value:
            return value
    return None


def _generate_unique_username(client, base_username: str) -> str:
    """Generate a username that does not conflict with existing records."""
    sanitized = base_username or "user"
    candidate = sanitized
    attempts = 0

    while attempts < 5:
        existing = (
            client.table("users").select("id").eq("username", candidate).limit(1).execute()
        )
        if not existing.data:
            return candidate

        attempts += 1
        token = secrets.token_hex(2)
        candidate = f"{sanitized}_{token}"

    # Last resort: append a longer random suffix
    return f"{sanitized}_{secrets.token_hex(4)}"


def _get_tier_display_name(tier: str | None) -> str | None:
    """Return a user-friendly display name for a subscription tier."""
    tier_display_map = {"basic": "Basic", "pro": "Pro", "max": "MAX"}
    return tier_display_map.get(tier) if tier else None


def _handle_existing_user(
    existing_user: dict[str, Any],
    request: PrivyAuthRequest,
    background_tasks: BackgroundTasks,
    auth_method: AuthMethod,
    display_name: str | None,
    email: str | None,
    auto_create_api_key: bool = True,
) -> PrivyAuthResponse:
    """Build a consistent response for existing users."""
    logger.info(f"Existing Privy user found: {existing_user['id']}")
    logger.info(
        f"User welcome email status: {existing_user.get('welcome_email_sent', 'Not set')}"
    )
    logger.info(
        "User credits at login: %s (type: %s)",
        existing_user.get("credits", "NOT_FOUND"),
        type(existing_user.get("credits")).__name__,
    )

    client = supabase_config.get_supabase_client()
    api_key_to_return = existing_user.get("api_key")

    try:
        # Fetch API keys with timeout protection
        def fetch_api_keys():
            return (
                client.table("api_keys_new")
                .select("api_key, is_primary, created_at")
                .eq("user_id", existing_user["id"])
                .eq("is_active", True)
                .order("is_primary", desc=True)
                .order("created_at", desc=False)
                .execute()
            )

        all_keys_result = safe_query_with_timeout(
            client,
            "api_keys_new",
            fetch_api_keys,
            timeout_seconds=AUTH_QUERY_TIMEOUT,
            operation_name=f"fetch API keys for user {existing_user['id']}",
            fallback_value=None,
            log_errors=True,
        )

        if all_keys_result and all_keys_result.data:
            sorted_keys = sorted(
                all_keys_result.data,
                key=lambda k: (not k.get("is_primary", False), k.get("created_at", "")),
            )
            api_key_to_return = sorted_keys[0]["api_key"]
            key_type = "primary" if sorted_keys[0].get("is_primary") else "active"
            logger.info(
                "Returning %s API key for user %s from %s active keys",
                key_type,
                existing_user["id"],
                len(sorted_keys),
            )
        else:
            logger.warning(
                "No API keys found in api_keys_new for user %s, using legacy key",
                existing_user["id"],
            )
    except QueryTimeoutError as key_error:
        logger.error(
            "Timeout retrieving API keys for user %s: %s, falling back to legacy key",
            existing_user["id"],
            key_error,
        )
    except Exception as key_error:
        logger.error(
            "Error retrieving API keys for user %s: %s, falling back to legacy key",
            existing_user["id"],
            key_error,
        )

    # Validate that we're not returning a temporary key pattern
    # Temporary keys created during user registration: gw_live_{token_urlsafe(16)}
    #   - Format: "gw_live_" (8 chars) + token_urlsafe(16) (22 chars) = 30 chars total
    # Proper keys from create_api_key: gw_live_{token_urlsafe(32)}
    #   - Format: "gw_live_" (8 chars) + token_urlsafe(32) (43 chars) = 51 chars total
    # Note: token_urlsafe() can contain underscores, so we check total length, not split by _
    if api_key_to_return and api_key_to_return.startswith("gw_live_"):
        key_length = len(api_key_to_return)
        # Threshold: midpoint between 30 (temp) and 51 (proper) = 40
        # Any gw_live_ key shorter than 40 chars is a temp key
        if key_length < 40:
            logger.error(
                "Detected temporary API key pattern for user %s: %s (length: %d). "
                "This key should have been replaced during user creation.",
                existing_user["id"],
                api_key_to_return[:15] + "...",
                key_length,
            )
            # Don't return temporary keys - force key recreation by setting to None
            api_key_to_return = None
            logger.warning(
                "Nullified temporary key for user %s to force proper key recreation",
                existing_user["id"],
            )

    user_credits = existing_user.get("credits")
    try:
        if user_credits is None:
            logger.warning("User %s has None/null credits, defaulting to 0.0", existing_user["id"])
            user_credits = 0.0
        else:
            user_credits = float(user_credits)
            logger.debug(
                "Normalized user %s credits to float: %s", existing_user["id"], user_credits
            )
    except (ValueError, TypeError) as credits_error:
        logger.error(
            "Failed to convert credits for user %s (value: %s, type: %s): %s, defaulting to 0.0",
            existing_user["id"],
            user_credits,
            type(user_credits).__name__,
            credits_error,
        )
        user_credits = 0.0

    tier = existing_user.get("tier")
    tier_display_name = _get_tier_display_name(tier)
    subscription_status_value = existing_user.get("subscription_status")
    trial_expires_at = existing_user.get("trial_expires_at")
    subscription_end_date = existing_user.get("subscription_end_date")

    user_email = existing_user.get("email") or email
    logger.info(
        "Welcome email check - User ID: %s, Welcome sent: %s",
        existing_user["id"],
        existing_user.get("welcome_email_sent", "Not set"),
    )

    if user_email:
        background_tasks.add_task(
            _send_welcome_email_background,
            user_id=existing_user["id"],
            username=existing_user.get("username") or display_name,
            email=user_email,
            credits=user_credits,
        )
    else:
        logger.warning("No email found for user %s, skipping welcome email", existing_user["id"])

    background_tasks.add_task(
        _log_auth_activity_background,
        user_id=existing_user["id"],
        auth_method=auth_method,
        privy_user_id=request.user.id,
        is_new_user=False,
    )

    # Handle case where no valid API key exists
    if not api_key_to_return:
        if auto_create_api_key:
            logger.info(
                "No valid API key found for user %s, creating new primary key (auto_create_api_key=True)",
                existing_user["id"],
            )
            try:
                from src.db.api_keys import create_api_key

                # Create a new primary key for this user
                primary_key, _ = create_api_key(
                    user_id=existing_user["id"],
                    key_name="Primary Key (Auto-created)",
                    environment_tag="live",
                    is_primary=True,
                )
                api_key_to_return = primary_key

                # Update the users table with the new key
                try:
                    client.table("users").update({"api_key": primary_key}).eq(
                        "id", existing_user["id"]
                    ).execute()
                    logger.info(
                        "Successfully created and set new primary key for user %s",
                        existing_user["id"],
                    )
                except Exception as update_error:
                    logger.warning(
                        "Failed to update users.api_key for user %s: %s",
                        existing_user["id"],
                        update_error,
                    )
            except Exception as create_error:
                logger.error(
                    "Failed to create new API key for user %s: %s",
                    existing_user["id"],
                    create_error,
                )
                # Continue without a key - frontend will need to handle this
        else:
            logger.warning(
                "No valid API key found for user %s and auto_create_api_key=False, returning None",
                existing_user["id"],
            )
            # Return None as api_key - frontend should handle this case

    logger.info("Returning login response with credits: %s", user_credits)

    return PrivyAuthResponse(
        success=True,
        message="Login successful",
        user_id=existing_user["id"],
        api_key=api_key_to_return,
        auth_method=auth_method,
        privy_user_id=request.user.id,
        is_new_user=False,
        display_name=existing_user.get("username") or display_name,
        email=user_email,
        credits=user_credits,
        timestamp=datetime.now(timezone.utc),
        subscription_status=subscription_status_value,
        tier=tier,
        tier_display_name=tier_display_name,
        trial_expires_at=trial_expires_at,
        subscription_end_date=subscription_end_date,
    )


# Background task functions for non-blocking operations
# ISSUE FIX #6: Improved background task error handling with better logging


def _send_welcome_email_background(user_id: str, username: str, email: str, credits: float):
    """Send welcome email in background for existing users"""
    try:
        logger.info(f"Background task: Sending welcome email to user {user_id}")
        if not email or "@" not in email:
            logger.warning(f"Background task: Invalid email '{email}' for user {user_id}, skipping")
            return

        success = notif_module.enhanced_notification_service.send_welcome_email_if_needed(
            user_id=user_id, username=username, email=email, credits=credits
        )
        if success:
            logger.info(f"Background task: Welcome email sent successfully to user {user_id}")
        else:
            logger.warning(
                f"Background task: Welcome email service returned false for user {user_id}"
            )
    except Exception as e:
        logger.error(
            f"Background task: Failed to send welcome email to existing user {user_id}: {e}",
            exc_info=True,
        )


def _send_new_user_welcome_email_background(
    user_id: str, username: str, email: str, credits: float
):
    """Send welcome email in background for new users"""
    try:
        logger.info(f"Background task: Sending welcome email to new user {user_id}")
        if not email or "@" not in email:
            logger.warning(
                f"Background task: Invalid email '{email}' for new user {user_id}, skipping"
            )
            return

        success = notif_module.enhanced_notification_service.send_welcome_email(
            user_id=user_id, username=username, email=email, credits=credits
        )
        if success:
            try:
                from src.db.users import mark_welcome_email_sent

                mark_welcome_email_sent(user_id)
                logger.info(
                    f"Background task: Welcome email sent and marked for new user {user_id}"
                )
            except Exception as mark_error:
                logger.error(
                    f"Background task: Failed to mark welcome email as sent for user {user_id}: "
                    f"{mark_error}"
                )
        else:
            logger.warning(
                f"Background task: Welcome email service returned false for new user {user_id}"
            )
    except Exception as e:
        logger.error(
            f"Background task: Failed to send welcome email for new user {user_id}: {e}",
            exc_info=True,
        )


def _log_auth_activity_background(
    user_id: str, auth_method: AuthMethod, privy_user_id: str, is_new_user: bool
):
    """Log authentication activity in background"""
    try:
        # Convert user_id to int if it's a string
        try:
            user_id_int = int(user_id) if isinstance(user_id, str) else user_id
        except (ValueError, TypeError) as conv_error:
            logger.error(
                f"Background task: Failed to convert user_id '{user_id}' to int: {conv_error}"
            )
            return

        log_activity(
            user_id=user_id_int,
            model="auth",
            provider="Privy",
            tokens=0,
            cost=0.0,
            speed=0.0,
            finish_reason="login",
            app="Auth",
            metadata={
                "action": "login",
                "auth_method": (
                    auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                ),
                "privy_user_id": privy_user_id,
                "is_new_user": is_new_user,
            },
        )
        logger.debug(f"Background task: Auth activity logged for user {user_id}")
    except Exception as e:
        logger.error(
            f"Background task: Failed to log auth activity for user {user_id}: {e}",
            exc_info=True,
        )


def _log_registration_activity_background(user_id: str, metadata: dict):
    """Log registration activity in background"""
    try:
        # Convert user_id to int if it's a string
        try:
            user_id_int = int(user_id) if isinstance(user_id, str) else user_id
        except (ValueError, TypeError) as conv_error:
            logger.error(
                f"Background task: Failed to convert user_id '{user_id}' to int: {conv_error}"
            )
            return

        log_activity(
            user_id=user_id_int,
            model="auth",
            provider="Privy",
            tokens=0,
            cost=0.0,
            speed=0.0,
            finish_reason="register",
            app="Auth",
            metadata=metadata,
        )
        logger.debug(f"Background task: Registration activity logged for user {user_id}")
    except Exception as e:
        logger.error(
            f"Background task: Failed to log registration activity for user {user_id}: {e}",
            exc_info=True,
        )


def _process_referral_code_background(
    referral_code: str, user_id: str, username: str, is_new_user: bool = True
):
    """Process referral code in background to avoid blocking auth response.

    OPTIMIZATION: Moved from main auth flow to background task to reduce
    latency on auth endpoint. Referral tracking is non-critical for login success.
    """
    try:
        logger.info(f"Background task: Processing referral code '{referral_code}' for user {user_id}")

        from src.services.referral import (
            send_referral_signup_notification,
            track_referral_signup,
        )

        # Track referral signup and get referrer info
        success, error_msg, referrer = track_referral_signup(referral_code, user_id)

        if success and referrer:
            logger.info(
                f"Background task: Valid referral code processed: {referral_code} "
                f"for user {user_id}"
            )

            try:
                # Store referral code for the user
                client = supabase_config.get_supabase_client()
                client.table("users").update(
                    {"referred_by_code": referral_code}
                ).eq("id", user_id).execute()

                logger.info(
                    f"Background task: Stored referral code {referral_code} "
                    f"for user {user_id}"
                )

                # Send notification to referrer
                if referrer.get("email"):
                    try:
                        send_referral_signup_notification(
                            referrer_id=referrer["id"],
                            referrer_email=referrer["email"],
                            referrer_username=referrer.get("username", "User"),
                            referee_username=username,
                        )
                        logger.info(
                            f"Background task: Referral notification sent to referrer "
                            f"{referrer['id']}"
                        )
                    except Exception as notify_error:
                        logger.error(
                            f"Background task: Failed to send referral notification: {notify_error}"
                        )
            except Exception as store_error:
                logger.error(
                    f"Background task: Failed to store referral code: {store_error}"
                )
        else:
            logger.warning(
                f"Background task: Invalid referral code provided: {referral_code} - {error_msg}"
            )
    except Exception as e:
        logger.error(
            f"Background task: Error processing referral code '{referral_code}' "
            f"for user {user_id}: {e}",
            exc_info=True,
        )


@router.post("/auth", response_model=PrivyAuthResponse, tags=["authentication"])
async def privy_auth(request: PrivyAuthRequest, background_tasks: BackgroundTasks):
    """Authenticate user via Privy and return API key"""
    try:
        logger.info(f"Privy auth request for user: {request.user.id}")
        if request.referral_code:
            logger.info(f"Referral code provided in auth request: {request.referral_code}")
        logger.info(f"is_new_user flag: {request.is_new_user}")

        # ISSUE FIX #2: Validate Privy request structure before accessing nested fields
        if not request.user or not request.user.id:
            raise ValueError("Invalid Privy user data: user ID is required")

        if not isinstance(request.user.linked_accounts, list):
            logger.warning(
                f"Invalid linked_accounts structure for user {request.user.id}, "
                "defaulting to empty list"
            )
            request.user.linked_accounts = []

        # Extract user info from Privy linked accounts
        # Priority: 1) Top-level email from request, 2) Email from linked accounts, 3) Fallback
        email = request.email  # Start with top-level email if provided by frontend
        display_name = None
        auth_method = AuthMethod.EMAIL  # Default

        # Try to extract from linked accounts if not provided at top level
        if not email and request.user.linked_accounts:
            for account in request.user.linked_accounts:
                try:
                    account_email = _resolve_account_email(account)
                    if account.type == "email" and account_email:
                        email = account_email
                        auth_method = AuthMethod.EMAIL
                        logger.debug(f"Extracted email from email account: {email}")
                        break
                    elif account.type == "google_oauth" and account_email:
                        email = account_email
                        display_name = account.name
                        auth_method = AuthMethod.GOOGLE
                        logger.debug(
                            f"Extracted email from Google OAuth: {email}, "
                            f"display_name: {display_name}"
                        )
                        break
                    elif account.type == "github" and account.name:
                        display_name = account.name
                        auth_method = AuthMethod.GITHUB
                        logger.debug(f"Extracted GitHub username: {display_name}")
                        # GitHub doesn't provide email in this field, will use fallback
                except Exception as account_error:
                    logger.warning(
                        f"Error processing linked account for user {request.user.id}: "
                        f"{account_error}"
                    )
                    continue

        # ISSUE FIX #3: Improved email extraction with better logging
        if not email and request.token:
            logger.warning(
                f"No email found from linked accounts for user {request.user.id}, "
                "using fallback email format"
            )
            # Use a fallback email format instead of calling external API
            email = f"{request.user.id}@privy.user"
        elif not email:
            logger.error(
                f"Failed to extract any email for user {request.user.id} and no token provided"
            )
            raise ValueError(
                "Unable to extract email from Privy user data and no fallback available"
            )

        logger.info(
            f"Email extraction completed for user {request.user.id}: {email}, "
            f"auth_method: {auth_method}"
        )

        # Generate username from email or privy ID (for fallback check)
        username = email.split("@")[0] if email else f"user_{request.user.id[:8]}"
        logger.debug(f"Generated username for user {request.user.id}: {username}")

        # Check if user already exists by privy_user_id (with cache + timeout)
        existing_user = None

        # Try cache first
        logger.debug(f"Checking cache for Privy ID: {request.user.id}")
        existing_user = get_cached_user_by_privy_id(request.user.id)

        # If not in cache, try database with timeout
        if not existing_user:
            try:
                logger.debug(f"Cache miss for Privy ID, querying database...")
                existing_user = safe_query_with_timeout(
                    supabase_config.get_supabase_client(),
                    "users",
                    lambda: users_module.get_user_by_privy_id(request.user.id),
                    timeout_seconds=USER_LOOKUP_TIMEOUT,
                    operation_name="get user by privy_id",
                    fallback_value=None,
                    log_errors=True,
                )
                if existing_user:
                    # Cache the result for future lookups
                    cache_user_by_privy_id(request.user.id, existing_user)
            except QueryTimeoutError as e:
                logger.error(f"Privy ID lookup timed out: {e}")
                existing_user = None

        # Fallback: check by username if privy_user_id lookup failed
        if not existing_user:
            logger.debug(f"Privy ID lookup failed, trying username: {username}")

            # Try cache first
            existing_user = get_cached_user_by_username(username)

            if not existing_user:
                try:
                    logger.debug(f"Cache miss for username, querying database...")
                    existing_user = safe_query_with_timeout(
                        supabase_config.get_supabase_client(),
                        "users",
                        lambda: users_module.get_user_by_username(username),
                        timeout_seconds=USER_LOOKUP_TIMEOUT,
                        operation_name="get user by username",
                        fallback_value=None,
                        log_errors=True,
                    )
                    if existing_user:
                        # Cache the result
                        cache_user_by_username(username, existing_user)
                except QueryTimeoutError as e:
                    logger.error(f"Username lookup timed out: {e}")
                    existing_user = None

            if existing_user:
                logger.warning(
                    f"User found by username '{username}' but not by privy_user_id. Updating privy_user_id..."
                )
                # Update the existing user with the privy_user_id
                try:
                    client = supabase_config.get_supabase_client()
                    safe_query_with_timeout(
                        client,
                        "users",
                        lambda: client.table("users").update({"privy_user_id": request.user.id}).eq(
                            "id", existing_user["id"]
                        ).execute(),
                        timeout_seconds=AUTH_QUERY_TIMEOUT,
                        operation_name="update privy_user_id",
                        fallback_value=None,
                        log_errors=True,
                    )
                    existing_user["privy_user_id"] = request.user.id
                    logger.info(f"Updated user {existing_user['id']} with privy_user_id")
                    # Invalidate caches since we updated the user
                    invalidate_user_cache(privy_id=request.user.id, username=username)
                except QueryTimeoutError as e:
                    logger.error(f"Failed to update privy_user_id (timeout): {e}")
                except Exception as e:
                    logger.error(f"Failed to update privy_user_id: {e}")

        if existing_user:
            return _handle_existing_user(
                existing_user=existing_user,
                request=request,
                background_tasks=background_tasks,
                auth_method=auth_method,
                display_name=display_name,
                email=email,
                auto_create_api_key=request.auto_create_api_key
                if request.auto_create_api_key is not None
                else True,
            )
        else:
            # New user - create account
            logger.info(f"Creating new Privy user: {request.user.id}")

            # Create user with Privy ID (username already generated above)
            try:
                # Convert auth_method enum to string for create_enhanced_user
                auth_method_str = (
                    auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                )
                user_data = users_module.create_enhanced_user(
                    username=username,
                    email=email or f"{request.user.id}@privy.user",
                    auth_method=auth_method_str,
                    privy_user_id=request.user.id,
                    credits=10,  # Users start with $10 trial credits for 3 days
                )
            except Exception as creation_error:
                logger.warning(
                    "create_enhanced_user failed (%s); falling back to manual creation: %s",
                    creation_error,
                    str(creation_error),
                )

                client = supabase_config.get_supabase_client()

                # Re-check for existing user before attempting manual insert to avoid TOCTOU issues
                existing_user_after_failure = users_module.get_user_by_privy_id(request.user.id)
                if not existing_user_after_failure:
                    existing_user_after_failure = users_module.get_user_by_username(username)

                if existing_user_after_failure:
                    logger.info(
                        "Detected an existing user for Privy ID %s after fallback trigger; "
                        "returning existing record instead of inserting a duplicate",
                        request.user.id,
                    )
                    return _handle_existing_user(
                        existing_user=existing_user_after_failure,
                        request=request,
                        background_tasks=background_tasks,
                        auth_method=auth_method,
                        display_name=display_name,
                        email=email,
                        auto_create_api_key=request.auto_create_api_key
                        if request.auto_create_api_key is not None
                        else True,
                    )

                fallback_email = email or f"{request.user.id}@privy.user"

                resolved_username = _generate_unique_username(client, username)
                if resolved_username != username:
                    logger.info(
                        "Username collision detected for base '%s'; resolved to '%s'",
                        username,
                        resolved_username,
                    )
                    username = resolved_username

                trial_start = datetime.now(timezone.utc)
                trial_end = trial_start + timedelta(days=3)

                user_payload = {
                    "username": username,
                    "email": fallback_email,
                    "credits": 10,
                    "privy_user_id": request.user.id,
                    "auth_method": (
                        auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                    ),
                    "created_at": trial_start.isoformat(),
                    "welcome_email_sent": False,
                    "subscription_status": "trial",
                    "trial_expires_at": trial_end.isoformat(),
                    "tier": "basic",
                }

                try:
                    created_user = None
                    created_new_user = False

                    # Detect partially created user records before attempting an insert
                    partial_user = None
                    try:
                        partial_user = users_module.get_user_by_privy_id(
                            request.user.id
                        ) or users_module.get_user_by_username(username)
                    except Exception as lookup_error:
                        logger.warning(
                            "Failed to lookup partially created user prior to fallback insert: %s",
                            sanitize_for_logging(str(lookup_error)),
                        )

                    if partial_user:
                        logger.warning(
                            "Detected partially created user %s after create_enhanced_user failure; reusing existing record",
                            partial_user.get("id"),
                        )
                        update_fields = {
                            field: value
                            for field, value in user_payload.items()
                            if field != "created_at" and partial_user.get(field) != value
                        }
                        if update_fields:
                            update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
                            updated_result = (
                                client.table("users")
                                .update(update_fields)
                                .eq("id", partial_user["id"])
                                .execute()
                            )
                            if updated_result.data:
                                partial_user = updated_result.data[0]
                            else:
                                partial_user.update(update_fields)
                        created_user = partial_user
                    else:
                        try:
                            user_insert = client.table("users").insert(user_payload).execute()
                            if not user_insert.data:
                                raise HTTPException(
                                    status_code=500, detail="Failed to create user account"
                                ) from creation_error
                            created_user = user_insert.data[0]
                            created_new_user = True
                        except APIError as insert_error:
                            if getattr(insert_error, "code", None) == "23505":
                                logger.warning(
                                    "Fallback user insert encountered duplicate username/email (%s); retrieving existing record instead",
                                    insert_error.message
                                    if hasattr(insert_error, "message")
                                    else str(insert_error),
                                )
                                existing_user = (
                                    client.table("users")
                                    .select("*")
                                    .eq("username", username)
                                    .limit(1)
                                    .execute()
                                )
                                if not existing_user.data:
                                    existing_user = (
                                        client.table("users")
                                        .select("*")
                                        .eq("email", fallback_email)
                                        .limit(1)
                                        .execute()
                                    )
                                if not existing_user.data:
                                    raise HTTPException(
                                        status_code=500,
                                        detail="Failed to fetch existing user after duplicate insert",
                                    ) from insert_error
                                created_user = existing_user.data[0]
                            else:
                                raise

                    if created_user is None:
                        raise HTTPException(
                            status_code=500, detail="Failed to create user account"
                        ) from creation_error

                    # ISSUE FIX #5: Ensure environment_tag is valid before using it
                    env_tag = getattr(request, "environment_tag", None) or "live"
                    if env_tag not in {"live", "test", "development"}:
                        logger.warning(
                            f"Invalid environment_tag '{env_tag}' for user {created_user['id']}, defaulting to 'live'"
                        )
                        env_tag = "live"

                    api_key_value = created_user.get("api_key")
                    try:
                        existing_key_result = (
                            client.table("api_keys_new")
                            .select("api_key")
                            .eq("user_id", created_user["id"])
                            .eq("is_active", True)
                            .order("is_primary", desc=True)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        if existing_key_result.data:
                            api_key_value = existing_key_result.data[0]["api_key"]
                            logger.info(
                                f"Reusing existing API key for fallback user {created_user['id']}"
                            )
                    except Exception as api_key_lookup_error:
                        logger.error(
                            "Failed to check existing API keys for fallback user %s: %s",
                            created_user["id"],
                            api_key_lookup_error,
                        )

                    if not api_key_value:
                        api_key_value = f"gw_live_{username}_fallback"
                        try:
                            client.table("api_keys_new").insert(
                                {
                                    "user_id": created_user["id"],
                                    "api_key": api_key_value,
                                    "key_name": "Primary API Key",
                                    "is_primary": True,
                                    "is_active": True,
                                    "environment_tag": env_tag,
                                }
                            ).execute()
                            logger.info(
                                f"Created API key for fallback user {created_user['id']} with environment_tag: {env_tag}"
                            )
                        except Exception as api_key_error:
                            logger.error(
                                f"Failed to create API key for fallback user {created_user['id']}: {api_key_error}"
                            )

                    user_data = {
                        "user_id": created_user["id"],
                        "username": created_user.get("username", username),
                        "email": created_user.get("email", fallback_email),
                        "credits": created_user.get("credits", 10),
                        "primary_api_key": api_key_value,
                        "api_key": api_key_value,
                        "scope_permissions": created_user.get("scope_permissions", {}),
                        "subscription_status": created_user.get("subscription_status", "trial"),
                        "trial_expires_at": created_user.get("trial_expires_at"),
                        "tier": created_user.get("tier"),
                        "subscription_end_date": created_user.get("subscription_end_date"),
                    }
                    logger.info(
                        (
                            "Successfully created fallback user %s with username %s"
                            if created_new_user
                            else "Successfully reused existing user %s for fallback signup (username=%s)"
                        ),
                        created_user["id"],
                        username,
                    )

                except Exception as fallback_error:
                    logger.error(f"Fallback user creation failed: {fallback_error}", exc_info=True)
                    raise HTTPException(
                        status_code=500, detail="Failed to create user account"
                    ) from fallback_error

            # OPTIMIZATION: Process referral code in background to avoid blocking auth response
            # Referral tracking is non-critical for successful login/signup
            referral_code_valid = False
            if request.referral_code:
                logger.info(f"Queuing referral code processing for new user: {request.referral_code}")
                background_tasks.add_task(
                    _process_referral_code_background,
                    referral_code=request.referral_code,
                    user_id=user_data["user_id"],
                    username=username,
                    is_new_user=True,
                )
                # We don't know if it's valid until processed in background, so assume it might be valid
                # This is logged/tracked in the background task

            # OPTIMIZATION: Send welcome email in background for new users
            if email:
                background_tasks.add_task(
                    _send_new_user_welcome_email_background,
                    user_id=user_data["user_id"],
                    username=user_data["username"],
                    email=email,
                    credits=user_data["credits"],
                )

            logger.info(f"New Privy user created: {user_data['user_id']}")
            logger.info(
                f"Referral code processing result for new user {user_data['user_id']}: valid={referral_code_valid}"
            )

            # OPTIMIZATION: Log registration activity in background
            activity_metadata = {
                "action": "register",
                "auth_method": (
                    auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                ),
                "privy_user_id": request.user.id,
                "is_new_user": True,
                "initial_credits": user_data["credits"],
                "referral_code": request.referral_code,
                "referral_code_valid": referral_code_valid,
            }
            background_tasks.add_task(
                _log_registration_activity_background,
                user_id=user_data["user_id"],
                metadata=activity_metadata,
            )

            # ISSUE FIX #4: Ensure credits is a float value with error handling for new users
            try:
                new_user_credits = float(user_data["credits"])
                logger.debug(
                    f"Normalized new user {user_data['user_id']} credits to float: {new_user_credits}"
                )
            except (ValueError, TypeError) as credits_error:
                logger.error(
                    f"Failed to convert new user credits (value: {user_data['credits']}, "
                    f"type: {type(user_data['credits']).__name__}): {credits_error}, "
                    "defaulting to 10.0"
                )
                new_user_credits = 10.0
            logger.info(f"Returning registration response with credits: {new_user_credits}")

            tier_value = user_data.get("tier")

            return PrivyAuthResponse(
                success=True,
                message="Account created successfully",
                user_id=user_data["user_id"],
                api_key=user_data["primary_api_key"],
                auth_method=auth_method,
                privy_user_id=request.user.id,
                is_new_user=True,
                display_name=display_name or user_data["username"],
                email=email,
                credits=new_user_credits,
                timestamp=datetime.now(timezone.utc),
                subscription_status=user_data.get("subscription_status", "trial"),
                tier=tier_value,
                tier_display_name=_get_tier_display_name(tier_value),
                trial_expires_at=user_data.get("trial_expires_at"),
                subscription_end_date=user_data.get("subscription_end_date"),
            )

    except Exception as e:
        logger.error(f"Privy authentication failed: {e}")
        error_message = str(e)
        # Provide clearer error message for common configuration issues
        if (
            "missing an 'http://' or 'https://' protocol" in error_message.lower()
            or "supabase_url must start with" in error_message.lower()
            or "supabase_url environment variable is not set" in error_message.lower()
        ):
            logger.error(
                "SUPABASE_URL environment variable is missing or misconfigured. "
                "Please update your environment configuration with a valid URL "
                "(e.g., https://xxxxx.supabase.co)"
            )
            raise HTTPException(
                status_code=503,
                detail="Service configuration error: Database URL is misconfigured. Please contact support.",
            ) from e
        raise HTTPException(status_code=500, detail=f"Authentication failed: {error_message}") from e


@router.post("/auth/register", response_model=UserRegistrationResponse, tags=["authentication"])
async def register_user(request: UserRegistrationRequest, background_tasks: BackgroundTasks):
    """Register a new user with username and email"""
    try:
        logger.info(f"Registration request for user: {request.username}")

        client = supabase_config.get_supabase_client()

        # Check if email already exists (with timeout)
        try:
            existing_email = safe_query_with_timeout(
                client,
                "users",
                lambda: client.table("users").select("id").eq("email", request.email).execute(),
                timeout_seconds=AUTH_QUERY_TIMEOUT,
                operation_name="check existing email",
                fallback_value=None,
                log_errors=True,
            )
            if existing_email and existing_email.data:
                raise HTTPException(status_code=400, detail="User with this email already exists")
        except QueryTimeoutError:
            logger.error("Timeout checking existing email, treating as unavailable")
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")

        # Check if username already exists (with timeout)
        try:
            existing_username = safe_query_with_timeout(
                client,
                "users",
                lambda: client.table("users").select("id").eq("username", request.username).execute(),
                timeout_seconds=AUTH_QUERY_TIMEOUT,
                operation_name="check existing username",
                fallback_value=None,
                log_errors=True,
            )
            if existing_username and existing_username.data:
                raise HTTPException(status_code=400, detail="Username already taken")
        except QueryTimeoutError:
            logger.error("Timeout checking existing username, treating as unavailable")
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")

        # Create user first
        try:
            # Convert auth_method enum to string for create_enhanced_user
            auth_method_str = (
                request.auth_method.value
                if hasattr(request.auth_method, "value")
                else str(request.auth_method)
            )
            user_data = users_module.create_enhanced_user(
                username=request.username,
                email=request.email,
                auth_method=auth_method_str,
                privy_user_id=None,  # No Privy for direct registration
                credits=10,
            )
        except Exception as creation_error:
            logger.warning(
                "create_enhanced_user failed during registration (%s); using manual fallback: %s",
                creation_error,
                str(creation_error),
            )

            trial_start = datetime.now(timezone.utc)
            trial_end = trial_start + timedelta(days=3)

            fallback_payload = {
                "username": request.username,
                "email": request.email,
                "credits": 10,
                "privy_user_id": None,
                "auth_method": (
                    request.auth_method.value
                    if hasattr(request.auth_method, "value")
                    else str(request.auth_method)
                ),
                "created_at": trial_start.isoformat(),
                "welcome_email_sent": False,
                "subscription_status": "trial",
                "trial_expires_at": trial_end.isoformat(),
                "tier": "basic",
            }

            try:
                user_insert = client.table("users").insert(fallback_payload).execute()
                if not user_insert.data:
                    raise HTTPException(
                        status_code=500, detail="Failed to create user account"
                    ) from creation_error

                created_user = user_insert.data[0]
                api_key_value = f"gw_live_{request.username}_fallback"

                # Apply same fixes as privy_auth: validate environment_tag
                env_tag = request.environment_tag or "live"
                if env_tag not in {"live", "test", "development"}:
                    logger.warning(
                        f"Invalid environment_tag '{env_tag}' for registration, defaulting to 'live'"
                    )
                    env_tag = "live"

                try:
                    client.table("api_keys_new").insert(
                        {
                            "user_id": created_user["id"],
                            "api_key": api_key_value,
                            "key_name": request.key_name,
                            "is_primary": True,
                            "is_active": True,
                            "environment_tag": env_tag,
                        }
                    ).execute()
                    logger.info(
                        f"Created API key for fallback registration user {created_user['id']} "
                        f"with environment_tag: {env_tag}"
                    )
                except Exception as api_key_error:
                    logger.error(
                        f"Failed to create API key for registration user {created_user['id']}: "
                        f"{api_key_error}, proceeding without API key in api_keys_new table"
                    )

                user_data = {
                    "user_id": created_user["id"],
                    "username": created_user.get("username", request.username),
                    "email": created_user.get("email", request.email),
                    "credits": created_user.get("credits", 10),
                    "primary_api_key": api_key_value,
                    "api_key": api_key_value,
                    "scope_permissions": created_user.get("scope_permissions", {}),
                    "subscription_status": created_user.get("subscription_status", "trial"),
                    "trial_expires_at": created_user.get("trial_expires_at"),
                    "tier": created_user.get("tier"),
                    "subscription_end_date": created_user.get("subscription_end_date"),
                }
                logger.info(
                    f"Successfully created fallback registration user {created_user['id']} "
                    f"with username {request.username}"
                )
            except Exception as fallback_error:
                logger.error(
                    f"Fallback registration user creation failed: {fallback_error}", exc_info=True
                )
                raise HTTPException(
                    status_code=500, detail="Failed to create user account"
                ) from fallback_error

        # OPTIMIZATION: Process referral code in background to avoid blocking registration response
        if request.referral_code:
            logger.info(f"Queuing referral code processing for user: {request.referral_code}")
            background_tasks.add_task(
                _process_referral_code_background,
                referral_code=request.referral_code,
                user_id=user_data["user_id"],
                username=request.username,
                is_new_user=True,
            )

        # Send welcome email
        try:
            success = notif_module.enhanced_notification_service.send_welcome_email(
                user_id=user_data["user_id"],
                username=user_data["username"],
                email=request.email,
                credits=user_data["credits"],
            )

            if success:
                from src.db.users import mark_welcome_email_sent

                mark_welcome_email_sent(user_data["user_id"])
                logger.info(f"Welcome email sent for user {user_data['user_id']}")
        except Exception as e:
            logger.warning(f"Failed to send welcome email: {e}")

        logger.info(f"User registered successfully: {user_data['user_id']}")

        return UserRegistrationResponse(
            user_id=user_data["user_id"],
            username=user_data["username"],
            email=request.email,
            api_key=user_data["primary_api_key"],
            credits=user_data["credits"],
            environment_tag=request.environment_tag,
            scope_permissions=user_data.get("scope_permissions", {}),
            auth_method=request.auth_method,
            subscription_status=SubscriptionStatus.TRIAL,
            message="Account created successfully",
            timestamp=datetime.now(timezone.utc),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}") from e


@router.post("/auth/password-reset", tags=["authentication"])
async def request_password_reset(email: str):
    """Request password reset email"""
    try:
        # Find the user by email
        client = supabase_config.get_supabase_client()
        user_result = (
            client.table("users").select("id", "username", "email").eq("email", email).execute()
        )

        if not user_result.data:
            # Don't reveal if email exists or not for security
            return {
                "message": "If an account with that email exists, a password reset link has been sent."
            }

        user = user_result.data[0]

        # Send password reset email
        reset_token = notif_module.enhanced_notification_service.send_password_reset_email(
            user_id=user["id"], username=user["username"], email=user["email"]
        )

        if reset_token:
            return {"message": "Password reset email sent successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send password reset email")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error requesting password reset: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/auth/reset-password", tags=["authentication"])
async def reset_password(token: str):
    """Reset password using token"""
    try:
        client = supabase_config.get_supabase_client()

        # Verify token
        token_result = (
            client.table("password_reset_tokens")
            .select("*")
            .eq("token", token)
            .eq("used", False)
            .execute()
        )

        if not token_result.data:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")

        token_data = token_result.data[0]
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))

        if datetime.now(timezone.utc).replace(tzinfo=expires_at.tzinfo) > expires_at:
            raise HTTPException(status_code=400, detail="Reset token has expired")

        # Update password (in a real app, you'd hash this)
        # For now, we'll just mark the token as used
        client.table("password_reset_tokens").update({"used": True}).eq(
            "id", token_data["id"]
        ).execute()

        return {"message": "Password reset successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting password: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e
