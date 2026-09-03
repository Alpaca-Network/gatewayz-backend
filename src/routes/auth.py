import asyncio
import hashlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from postgrest import APIError
from prometheus_client import Counter

import src.config.supabase_config as supabase_config
import src.db.users as users_module
import src.enhanced_notification_service as notif_module
from src.db.activity import log_activity
from src.db.users import _is_temporary_api_key
from src.schemas import (
    AuthMethod,
    PrivyAuthRequest,
    PrivyAuthResponse,
    SubscriptionStatus,
    UserRegistrationRequest,
    UserRegistrationResponse,
)
from src.security.privy_token import (
    PrivyTokenError,
    privy_verification_mode,
    verify_privy_access_token,
)
from src.services.auth_cache import (
    cache_user_by_privy_id,
    cache_user_by_username,
    get_cached_user_by_privy_id,
    get_cached_user_by_username,
)
from src.services.auth_rate_limiting import AuthRateLimitType, check_auth_rate_limit, get_client_ip
from src.services.email_verification import EmailVerificationResult
from src.services.email_verification import verify_email as emailable_verify_email
from src.services.query_timeout import (
    AUTH_QUERY_TIMEOUT,
    USER_LOOKUP_TIMEOUT,
    QueryTimeoutError,
    safe_query_with_timeout,
)
from src.utils.security_validators import (
    is_blocked_email_domain,
    is_temporary_email_domain,
    is_valid_email,
    sanitize_for_logging,
)
from src.utils.sentry_context import capture_error

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
        existing = client.table("users").select("id").eq("username", candidate).limit(1).execute()
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


async def _get_subscription_status_for_email(email: str) -> tuple[str, bool]:
    """
    Determine subscription status for an email using Emailable API + local checks.

    Returns:
        Tuple of (subscription_status, should_block)
        - subscription_status: "trial" or "bot"
        - should_block: True if registration should be blocked entirely
    """
    if not email:
        return "trial", False

    # Skip verification for Privy placeholder emails
    if email.endswith("@privy.user") or email.endswith("@privy.placeholder"):
        return "trial", False

    # Step 1: Check local blocklist (blocked domains are rejected outright)
    if is_blocked_email_domain(email):
        logger.warning(f"Email blocked by local blocklist: {sanitize_for_logging(email)}")
        return "bot", True

    # Step 2: Check local temp email list (fast check)
    if is_temporary_email_domain(email):
        logger.info(f"Temporary email detected by local check: {sanitize_for_logging(email)}")
        return "bot", False

    # Step 3: Use Emailable API for comprehensive verification
    try:
        result: EmailVerificationResult = await emailable_verify_email(email)

        logger.info(
            f"Emailable verification for {sanitize_for_logging(email)}: "
            f"state={result.state.value}, reason={result.reason.value}, "
            f"score={result.score}, disposable={result.is_disposable}"
        )

        # Block undeliverable emails
        if result.should_block:
            logger.warning(
                f"Email blocked by Emailable: {sanitize_for_logging(email)} "
                f"({result.reason.value})"
            )
            return "bot", True

        # Mark disposable/suspicious as bot
        if result.is_bot:
            logger.info(f"Email marked as bot by Emailable: {sanitize_for_logging(email)}")
            return "bot", False

        return "trial", False

    except Exception as e:
        # Don't block registration if API fails - fall back to local checks only
        logger.error(f"Emailable API error for {sanitize_for_logging(email)}: {e}")
        # Capture to Sentry for monitoring
        capture_error(
            e,
            context_type="provider",
            context_data={
                "provider": "emailable",
                "operation": "email_verification",
            },
        )
        return "trial", False


