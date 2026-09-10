"""
FastAPI Security Dependencies
Dependency injection functions for authentication and authorization
"""

import logging
import os
import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.security.security import audit_logger, validate_api_key_security
from src.services.user_lookup_cache import get_user
from src.utils.validators import ensure_api_key_like, ensure_non_empty_string

logger = logging.getLogger(__name__)


# Key-validation failures, mapped to a status AND a machine-readable code.
#
# The two spend ceilings are the reason this exists. A rate limit is transient
# and should be waited out; an exhausted request cap or an empty balance is
# terminal until a human raises the cap or tops up. Cap exhaustion used to
# return 429 — the same status as a rate limit — with a bare
# `{"detail": "..."}` string, so an integrator could only tell them apart by
# matching on prose, and every SDK with a retry policy (Anthropic's included)
# retried a condition that retrying can never clear.
#
# Ordered: the first substring found in the message wins, so put the specific
# entries above the general ones.
_KEY_FAILURE_MAP: tuple[tuple[str, int, str, str], ...] = (
    (
        "limit reached",
        402,
        "request_cap_exhausted",
        "This API key has reached its request cap. Retrying will not clear it — "
        "the cap must be raised.",
    ),
    (
        "Insufficient credits",
        402,
        "insufficient_credits",
        "Insufficient credits. Please add credits to continue.",
    ),
    ("inactive", 401, "api_key_inactive", "This API key is inactive."),
    ("expired", 401, "api_key_expired", "This API key has expired."),
    ("IP address", 403, "ip_not_allowed", "This IP address is not allowed for this API key."),
    ("Domain", 403, "domain_not_allowed", "This domain is not allowed for this API key."),
    ("not allowed", 403, "not_allowed", "This request is not allowed for this API key."),
)


def ceiling_http_exception(error_message: str) -> HTTPException:
    """Build the HTTPException for a key-validation failure.

    Returns a structured body — `{"error": {"message", "type", "code"}}` —
    so a caller can branch on `code` instead of substring-matching the prose.
    Unrecognised failures keep the historical 401 default.
    """
    for keyword, status, code, message in _KEY_FAILURE_MAP:
        if keyword in error_message:
            # The two spend ceilings get the canned, actionable copy — they are
            # what a partner branches on. Everything else echoes the original
            # validation message, which is what callers already relied on.
            body = message if status == 402 else error_message
            return HTTPException(
                status_code=status,
                detail={
                    "error": {
                        "message": body,
                        "type": (
                            "invalid_request_error" if status == 402 else "authentication_error"
                        ),
                        "code": code,
                    }
                },
            )
    return HTTPException(
        status_code=401,
        detail={
            "error": {
                "message": error_message,
                "type": "authentication_error",
                "code": "invalid_api_key",
            }
        },
    )


# HTTP Bearer security scheme with auto_error=False to allow custom error handling
security = HTTPBearer(auto_error=False)

# Constants
ERROR_INVALID_ADMIN_API_KEY = "Invalid admin API key"


async def get_admin_key(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())) -> str:
    """
    Validate admin API key with security improvements

    This function validates that the provided API key matches the configured
    ADMIN_API_KEY environment variable using constant-time comparison to
    prevent timing attacks.

    Args:
        credentials: HTTP Authorization credentials containing the bearer token

    Returns:
        The validated admin API key string

    Raises:
        HTTPException: 401 if the admin key is invalid, missing, or doesn't match
    """
    admin_key = credentials.credentials

    # Input validation
    try:
        ensure_non_empty_string(admin_key, "admin API key")
        ensure_api_key_like(admin_key, field_name="admin API key", min_length=10)
    except ValueError:
        # Do not leak details; preserve current response contract
        raise HTTPException(status_code=401, detail=ERROR_INVALID_ADMIN_API_KEY) from None

    # Get expected key from environment
    expected_key = os.environ.get("ADMIN_API_KEY")

    # Ensure admin key is configured
    if not expected_key:
        logger.error("ADMIN_API_KEY environment variable is not configured")
        raise HTTPException(status_code=401, detail=ERROR_INVALID_ADMIN_API_KEY)

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(admin_key, expected_key):
        audit_logger.log_security_violation(
            violation_type="INVALID_ADMIN_KEY_ATTEMPT",
            details=f"Invalid admin key attempt with key prefix: {admin_key[:10]}...",
        )
        raise HTTPException(status_code=401, detail=ERROR_INVALID_ADMIN_API_KEY)

    return admin_key