def _handle_existing_user(
    existing_user: dict[str, Any],
    request: PrivyAuthRequest,
    background_tasks: BackgroundTasks,
    auth_method: AuthMethod,
    display_name: str | None,
    email: str | None,
    phone_number: str | None = None,
    auto_create_api_key: bool = True,
) -> PrivyAuthResponse:
    """Build a consistent response for existing users."""
    logger.info(f"Existing Privy user found: {existing_user['id']}")
    logger.info(f"User welcome email status: {existing_user.get('welcome_email_sent', 'Not set')}")
    _login_allowance = existing_user.get("subscription_allowance", 0) or 0
    _login_purchased = existing_user.get("purchased_credits", 0) or 0
    logger.info(
        "User credits at login: subscription_allowance=%s purchased_credits=%s",
        _login_allowance,
        _login_purchased,
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
    if api_key_to_return and _is_temporary_api_key(api_key_to_return):
        logger.warning(
            "Detected temporary API key for user %s (length: %d). "
            "This key was created during registration but never properly replaced. "
            "Will create a new proper key.",
            existing_user["id"],
            len(api_key_to_return),
        )
        # Don't return temporary keys - force key recreation by setting to None
        api_key_to_return = None

    # user_credits is only used for legacy migration detection below; tiered fields are used for response
    user_credits = float(existing_user.get("subscription_allowance") or 0) + float(
        existing_user.get("purchased_credits") or 0
    )

    tier = existing_user.get("tier")
    tier_display_name = _get_tier_display_name(tier)
    subscription_status_value = existing_user.get("subscription_status")
    trial_expires_at = existing_user.get("trial_expires_at")
    subscription_end_date = existing_user.get("subscription_end_date")

    # Calculate tiered credit fields (same logic as get_user_profile)
    # Database stores values in dollars, frontend expects cents
    subscription_allowance_dollars = float(existing_user.get("subscription_allowance") or 0)
    purchased_credits_dollars = float(existing_user.get("purchased_credits") or 0)
    legacy_credits_dollars = float(existing_user.get("credits") or 0)

    # If tiered fields are empty but legacy credits exist, migrate them once at login
    if (
        subscription_allowance_dollars == 0
        and purchased_credits_dollars == 0
        and legacy_credits_dollars > 0
    ):
        if tier in ("pro", "max") and subscription_status_value == "active":
            # Active Pro/Max subscriber - legacy credits are subscription allowance
            subscription_allowance_dollars = legacy_credits_dollars
        else:
            # Basic user or no active subscription - legacy credits are purchased credits
            purchased_credits_dollars = legacy_credits_dollars

        # Write back to DB so deduction RPC sees the correct values
        try:
            _supabase = supabase_config.get_supabase_client()
            _supabase.table("users").update(
                {
                    "subscription_allowance": subscription_allowance_dollars,
                    "purchased_credits": purchased_credits_dollars,
                }
            ).eq("id", existing_user["id"]).execute()
            logger.info(
                "Migrated legacy credits for user %s: $%.2f → subscription_allowance=$%.2f, purchased_credits=$%.2f",
                existing_user["id"],
                legacy_credits_dollars,
                subscription_allowance_dollars,
                purchased_credits_dollars,
            )
        except Exception as _migration_err:
            logger.warning(
                "Failed to persist legacy credit migration for user %s: %s",
                existing_user["id"],
                _migration_err,
            )

    total_credits_dollars = subscription_allowance_dollars + purchased_credits_dollars

    # Send raw DB values — no unit conversion
    subscription_allowance_cents = subscription_allowance_dollars
    purchased_credits_cents = purchased_credits_dollars
    total_credits_cents = total_credits_dollars
    allowance_reset_date = existing_user.get("allowance_reset_date")

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
                from src.services.payment_gate import resolve_key_environment

                # The payment gate applies here too. Guarding only
                # POST /user/api-keys left this path as a free source of live
                # keys, which is exactly the hole the gate exists to close.
                env_tag, _downgraded = resolve_key_environment(existing_user, "live")

                # Create a new primary key for this user
                primary_key, _ = create_api_key(
                    user_id=existing_user["id"],
                    key_name="Primary Key (Auto-created)",
                    environment_tag=env_tag,
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

    # Send raw credit value — no unit conversion
    user_credits_cents = user_credits
    logger.info("Returning login response with credits: %.4f", user_credits)

    # CRITICAL: Verify API key exists before returning success for existing users
    # This prevents silent failures where user exists but has no working key
    if not api_key_to_return:
        logger.critical(
            "CRITICAL: Existing user %s login but NO API KEY available! "
            "This user will not be able to authenticate API requests. "
            "Privy ID: %s, Email: %s",
            existing_user["id"],
            request.user.id,
            user_email,
        )
        raise HTTPException(
            status_code=503,
            detail="Your account exists but no API key is available. Please try again or contact support.",
        )

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
        phone_number=phone_number or existing_user.get("phone_number"),
        credits=user_credits_cents,  # In cents — matches /user/profile
        timestamp=datetime.now(UTC),
        subscription_status=subscription_status_value,
        tier=tier,
        tier_display_name=tier_display_name,
        trial_expires_at=trial_expires_at,
        subscription_end_date=subscription_end_date,
        # Tiered credit fields (in cents for frontend)
        subscription_allowance=subscription_allowance_cents,
        purchased_credits=purchased_credits_cents,
        total_credits=total_credits_cents,
        allowance_reset_date=allowance_reset_date,
    )


# Background task functions for non-blocking operations
# ISSUE FIX #6: Improved background task error handling with better logging


def _is_valid_deliverable_email(email: str) -> bool:
    """Check if email is valid and deliverable (not a fallback placeholder).

    This function combines RFC validation with additional checks to ensure
    we don't attempt to send emails to placeholder addresses that are only
    used for database storage.
    """
    if not email or "@" not in email:
        return False
    # Skip fallback placeholder emails that cannot receive mail
    if email.endswith("@privy.user"):
        return False
    # Skip new placeholder format used for database storage when no valid email exists
    # These are RFC-valid but not deliverable (e.g., noemail+xxx@privy.placeholder)
    if email.endswith("@privy.placeholder"):
        return False
    # Use RFC-compliant validation for real emails
    return is_valid_email(email)


def _send_welcome_email_background(user_id: str, username: str, email: str, credits: float):
    """Send welcome email in background for existing users"""
    try:
        logger.info(f"Background task: Sending welcome email to user {user_id}")
        # Validate email is both RFC-compliant and deliverable (not a placeholder)
        if not _is_valid_deliverable_email(email):
            logger.warning(
                f"Background task: Invalid or non-deliverable email '{email}' for user {user_id}, "
                f"skipping welcome email. User can still use the service."
            )
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
        # Validate email is both RFC-compliant and deliverable (not a placeholder)
        if not _is_valid_deliverable_email(email):
            logger.warning(
                f"Background task: Invalid or non-deliverable email '{email}' for new user {user_id}, "
                f"skipping welcome email. User can still use the service."
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


# gatewayz-backend#2248: tracks the outcome of server-side Privy access-token
# verification on POST /auth so the "log" -> "enforce" rollout can be watched.
privy_token_verification_total = Counter(
    "gatewayz_privy_token_verification_total",
    "Outcome of Privy access-token verification on POST /auth",
    ["result", "mode"],
)

# Reasons that should read as "no token was given" vs. "a token was given but invalid".
_MISSING_TOKEN_ERROR = "privy_token_required"
_INVALID_TOKEN_ERROR = "invalid_privy_token"


def _enforce_privy_token(request: PrivyAuthRequest, raw_request: Request, client_ip: str) -> None:
    """
    Verify the Privy access token before any account lookup/creation runs.

    `request.user.id` is client-supplied; without this check anyone who knows
    (or guesses) a Privy DID could obtain that account's API key. Behavior is
    controlled by `Config.PRIVY_TOKEN_VERIFICATION` (see
    `src.security.privy_token.privy_verification_mode`):
      - "off": no-op (tests only).
      - "log": verification failures are logged, not blocked, so the frontend
        rollout can be watched before enforcing.
      - "enforce": verification failures raise 401.

    Sets `raw_request.state.privy_token_verified` so downstream wallet
    ingestion (gatewayz-backend#2251) can gate on a verified token.
    """
    mode = privy_verification_mode()
    raw_request.state.privy_token_verified = False

    if mode == "off":
        return

    token = request.token or request.privy_access_token
    try:
        verify_privy_access_token(token, expected_sub=request.user.id)
        raw_request.state.privy_token_verified = True
        privy_token_verification_total.labels(result="verified", mode=mode).inc()
        return
    except PrivyTokenError as e:
        privy_token_verification_total.labels(result=e.reason, mode=mode).inc()
        if mode == "log":
            logger.warning(
                "privy_token_unverified reason=%s privy_id=%s ip=%s",
                e.reason,
                request.user.id,
                client_ip,
            )
            return

        error_code = _MISSING_TOKEN_ERROR if e.reason == "missing" else _INVALID_TOKEN_ERROR
        raise HTTPException(
            status_code=401,
            detail={"error": error_code, "reason": e.reason},
        ) from e


@router.post("/auth", response_model=PrivyAuthResponse, tags=["authentication"])
async def privy_auth(
    request: PrivyAuthRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
):
    """Authenticate user via Privy and return API key"""
    # Rate limit check - 10 attempts per 15 minutes per IP
    client_ip = get_client_ip(raw_request)
    rate_limit_result = await check_auth_rate_limit(client_ip, AuthRateLimitType.LOGIN)
    if not rate_limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many login attempts. Please try again in {rate_limit_result.retry_after} seconds.",
                "retry_after": rate_limit_result.retry_after,
            },
            headers={"Retry-After": str(rate_limit_result.retry_after)},
        )

    # Verify the Privy access token before touching any account (gatewayz-backend#2248).
    # Deliberately kept outside the try/except below: that block's broad
    # `except Exception` converts any exception (HTTPException included) into a
    # generic 500, which would swallow this 401.
    _enforce_privy_token(request, raw_request, client_ip)

    try:
        logger.info(f"Privy auth request for user: {request.user.id}")
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
        # Priority: 1) Top-level email from request, 2) Email from linked accounts, 3) Phone from linked accounts, 4) Fallback
        email = request.email  # Start with top-level email if provided by frontend
        phone_number = None  # Phone number for SMS auth
        display_name = None
        auth_method = AuthMethod.EMAIL  # Default

        # Try to extract from linked accounts if not provided at top level
        for account in request.user.linked_accounts or []:
            try:
                account_email = _resolve_account_email(account)
                if account.type == "phone" and account.phone_number:
                    # Phone authentication - extract phone number
                    phone_number = account.phone_number
                    if not email:  # Only set auth method if email wasn't found first
                        auth_method = AuthMethod.PHONE
                    logger.debug(f"Extracted phone number from phone account: {phone_number}")
                elif account.type == "email" and account_email and not email:
                    email = account_email
                    auth_method = AuthMethod.EMAIL
                    logger.debug(f"Extracted email from email account: {email}")
                elif account.type == "google_oauth" and account_email and not email:
                    email = account_email
                    display_name = account.name
                    auth_method = AuthMethod.GOOGLE
                    logger.debug(
                        f"Extracted email from Google OAuth: {email}, "
                        f"display_name: {display_name}"
                    )
                elif account.type == "github" and account.name and not display_name:
                    display_name = account.name
                    # Only set auth method to GITHUB if no email AND no phone auth
                    # Phone auth (priority 3) takes precedence over GitHub (priority 4)
                    if not email and auth_method != AuthMethod.PHONE:
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
        # NOTE: We no longer generate fallback emails from Privy IDs because they contain
        # special characters (colons) that are not valid in email addresses per RFC 5321/5322.
        # If no email is available, we'll skip sending the welcome email instead of attempting
        # to send to an invalid address that will be rejected by email providers.
        if not email:
            logger.warning(
                f"No valid email found for user {request.user.id}. "
                f"Welcome email will be skipped. User can still use the service."
            )
            # Set email to None to indicate it's not available
            # The background task will check and skip sending if email is None or invalid
            email = None

        logger.info(
            f"Auth info extraction completed for user {request.user.id}: "
            f"email={email}, phone={phone_number}, auth_method={auth_method}"
        )

        # Generate base username from email, phone number, or privy ID
        # Note: This is just the base - uniqueness is ensured later via _generate_unique_username
        if email:
            username = email.split("@")[0]
        elif phone_number:
            # Use last 4 digits of phone number as base for username
            clean_phone = "".join(filter(str.isdigit, phone_number))
            username = (
                f"user_{clean_phone[-4:]}"
                if len(clean_phone) >= 4
                else f"user_{request.user.id[:8]}"
            )
        else:
            username = f"user_{request.user.id[:8]}"
        logger.debug(f"Generated base username for user {request.user.id}: {username}")

        # Check if user already exists by privy_user_id (with cache + timeout)
        existing_user = None

        # Try cache first
        logger.debug(f"Checking cache for Privy ID: {request.user.id}")
        existing_user = get_cached_user_by_privy_id(request.user.id)

        # If not in cache, try database with timeout
        if not existing_user:
            try:
                logger.debug("Cache miss for Privy ID, querying database...")
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

        # SECURITY (gatewayz-backend#2248 security review, "Fix round 1"): once the
        # Privy access token has been cryptographically verified, privy_user_id is
        # the ONLY authoritative key for locating an existing account. `email` (and
        # therefore `username`, derived from it above) is unauthenticated
        # client-supplied input — it is not part of the token's claims — so it must
        # never be used to locate, rebind, or return the API key of someone else's
        # account. If no row has this DID, this is unconditionally a NEW account,
        # even if the derived username/email collides with an existing user. The
        # username-fallback block below (a legacy-account merge path for accounts
        # created before privy_user_id was captured) is therefore skipped entirely
        # once the token is verified; the new-account path a few lines down already
        # handles a username collision safely (_generate_unique_username) and gets
        # its own email-collision guard.
        token_verified = bool(getattr(raw_request.state, "privy_token_verified", False))

        # Fallback: check by username if privy_user_id lookup failed
        if not existing_user and token_verified:
            logger.debug(
                "Privy ID lookup failed for verified token sub=%s; skipping the "
                "username fallback and treating this as a new account",
                request.user.id,
            )
        elif not existing_user:
            logger.debug(f"Privy ID lookup failed, trying username: {username}")

            # Try cache first
            existing_user = get_cached_user_by_username(username)

            if not existing_user:
                try:
                    logger.debug("Cache miss for username, querying database...")
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
                # This whole `elif` only runs when the token was NOT verified (the
                # `if not existing_user and token_verified:` branch above already
                # skipped straight to new-account creation for verified callers) —
                # so nothing below may treat `request.user.id` as proven.
                current_privy_id = existing_user.get("privy_user_id")
                if current_privy_id and current_privy_id != request.user.id:
                    # SECURITY: never rebind an account that already belongs to a
                    # different Privy identity — that is an account takeover, not a
                    # legacy merge, and is refused unconditionally regardless of
                    # verification mode. Backward compatibility during rollout: this
                    # account's key is still returned below (unlike the null-DID
                    # case) since it was already discoverable by an unverified
                    # caller before this fix; only the corrupting rebind is refused.
                    logger.warning(
                        "privy_auth.rebind_blocked base=%s existing_privy_id=%s attempted_sub=%s",
                        username,
                        current_privy_id,
                        request.user.id,
                    )
                else:
                    # SECURITY (gatewayz-backend#2248 review, "Fix round 2"):
                    # current_privy_id is None — a legacy account (email/password or
                    # wallet signup) that never linked Privy. Claiming ANY account,
                    # linked or not, via unauthenticated client-supplied
                    # username/email is a takeover; the previous behavior (rebind +
                    # return this account's key with zero proof of ownership) let an
                    # unverified caller take over any never-linked account by
                    # guessing its username. Refuse the claim entirely — this falls
                    # through to the new-account path below (which safely
                    # suffixes a colliding username and placeholder-guards a
                    # colliding email) rather than adopting someone else's row.
                    logger.warning(
                        "privy_auth.legacy_account_claim_blocked username=%s sub=%s",
                        username,
                        request.user.id,
                    )
                    existing_user = None

        if existing_user:
            return _handle_existing_user(
                existing_user=existing_user,
                request=request,
                background_tasks=background_tasks,
                auth_method=auth_method,
                display_name=display_name,
                email=email,
                phone_number=phone_number,
                auto_create_api_key=(
                    request.auto_create_api_key if request.auto_create_api_key is not None else True
                ),
            )
        else:
            # New user - create account
            logger.info(f"Creating new Privy user: {request.user.id}")

            # Verify email using Emailable API + local checks
            subscription_status = "trial"
            if email:
                subscription_status, should_block = await _get_subscription_status_for_email(email)
                if should_block:
                    logger.warning(
                        f"Registration blocked for email: {sanitize_for_logging(email)} "
                        f"(privy_user_id={request.user.id})"
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="This email address is not allowed. Please use a valid email.",
                    )
                if subscription_status == "bot":
                    logger.warning(
                        f"Email marked as bot: {sanitize_for_logging(email)} "
                        f"(privy_user_id={request.user.id})"
                    )

            # Legacy variable for backwards compatibility in fallback code
            is_temp_email = subscription_status == "bot"

            # Ensure username is unique before attempting to create user
            # This prevents duplicate username errors, especially for phone auth
            # where multiple users might have phone numbers ending in the same 4 digits
            client = supabase_config.get_supabase_client()
            _base_username = username
            username = _generate_unique_username(client, username)
            if username != _base_username:
                logger.info(
                    "privy_auth.username_collision base=%s sub=%s resolved=%s",
                    _base_username,
                    request.user.id,
                    username,
                )
            logger.debug(f"Resolved unique username for new user: {username}")

            # Create user with Privy ID
            try:
                # Convert auth_method enum to string for create_enhanced_user
                auth_method_str = (
                    auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                )
                # Use a placeholder email if no valid email is available
                # Note: We use a safe placeholder instead of the Privy ID because
                # Privy IDs contain colons which are not valid in email addresses
                _placeholder_email = (
                    f"noemail+{request.user.id.replace(':', '_')}@privy.placeholder"
                )
                user_email = email if email and is_valid_email(email) else _placeholder_email

                # SECURITY (gatewayz-backend#2248 review, "Fix round 1"/"Fix round 2"):
                # `email` is unauthenticated client input. This point in the function
                # is reached whenever no account was found (or claimable) by
                # privy_user_id — a genuinely new account, in every verification
                # mode. If the derived email belongs to an existing (necessarily
                # different) account, letting create_enhanced_user proceed would
                # either fail on the DB's UNIQUE constraint or, worse, be adopted as
                # a "claim" of that account by the duplicate-insert fallback below.
                # Refuse the claim up front, unconditionally: fall back to the
                # placeholder email and flag it for manual merge.
                if user_email != _placeholder_email:
                    email_owner = users_module.get_user_by_email(user_email)
                    if email_owner and email_owner.get("privy_user_id") != request.user.id:
                        email_hash = hashlib.sha256(user_email.lower().encode()).hexdigest()[:12]
                        logger.warning(
                            "privy_auth.legacy_account_claim_blocked email_hash=%s sub=%s",
                            email_hash,
                            request.user.id,
                        )
                        user_email = _placeholder_email

                user_data = users_module.create_enhanced_user(
                    username=username,
                    email=user_email,
                    auth_method=auth_method_str,
                    privy_user_id=request.user.id,
                    credits=0,  # No free credits — paid only
                    subscription_status="inactive",
                )
            except Exception as creation_error:
                logger.warning(
                    "create_enhanced_user failed (%s); falling back to manual creation: %s",
                    creation_error,
                    str(creation_error),
                )

                client = supabase_config.get_supabase_client()

                # Re-check for existing user before attempting manual insert to avoid TOCTOU issues.
                # SECURITY (gatewayz-backend#2248 review, "Fix round 1"/"Fix round 2"):
                # only an exact privy_user_id match may be adopted here, in every mode —
                # a username match is unauthenticated client input and adopting/
                # returning that row would be the same account-takeover class fixed
                # above (a legitimate concurrent-insert race is still caught: that
                # row already carries this same request's privy_user_id).
                existing_user_after_failure = users_module.get_user_by_privy_id(request.user.id)

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
                        phone_number=phone_number,
                        auto_create_api_key=(
                            request.auto_create_api_key
                            if request.auto_create_api_key is not None
                            else True
                        ),
                    )

                # Use a safe placeholder email if no valid email is available
                # Note: We use a safe placeholder instead of the Privy ID because
                # Privy IDs contain colons which are not valid in email addresses
                fallback_email = (
                    email
                    if email and is_valid_email(email)
                    else f"noemail+{request.user.id.replace(':', '_')}@privy.placeholder"
                )

                resolved_username = _generate_unique_username(client, username)
                if resolved_username != username:
                    logger.info(
                        "Username collision detected for base '%s'; resolved to '%s'",
                        username,
                        resolved_username,
                    )
                    username = resolved_username

                user_payload = {
                    "username": username,
                    "email": fallback_email,
                    "purchased_credits": 0.0,
                    "subscription_allowance": 0.0,
                    "privy_user_id": request.user.id,
                    "auth_method": (
                        auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                    ),
                    "created_at": datetime.now(UTC).isoformat(),
                    "welcome_email_sent": False,
                    "subscription_status": "inactive",
                    "tier": "basic",
                }

                try:
                    created_user = None
                    created_new_user = False

                    # Detect partially created user records before attempting an insert.
                    # SECURITY: same rule as above, in every mode — only an exact
                    # privy_user_id match may be adopted here, never an
                    # unauthenticated username lookup (which would then get its row
                    # updated/rebound below).
                    partial_user = None
                    try:
                        partial_user = users_module.get_user_by_privy_id(request.user.id)
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
                            update_fields["updated_at"] = datetime.now(UTC).isoformat()
                            updated_result = (
                                client.table("users")
                                .update(update_fields)
                                .eq("id", partial_user["id"])
                                .execute()
                            )
                            if updated_result.data and len(updated_result.data) > 0:
                                partial_user = updated_result.data[0]
                            else:
                                partial_user.update(update_fields)
                        created_user = partial_user
                    else:
                        try:
                            user_insert = client.table("users").insert(user_payload).execute()
                            if not user_insert.data or len(user_insert.data) == 0:
                                raise HTTPException(
                                    status_code=500, detail="Failed to create user account"
                                ) from creation_error
                            created_user = user_insert.data[0]
                            created_new_user = True
                        except APIError as insert_error:
                            if getattr(insert_error, "code", None) == "23505":
                                logger.warning(
                                    "Fallback user insert encountered duplicate username/email (%s); retrieving existing record instead",
                                    (
                                        insert_error.message
                                        if hasattr(insert_error, "message")
                                        else str(insert_error)
                                    ),
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
                                if not existing_user.data or len(existing_user.data) == 0:
                                    raise HTTPException(
                                        status_code=500,
                                        detail="Failed to fetch existing user after duplicate insert",
                                    ) from insert_error
                                conflicting_user = existing_user.data[0]
                                # SECURITY (gatewayz-backend#2248 review, "Fix round
                                # 1"/"Fix round 2"): the row that collided is whatever
                                # unauthenticated username/email pointed at — do not
                                # adopt it as "our" created user (and hand back its API
                                # key) unless it's genuinely this same request's own
                                # row (a real insert race), i.e. its privy_user_id
                                # already matches. This check is unconditional — in
                                # every verification mode — because adopting a legacy,
                                # never-linked (privy_user_id IS NULL) row here would
                                # reopen exactly the takeover this function's primary
                                # username fallback refuses further up.
                                if conflicting_user.get("privy_user_id") != request.user.id:
                                    logger.warning(
                                        "privy_auth.duplicate_insert_claim_blocked "
                                        "conflicting_user_id=%s sub=%s",
                                        conflicting_user.get("id"),
                                        request.user.id,
                                    )
                                    raise HTTPException(
                                        status_code=500,
                                        detail="Failed to create user account",
                                    ) from insert_error
                                created_user = conflicting_user
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

                    # A brand-new account has no payment signal, so a requested
                    # live key is downgraded to a free rate-limited test key
                    # rather than handed out. Signup still succeeds — refusing
                    # registration over a key environment would be worse than
                    # the farming it prevents.
                    from src.services.payment_gate import resolve_key_environment

                    env_tag, _downgraded = resolve_key_environment(created_user, env_tag)

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
                        if existing_key_result.data and len(existing_key_result.data) > 0:
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
                        "credits": float(created_user.get("purchased_credits", 0) or 0)
                        + float(created_user.get("subscription_allowance", 0) or 0),
                        "primary_api_key": api_key_value,
                        "api_key": api_key_value,
                        "scope_permissions": created_user.get("scope_permissions", {}),
                        "subscription_status": created_user.get("subscription_status", "inactive"),
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

            # OPTIMIZATION: Log registration activity in background
            activity_metadata = {
                "action": "register",
                "auth_method": (
                    auth_method.value if hasattr(auth_method, "value") else str(auth_method)
                ),
                "privy_user_id": request.user.id,
                "is_new_user": True,
                "initial_credits": user_data["credits"],
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
                    "defaulting to 0.0"
                )
                new_user_credits = 0.0
            logger.info(f"Returning registration response with credits: {new_user_credits}")

            tier_value = user_data.get("tier")

            # No unit conversion — raw credit value
            new_user_credits_cents = new_user_credits
            new_subscription_allowance = 0  # No subscription yet
            new_purchased_credits = new_user_credits
            new_total_credits = new_user_credits

            # CRITICAL: Verify API key exists before returning success
            # This prevents silent failures where user is created but has no working key
            if not user_data.get("primary_api_key"):
                logger.critical(
                    "CRITICAL: User %s created but NO API KEY generated! "
                    "This user will not be able to authenticate API requests. "
                    "Privy ID: %s, Email: %s",
                    user_data["user_id"],
                    request.user.id,
                    email,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Account created but API key generation failed. Please try again or contact support.",
                )

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
                phone_number=phone_number,
                credits=new_user_credits_cents,  # In cents — matches /user/profile
                timestamp=datetime.now(UTC),
                subscription_status=user_data.get("subscription_status", "inactive"),
                tier=tier_value,
                tier_display_name=_get_tier_display_name(tier_value),
                trial_expires_at=user_data.get("trial_expires_at"),
                subscription_end_date=user_data.get("subscription_end_date"),
                # Tiered credit fields (in cents for frontend)
                subscription_allowance=new_subscription_allowance,
                purchased_credits=new_purchased_credits,
                total_credits=new_total_credits,
                allowance_reset_date=None,  # No subscription = no reset date
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
        raise HTTPException(
            status_code=500, detail=f"Authentication failed: {error_message}"
        ) from e


@router.post("/auth/register", response_model=UserRegistrationResponse, tags=["authentication"])
async def register_user(
    request: UserRegistrationRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
):
    """Register a new user with username and email"""
    # Rate limit check - 3 attempts per hour per IP (prevent mass account creation)
    client_ip = get_client_ip(raw_request)
    rate_limit_result = await check_auth_rate_limit(client_ip, AuthRateLimitType.REGISTER)
    if not rate_limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many registration attempts. Please try again in {rate_limit_result.retry_after} seconds.",
                "retry_after": rate_limit_result.retry_after,
            },
            headers={"Retry-After": str(rate_limit_result.retry_after)},
        )

    try:
        logger.info(f"Registration request for user: {request.username}")

        # Verify email using Emailable API + local checks
        subscription_status, should_block = await _get_subscription_status_for_email(request.email)
        if should_block:
            logger.warning(f"Registration blocked for email: {sanitize_for_logging(request.email)}")
            raise HTTPException(
                status_code=400,
                detail="This email address is not allowed. Please use a valid email.",
            )
        if subscription_status == "bot":
            logger.warning(f"Email marked as bot: {sanitize_for_logging(request.email)}")

        # Legacy variable for backwards compatibility
        is_temp_email = subscription_status == "bot"

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
                lambda: client.table("users")
                .select("id")
                .eq("username", request.username)
                .execute(),
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
                credits=0,  # No free credits — paid only
                subscription_status="inactive",
            )
        except Exception as creation_error:
            logger.warning(
                "create_enhanced_user failed during registration (%s); using manual fallback: %s",
                creation_error,
                str(creation_error),
            )

            fallback_payload = {
                "username": request.username,
                "email": request.email,
                "purchased_credits": 0.0,
                "subscription_allowance": 0.0,
                "privy_user_id": None,
                "auth_method": (
                    request.auth_method.value
                    if hasattr(request.auth_method, "value")
                    else str(request.auth_method)
                ),
                "created_at": datetime.now(UTC).isoformat(),
                "welcome_email_sent": False,
                "subscription_status": "inactive",
                "tier": "basic",
            }

            try:
                user_insert = client.table("users").insert(fallback_payload).execute()
                if not user_insert.data or len(user_insert.data) == 0:
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

                # Payment gate: a fresh registration has no payment signal, so a
                # live key is downgraded to a free test key.
                from src.services.payment_gate import resolve_key_environment

                env_tag, _downgraded = resolve_key_environment(created_user, env_tag)

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
                    "credits": float(created_user.get("purchased_credits", 0) or 0)
                    + float(created_user.get("subscription_allowance", 0) or 0),
                    "primary_api_key": api_key_value,
                    "api_key": api_key_value,
                    "scope_permissions": created_user.get("scope_permissions", {}),
                    "subscription_status": created_user.get("subscription_status", "inactive"),
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

        # Send welcome email
        # FREEZE FIX: Wrap in asyncio.to_thread() — send_welcome_email() calls
        # _wait_for_rate_limit() which uses time.sleep(0.5s). Calling it directly
        # from an async route blocks the entire event loop for each email sent.
        try:
            success = await asyncio.to_thread(
                notif_module.enhanced_notification_service.send_welcome_email,
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
            timestamp=datetime.now(UTC),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}") from e