async def get_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None,
    *,
    log_security_violations: bool = True,
) -> str:
    """
    Validate API key from Authorization header

    Extracts and validates Bearer token with security checks including:
    - Key existence and format
    - Active status
    - Expiration date
    - Request limits
    - IP allowlist
    - Domain restrictions

    In local/development environment (APP_ENV=development), API key validation is bypassed
    to allow open access for development.

    Args:
        credentials: HTTP Authorization credentials
        request: FastAPI request object
        log_security_violations: Whether to log security violations for invalid keys.
            Set to False for optional auth endpoints where invalid credentials
            should silently fall back to anonymous access.

    Returns:
        Validated API key string (or dummy key in local environment)

    Raises:
        HTTPException: 401/402/403 depending on error type (402 = a terminal
            spend ceiling: request cap exhausted or credits out)
    """
    # Import Config here to avoid circular imports
    from src.config import Config

    # In local/development environment with NO credentials, fall back to a dev
    # API key so keyless local requests still work. When credentials ARE provided
    # (e.g. a real signed-in user's key from the frontend), honor them instead —
    # otherwise dev mode would hijack every request with a shared dev user and
    # 401 real users (breaking sign-in with a re-auth loop).
    if Config.IS_DEVELOPMENT and not credentials:
        from src.utils.dev_api_key import get_or_create_dev_api_key

        dev_key = get_or_create_dev_api_key()
        if dev_key:
            logger.debug("Using development API key for local environment")
            return dev_key
        else:
            # Fallback to bypass key if dev key creation fails
            logger.warning("Failed to create dev API key, using bypass key")
            return "local-dev-bypass-key"

    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    api_key = credentials.credentials
    if not api_key:
        raise HTTPException(status_code=401, detail="API key is required")

    # Extract security context
    client_ip = None
    referer = None
    user_agent = None

    if request:
        client_ip = request.client.host if request.client else None
        referer = request.headers.get("referer")
        user_agent = request.headers.get("user-agent")

    try:
        # Validate API key with security checks
        validated_key = validate_api_key_security(
            api_key=api_key, client_ip=client_ip, referer=referer
        )

        # Log successful authentication
        user = get_user(api_key)
        if user and request:
            audit_logger.log_api_key_usage(
                user_id=user["id"],
                key_id=user.get("key_id", 0),
                endpoint=request.url.path,
                ip_address=client_ip or "unknown",
                user_agent=user_agent,
            )

        return validated_key

    except ValueError as e:
        error_message = str(e)

        # Log security violation (only for required auth endpoints)
        if log_security_violations and client_ip:
            audit_logger.log_security_violation(
                violation_type="INVALID_API_KEY", details=error_message, ip_address=client_ip
            )

        raise ceiling_http_exception(error_message) from e

    except Exception as e:
        logger.error(f"Unexpected error validating API key: {e}")
        raise HTTPException(status_code=500, detail="Internal authentication error") from e


async def get_current_user(api_key: str = Depends(get_api_key)) -> dict[str, Any]:
    """
    Get the current authenticated user and validate trial expiration

    Chains with get_api_key to extract full user object and checks
    if trial period has expired for trial users.

    Args:
        api_key: Validated API key

    Returns:
        User dictionary with all data

    Raises:
        HTTPException: 404 if user not found, 402 if trial expired
    """
    user = get_user(api_key)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