@router.post("/auth/password-reset", tags=["authentication"])
async def request_password_reset(email: str, raw_request: Request):
    """Request password reset email"""
    # Rate limit check - 3 attempts per hour per IP (prevent email bombing)
    client_ip = get_client_ip(raw_request)
    rate_limit_result = await check_auth_rate_limit(client_ip, AuthRateLimitType.PASSWORD_RESET)
    if not rate_limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many password reset requests. Please try again in {rate_limit_result.retry_after} seconds.",
                "retry_after": rate_limit_result.retry_after,
            },
            headers={"Retry-After": str(rate_limit_result.retry_after)},
        )

    try:
        # Find the user by email
        client = supabase_config.get_supabase_client()
        user_result = (
            client.table("users").select("id", "username", "email").eq("email", email).execute()
        )

        if not user_result.data or len(user_result.data) == 0:
            # Don't reveal if email exists or not for security
            return {
                "message": "If an account with that email exists, a password reset link has been sent."
            }

        user = user_result.data[0]

        # Send password reset email
        # FREEZE FIX: Wrap in asyncio.to_thread() — same _wait_for_rate_limit() sleep issue.
        reset_token = await asyncio.to_thread(
            notif_module.enhanced_notification_service.send_password_reset_email,
            user_id=user["id"],
            username=user["username"],
            email=user["email"],
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
async def reset_password(token: str, raw_request: Request):
    """Reset password using token"""
    # Rate limit check - 3 attempts per hour per IP (prevent token enumeration)
    client_ip = get_client_ip(raw_request)
    rate_limit_result = await check_auth_rate_limit(client_ip, AuthRateLimitType.PASSWORD_RESET)
    if not rate_limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many password reset attempts. Please try again in {rate_limit_result.retry_after} seconds.",
                "retry_after": rate_limit_result.retry_after,
            },
            headers={"Retry-After": str(rate_limit_result.retry_after)},
        )

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

        if not token_result.data or len(token_result.data) == 0:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")

        token_data = token_result.data[0]
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))

        if datetime.now(UTC).replace(tzinfo=expires_at.tzinfo) > expires_at:
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


@router.get("/auth/health", tags=["authentication", "health"])
async def auth_health_check():
    """
    Dedicated health check endpoint for authentication service.

    Returns comprehensive health status including:
    - Database connectivity (Supabase)
    - Redis cache availability
    - Auth cache statistics
    - Query timeout configuration
    - Overall auth service status

    This endpoint does not require authentication and is suitable for
    load balancer health checks and monitoring systems.
    """
    import time

    from src.config.redis_config import get_redis_client
    from src.services.auth_cache import get_auth_cache_stats_lightweight

    start_time = time.time()
    health_status = {
        "service": "auth",
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": {},
        "latency_ms": 0,
    }

    issues = []

    # Check 1: Database connectivity
    # Use execute_with_timeout directly (not safe_query_with_timeout) so we can
    # properly distinguish between timeouts and database errors
    from src.services.query_timeout import execute_with_timeout

    db_check_start = time.time()
    try:
        client = supabase_config.get_supabase_client()
        # Simple query to verify connection - use timeout
        result = execute_with_timeout(
            lambda: client.table("users").select("id").limit(1).execute(),
            timeout_seconds=3,
            operation_name="auth health check",
        )
        db_latency = (time.time() - db_check_start) * 1000

        health_status["checks"]["database"] = {
            "status": "healthy",
            "latency_ms": round(db_latency, 2),
        }
    except QueryTimeoutError as e:
        db_latency = (time.time() - db_check_start) * 1000
        health_status["checks"]["database"] = {
            "status": "timeout",
            "latency_ms": round(db_latency, 2),
            "error": str(e),
        }
        issues.append("database_timeout")
    except Exception as e:
        db_latency = (time.time() - db_check_start) * 1000
        health_status["checks"]["database"] = {
            "status": "unhealthy",
            "latency_ms": round(db_latency, 2),
            "error": str(e),
        }
        issues.append("database_error")

    # Check 2: Redis cache availability
    redis_check_start = time.time()
    try:
        redis_client = get_redis_client()
        if redis_client:
            # Ping Redis
            redis_client.ping()
            redis_latency = (time.time() - redis_check_start) * 1000
            health_status["checks"]["redis"] = {
                "status": "healthy",
                "latency_ms": round(redis_latency, 2),
            }
        else:
            redis_latency = (time.time() - redis_check_start) * 1000
            health_status["checks"]["redis"] = {
                "status": "unavailable",
                "latency_ms": round(redis_latency, 2),
                "note": "Redis client not configured - using fallback caching",
            }
            # Redis being unavailable is not critical - we have in-memory fallback
    except Exception as e:
        redis_latency = (time.time() - redis_check_start) * 1000
        health_status["checks"]["redis"] = {
            "status": "unhealthy",
            "latency_ms": round(redis_latency, 2),
            "error": str(e),
        }
        issues.append("redis_error")

    # Check 3: Auth cache statistics (using lightweight O(1) operations only)
    try:
        cache_stats = get_auth_cache_stats_lightweight()
        health_status["checks"]["auth_cache"] = {
            "status": "healthy" if cache_stats.get("redis_available", False) else "degraded",
            "stats": cache_stats,
        }
    except Exception as e:
        health_status["checks"]["auth_cache"] = {
            "status": "error",
            "error": str(e),
        }

    # Check 4: Query timeout configuration
    health_status["checks"]["timeouts"] = {
        "auth_query_timeout_seconds": AUTH_QUERY_TIMEOUT,
        "user_lookup_timeout_seconds": USER_LOOKUP_TIMEOUT,
    }

    # Calculate total latency
    total_latency = (time.time() - start_time) * 1000
    health_status["latency_ms"] = round(total_latency, 2)

    # Determine overall status
    if "database_error" in issues:
        health_status["status"] = "unhealthy"
    elif "database_timeout" in issues or "redis_error" in issues:
        health_status["status"] = "degraded"
    elif total_latency > 5000:  # > 5 seconds is concerning
        health_status["status"] = "degraded"
        health_status["warning"] = f"High latency detected: {total_latency:.0f}ms"

    # Add issues list if any
    if issues:
        health_status["issues"] = issues

    return health_status