# Roles that satisfy an admin-gated endpoint. 'superadmin' is a strict
# superset of 'admin' -- see docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md
# §3 Phase A1.
_ADMIN_ROLES = ("admin", "superadmin")


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    Require admin role (admin OR superadmin)

    Args:
        user: Current user

    Returns:
        User dictionary if admin

    Raises:
        HTTPException: 403 if not admin
    """
    is_admin = user.get("is_admin", False) or user.get("role") in _ADMIN_ROLES

    if not is_admin:
        audit_logger.log_security_violation(
            violation_type="UNAUTHORIZED_ADMIN_ACCESS",
            user_id=user.get("id"),
            details="Non-admin attempted admin endpoint",
        )
        raise HTTPException(status_code=403, detail="Administrator privileges required")

    return user


async def require_superadmin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    Require superadmin role specifically (not satisfied by plain admin).

    Used for the most sensitive operations: staff management (granting/
    revoking admin access) and other actions where "an admin" isn't enough
    authority -- see docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md
    §3 Phase A2.

    Args:
        user: Current user

    Returns:
        User dictionary if superadmin

    Raises:
        HTTPException: 403 if not superadmin
    """
    if user.get("role") != "superadmin":
        audit_logger.log_security_violation(
            violation_type="UNAUTHORIZED_SUPERADMIN_ACCESS",
            user_id=user.get("id"),
            details="Non-superadmin attempted superadmin endpoint",
        )
        raise HTTPException(status_code=403, detail="Superadmin privileges required")

    return user


async def require_admin_or_env_key(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> dict[str, Any]:
    """
    Accept either a user admin API key OR the ADMIN_API_KEY env var.

    Tries user-key auth first (require_admin path). If that fails with 401
    (key not found in DB), falls back to env-var comparison (get_admin_key path).

    Returns:
        User dict if user-key auth, or {"role": "admin", "auth": "env_key"} for env-key.

    Raises:
        HTTPException: 401/403 if neither auth method succeeds.
    """
    token = credentials.credentials

    # 1. Try env-var admin key (fast, no DB)
    expected_env_key = os.environ.get("ADMIN_API_KEY")
    if expected_env_key and secrets.compare_digest(token, expected_env_key):
        return {"role": "admin", "auth": "env_key", "is_admin": True}

    # 2. Try user API key auth
    try:
        api_key = await get_api_key(credentials)
        user = await get_current_user(api_key)
        is_admin = user.get("is_admin", False) or user.get("role") in _ADMIN_ROLES
        if not is_admin:
            raise HTTPException(status_code=403, detail="Administrator privileges required")
        return user
    except HTTPException:
        raise


async def get_optional_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    request: Request = None,
) -> str | None:
    """
    Get API key if provided, None otherwise

    Use for endpoints that work for both auth and non-auth users
    but need the raw API key string (not the user object).

    In local/development environment (APP_ENV=development), returns None to allow
    anonymous access without requiring API keys.

    Note: Invalid credentials are silently ignored (returning None) without
    logging security violations, since authentication is optional for these
    endpoints and invalid credentials should fall back to anonymous access.

    Args:
        credentials: Optional credentials
        request: Request object

    Returns:
        Validated API key string if authenticated, None otherwise (or None in local env)
    """
    # Import Config here to avoid circular imports
    from src.config import Config

    # In local/development environment with no credentials, allow anonymous
    # access. When credentials ARE provided, validate them so a real signed-in
    # user is recognized instead of being forced to anonymous.
    if Config.IS_DEVELOPMENT and not credentials:
        return None

    if not credentials:
        return None

    try:
        # Don't log security violations for optional auth - invalid credentials
        # should silently fall back to anonymous access
        return await get_api_key(credentials, request, log_security_violations=False)
    except HTTPException:
        return None


async def get_optional_api_key_strict(
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    request: Request = None,
) -> str | None:
    """Optional auth that still refuses a key the caller actually supplied.

    Same contract as `get_optional_api_key` for the anonymous case — no
    credentials means `None`, and the endpoint serves anonymous traffic. The
    difference is what happens when a key IS supplied and fails validation:
    the lenient version swallows the rejection and hands back `None`, so the
    caller is treated as anonymous and learns nothing about their key.

    On a billed inference route that is actively misleading. A typo'd key
    produced "Model X is not available for anonymous users", sending an
    integrator to read the model catalog over one wrong character in a header —
    the same misdirection as returning 503 for an unknown model id, or 429 for
    an exhausted cap. An exhausted cap was swallowed the same way: a partner
    who hit their ceiling saw a model-availability message instead of the 402.

    Used only by the inference routes. The other call sites keep the lenient
    dependency on purpose: a stale token in a browser should degrade to
    anonymous on a public page, not 401 it.
    """
    if not credentials:
        return None
    # Deliberately NOT wrapped in try/except — the rejection is the point.
    return await get_api_key(credentials, request, log_security_violations=False)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    request: Request = None,
) -> dict[str, Any] | None:
    """
    Get user if authenticated, None otherwise

    Use for endpoints that work for both auth and non-auth users.

    Note: Invalid credentials are silently ignored (returning None) without
    logging security violations, since authentication is optional for these
    endpoints and invalid credentials should fall back to anonymous access.

    Args:
        credentials: Optional credentials
        request: Request object

    Returns:
        User dict if authenticated, None otherwise
    """
    if not credentials:
        return None

    try:
        # Don't log security violations for optional auth - invalid credentials
        # should silently fall back to anonymous access
        api_key = await get_api_key(credentials, request, log_security_violations=False)
        return get_user(api_key)
    except HTTPException:
        return None


async def require_active_subscription(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Require active subscription

    Args:
        user: Current user

    Returns:
        User if subscription active

    Raises:
        HTTPException: 403 if subscription inactive
    """
    subscription_status = user.get("subscription_status", "inactive")

    if subscription_status not in ["active", "trial"]:
        raise HTTPException(status_code=403, detail="Active subscription required")

    return user


async def check_credits(
    user: dict[str, Any] = Depends(get_current_user), min_credits: float = 0.0
) -> dict[str, Any]:
    """
    Check if user has sufficient credits and trial hasn't expired

    Args:
        user: Current user
        min_credits: Minimum credits required

    Returns:
        User if credits sufficient and trial valid

    Raises:
        HTTPException: 402 if insufficient credits or trial expired
    """
    current_credits = float(user.get("subscription_allowance") or 0) + float(
        user.get("purchased_credits") or 0
    )

    if current_credits < min_credits:
        logger.warning(
            "Insufficient credits for user %s: required=%s, available=%s",
            user.get("id"),
            min_credits,
            current_credits,
        )
        raise HTTPException(
            status_code=402,
            detail="Insufficient credits. Please add credits to continue.",
        )

    return user


async def get_user_id(user: dict[str, Any] = Depends(get_current_user)) -> int:
    """Extract just the user ID (lightweight dependency)"""
    return user["id"]


async def verify_key_permissions(
    api_key: str = Depends(get_api_key), required_permissions: list[str] = None
) -> str:
    """
    Verify API key has specific permissions

    Args:
        api_key: Validated API key
        required_permissions: List of required permissions

    Returns:
        API key if permissions valid

    Raises:
        HTTPException: 403 if insufficient permissions
    """
    if not required_permissions:
        return api_key

    user = get_user(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    scope_permissions = user.get("scope_permissions", {})

    for permission in required_permissions:
        allowed_resources = scope_permissions.get(permission, [])

        if "*" not in allowed_resources and permission not in allowed_resources:
            raise HTTPException(status_code=403, detail=f"API key lacks '{permission}' permission")

    return api_key
