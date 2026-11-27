import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response

from src.db.activity import get_user_usage_since
from src.db.api_keys import (
    create_api_key,
    delete_api_key,
    get_api_key_by_id,
    get_api_key_secret_by_id,
    get_user_all_api_keys_usage,
    get_user_api_keys,
    update_api_key,
)
from src.db.chat_completion_requests import get_usage_by_tag
from src.schemas import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    DeleteApiKeyRequest,
    UpdateApiKeyRequest,
    UpdateApiKeyResponse,
)
from src.security.deps import get_api_key
from src.security.roles import resolve_role_fields
from src.services.auth_rate_limiting import (
    AuthRateLimitType,
    check_auth_rate_limit,
)
from src.services.key_purpose import PURPOSE_GENERAL, normalize_purpose
from src.services.key_scopes import (
    SCOPE_ADMIN,
    SCOPE_KEY_MANAGEMENT,
    can_manage_keys,
    is_session_credential,
    privileged_scopes_in,
)
from src.services.usage_signing import ALG as USAGE_ALG
from src.services.usage_signing import SigningUnavailable
from src.services.usage_signing import key_id as usage_key_id
from src.services.usage_signing import public_key_b64 as usage_public_key_b64
from src.services.usage_signing import sign as sign_usage
from src.services.user_lookup_cache import get_user
from src.utils.safe_errors import log_internal_error
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)


router = APIRouter()


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"error": {"message": message, "type": "permission_error", "code": code}},
    )


def _require_key_manager(api_key: str) -> dict:
    """The caller's user, if this credential may manage keys.

    Allowed: the account's sign-in credential (primary key or gw_temp_ session
    key, which is what the web app uses) or a key with the explicit
    ``key_management`` scope. Ordinary inference keys get 403.
    """
    user = get_user(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not can_manage_keys(api_key, user):
        raise _forbidden(
            "key_management_scope_required",
            "This API key cannot manage API keys. Sign in to the dashboard, or use a key "
            f"with the '{SCOPE_KEY_MANAGEMENT}' scope.",
        )
    return user


def _check_scope_grant(api_key: str, user: dict, scope_permissions: dict | None) -> None:
    """Only a session credential may grant key_management or admin_api, and
    admin_api only on an elevated account."""
    privileged = privileged_scopes_in(scope_permissions)
    if not privileged:
        return
    if not is_session_credential(api_key, user):
        raise _forbidden(
            "session_required_to_grant_scope",
            f"Granting {', '.join(privileged)} requires a signed-in session.",
        )
    if SCOPE_ADMIN in privileged and not resolve_role_fields(user)[1]:
        raise _forbidden("admin_scope_not_allowed", "This account cannot hold admin-scoped keys.")


def _check_primary_target(api_key: str, user: dict, target: dict) -> None:
    """Changing the account's primary (sign-in) key needs a session credential,
    so a scoped key cannot lock the owner out."""
    if target.get("is_primary") and not is_session_credential(api_key, user):
        raise _forbidden(
            "session_required_for_primary_key",
            "Changing the primary key requires a signed-in session.",
        )


# API Key Management Endpoints
@router.post("/user/api-keys", tags=["authentication"])
async def create_user_api_key(
    request: CreateApiKeyRequest,
    api_key: str = Depends(get_api_key),
):
    """Create a new API key for the user.

    The full key is in this response only; every later read returns it masked.
    Without ``scope_permissions`` the new key is inference-only.
    """
    try:
        user = _require_key_manager(api_key)

        # Rate limit check - 10 API key creations per hour per user (prevent key spam)
        user_id = str(user["id"])
        rate_limit_result = await check_auth_rate_limit(user_id, AuthRateLimitType.API_KEY_CREATE)
        if not rate_limit_result.allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "message": f"Too many API key creation requests. Please try again in {rate_limit_result.retry_after} seconds.",
                    "retry_after": rate_limit_result.retry_after,
                },
                headers={"Retry-After": str(rate_limit_result.retry_after)},
            )

        if request.action == "create":
            _check_scope_grant(api_key, user, request.scope_permissions)

            # Validate input
            if request.expiration_days is not None and request.expiration_days <= 0:
                raise HTTPException(status_code=400, detail="Expiration days must be positive")

            if request.max_requests is not None and request.max_requests <= 0:
                raise HTTPException(status_code=400, detail="Max requests must be positive")

            # Key purpose: None/"general" or "validator" (no-logging mode,
            # src/services/key_purpose.py). Rejected before anything is created.
            try:
                purpose = normalize_purpose(request.purpose)
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=str(ve)) from ve

            # Validate environment tag
            valid_environments = ["test", "staging", "live", "development"]
            if request.environment_tag not in valid_environments:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid environment tag. Must be one of: {valid_environments}",
                )

            # Payment gate: live keys require a payment signal. Free signup
            # credits and referral payouts are already zero, so an unpriced live
            # key was the last remaining thing a credit-farming bot could mint
            # at zero cost — and the thing that made "accounts" numbers
            # unusable in diligence. 'test' keys stay free and rate limited.
            from src.services.payment_gate import check_live_key_allowed, gate_error_detail

            allowed, gate_reason = check_live_key_allowed(user, request.environment_tag)
            if not allowed:
                logger.info(
                    "Payment gate blocked %s key for user %s (%s)",
                    sanitize_for_logging(request.environment_tag),
                    sanitize_for_logging(str(user["id"])),
                    gate_reason,
                )
                raise HTTPException(
                    status_code=402,
                    detail=gate_error_detail(request.environment_tag),
                )

            # Create a new API key with Phase 4 security features (using an existing working system)
            try:
                # Use the existing create_api_key function for now (it works)
                new_api_key, key_id = create_api_key(
                    user_id=user["id"],
                    key_name=request.key_name,
                    environment_tag=request.environment_tag,
                    scope_permissions=request.scope_permissions,
                    expiration_days=request.expiration_days,
                    max_requests=request.max_requests,
                    ip_allowlist=request.ip_allowlist,
                    domain_referrers=request.domain_referrers,
                    **({"purpose": purpose} if purpose is not None else {}),
                )

                # Add Phase 4 security logging and audit features
                try:
                    from src.security.security import get_audit_logger

                    audit_logger = get_audit_logger()

                    # Log the API key creation using the key_id from the create operation
                    audit_logger.log_api_key_creation(
                        user["id"], key_id, request.key_name, request.environment_tag, "user"
                    )
                except Exception as audit_error:
                    # Don't fail the whole request if audit logging fails
                    logger.warning(
                        "Audit logging failed for API key creation: %s",
                        sanitize_for_logging(str(audit_error)),
                    )

                # Log the key creation for audit purposes (Phase 4 feature)
                logger.info(
                    "API key created with Phase 4 security features for user %s: %s (%s)",
                    sanitize_for_logging(str(user["id"])),
                    sanitize_for_logging(request.key_name),
                    sanitize_for_logging(request.environment_tag),
                )
            except ValueError as ve:
                # Handle specific validation errors
                error_message = str(ve)
                if "already exists" in error_message:
                    raise HTTPException(status_code=400, detail=error_message)
                else:
                    raise HTTPException(
                        status_code=400, detail=f"Validation error: {error_message}"
                    )

            return {
                "status": "success",
                "message": "API key created successfully with enhanced security features",
                "api_key": new_api_key,
                "key_name": request.key_name,
                "environment_tag": request.environment_tag,
                "purpose": purpose or PURPOSE_GENERAL,
                "security_features": {
                    "ip_allowlist": request.ip_allowlist or [],
                    "domain_referrers": request.domain_referrers or [],
                    "expiration_days": request.expiration_days,
                    "max_requests": request.max_requests,
                    "audit_logging": True,
                    "last_used_tracking": True,
                },
                "phase4_integration": True,
            }

        else:
            raise HTTPException(status_code=400, detail="Invalid action. Must be 'create'")

    except HTTPException:
        raise
    except Exception as e:
        public_error, _ = log_internal_error("Failed to create or change API key", e, logger)
        raise HTTPException(status_code=500, detail=public_error) from e


@router.put("/user/api-keys/{key_id}", tags=["authentication"])
async def update_user_api_key_endpoint(
    key_id: int, request: UpdateApiKeyRequest, api_key: str = Depends(get_api_key)
):
    """Update an existing API key for the user"""
    try:
        user = _require_key_manager(api_key)

        # Verify the user owns the key
        key_to_update = get_api_key_by_id(key_id, user["id"])

        if not key_to_update:
            raise HTTPException(status_code=404, detail="API key not found")

        _check_primary_target(api_key, user, key_to_update)

        # Rotation: a new secret for the same row. The new key is returned in
        # this response only (new_api_key); the old one stops working at once.
        if request.action == "rotate":
            from src.db_security import rotate_api_key

            new_api_key = rotate_api_key(key_id, user["id"])
            if not new_api_key:
                raise HTTPException(status_code=500, detail="Failed to rotate API key")

            logger.info(
                "API key rotated for user %s: %s",
                sanitize_for_logging(str(user["id"])),
                sanitize_for_logging(key_to_update["key_name"]),
            )
            rotated_key = get_api_key_by_id(key_id, user["id"])
            if not rotated_key:
                raise HTTPException(
                    status_code=500, detail="Failed to retrieve updated key details"
                )
            return UpdateApiKeyResponse(
                status="success",
                message="API key rotated. Copy the new key now: it will not be shown again.",
                updated_key=ApiKeyResponse(**rotated_key),
                timestamp=datetime.now(UTC),
                new_api_key=new_api_key,
            )

        elif request.action == "bulk_rotate":
            # Rotates every key, the primary (sign-in) key included.
            if not is_session_credential(api_key, user):
                raise _forbidden(
                    "session_required_for_primary_key",
                    "Bulk rotation requires a signed-in session.",
                )
            # Handle bulk rotation for all user keys
            try:
                from src.db_security import bulk_rotate_user_keys

                result = bulk_rotate_user_keys(
                    user_id=user["id"], environment_tag=request.environment_tag
                )

                return {
                    "status": "success",
                    "message": f"Bulk rotation completed: {result['rotated_count']} keys rotated",
                    "rotated_count": result["rotated_count"],
                    "new_keys": result["new_keys"],
                    "phase4_integration": True,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            except Exception as e:
                public_error, _ = log_internal_error("Bulk rotation failed", e, logger)
                raise HTTPException(status_code=500, detail=public_error) from e

        else:
            # Regular update - prepare updates (only include fields that were provided)
            updates = {}
            if request.key_name is not None:
                updates["key_name"] = request.key_name
            if request.scope_permissions is not None:
                _check_scope_grant(api_key, user, request.scope_permissions)
                updates["scope_permissions"] = request.scope_permissions
            if request.expiration_days is not None:
                updates["expiration_days"] = request.expiration_days
            if request.max_requests is not None:
                updates["max_requests"] = request.max_requests
            if request.ip_allowlist is not None:
                updates["ip_allowlist"] = request.ip_allowlist
            if request.domain_referrers is not None:
                updates["domain_referrers"] = request.domain_referrers
            if request.is_active is not None:
                updates["is_active"] = request.is_active
            if request.purpose is not None:
                try:
                    # "general" is stored as NULL (see normalize_purpose).
                    updates["purpose"] = normalize_purpose(request.purpose)
                except ValueError as ve:
                    raise HTTPException(status_code=400, detail=str(ve)) from ve

        if not updates:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        # Update the key in the database
        try:
            # Update the key named in the path. update_api_key looks a key up by
            # its secret, and this used to pass the CALLER's key -- so editing
            # key B while authenticated with key A silently edited A. For a
            # privacy flag that is a fail-open: B would keep being logged.
            key_secret = get_api_key_secret_by_id(key_id, user["id"])
            if not key_secret:
                raise HTTPException(status_code=500, detail="Failed to update API key")
            success = update_api_key(key_secret, user["id"], updates)

            if not success:
                raise HTTPException(status_code=500, detail="Failed to update API key")
        except ValueError as ve:
            # Handle specific validation errors
            error_message = str(ve)
            if "already exists" in error_message:
                raise HTTPException(status_code=400, detail=error_message)
            else:
                raise HTTPException(status_code=400, detail=f"Validation error: {error_message}")

        # Get the updated key details
        updated_key = get_api_key_by_id(key_id, user["id"])

        if not updated_key:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated key details")

        message = "API key updated successfully with enhanced security features"

        return UpdateApiKeyResponse(
            status="success",
            message=message,
            updated_key=ApiKeyResponse(**updated_key),
            timestamp=datetime.now(UTC),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating API key: %s", sanitize_for_logging(str(e)))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/user/api-keys", tags=["authentication"])
async def list_user_api_keys(api_key: str = Depends(get_api_key)):
    """Get all API keys for the authenticated user (masked: prefix + last 4)."""
    try:
        user = _require_key_manager(api_key)

        keys = get_user_api_keys(user["id"])

        # Add Phase 4 security status to each key
        enhanced_keys = []
        for key in keys:
            key_with_security = key.copy()
            key_with_security["security_status"] = {
                "has_ip_restrictions": bool(
                    key.get("ip_allowlist") and len(key.get("ip_allowlist", [])) > 0
                ),
                "has_domain_restrictions": bool(
                    key.get("domain_referrers") and len(key.get("domain_referrers", [])) > 0
                ),
                "has_expiration": bool(key.get("expiration_date")),
                "has_usage_limits": bool(key.get("max_requests")),
                "last_used_tracking": True,
                "audit_logging": True,
                "phase4_enhanced": True,
            }
            enhanced_keys.append(key_with_security)

        return {
            "status": "success",
            "total_keys": len(enhanced_keys),
            "keys": enhanced_keys,
            "phase4_integration": True,
            "security_features_enabled": True,
            "message": "API keys retrieved with Phase 4 security status",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing API keys: %s", sanitize_for_logging(str(e)))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/user/api-keys/{key_id}", tags=["authentication"])
async def delete_user_api_key(
    key_id: int, confirmation: DeleteApiKeyRequest, api_key: str = Depends(get_api_key)
):
    """Delete an API key for the user"""
    try:
        user = _require_key_manager(api_key)

        # Verify confirmation
        if confirmation.confirmation != "DELETE_KEY":
            raise HTTPException(
                status_code=400,
                detail="Confirmation must be 'DELETE_KEY' to proceed with key deletion",
            )

        # Resolve the key by id and ownership
        key_to_delete = get_api_key_by_id(key_id, user["id"])
        if not key_to_delete:
            raise HTTPException(status_code=404, detail="API key not found")

        _check_primary_target(api_key, user, key_to_delete)

        # Delete by the key itself (server-side only; never returned)
        key_secret = get_api_key_secret_by_id(key_id, user["id"])
        success = bool(key_secret) and delete_api_key(key_secret, user["id"])

        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete API key")

        return {
            "status": "success",
            "message": "API key deleted successfully",
            "deleted_key_id": key_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting API key: %s", sanitize_for_logging(str(e)))
        raise HTTPException(status_code=500, detail="Internal server error")


def _parse_since(since: str) -> str:
    """Normalize a `since` value to an ISO-8601 UTC timestamp.

    Accepts ISO-8601 (with or without timezone; naive is treated as UTC),
    epoch seconds, or epoch milliseconds. Raises ValueError on anything else.
    """
    value = since.strip()
    if value.replace(".", "", 1).isdigit():
        epoch = float(value)
        if epoch > 1e12:  # milliseconds
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


@router.get("/user/api-keys/usage", tags=["authentication"])
async def get_user_api_key_usage(
    api_key: str = Depends(get_api_key),
    since: str | None = None,
):
    """Get usage statistics for all API keys of the user.

    Each key reports TWO counts, because they answer two different questions:

    * ``requests_used`` — what consumed the key's **cap**. A rejected call does
      not, so a caller that only ever fails leaves this frozen at its old value.
    * ``arrivals`` / ``failed`` — what actually **reached** the key, read from
      the request ledger. ``last_arrival_at`` and ``last_failure_at`` come with
      them.

    Conflating those cost a day of a partner integration: a key showing
    ``requests_used: 1`` is equally consistent with one call ever and with six
    days of 503s, and only a hand-run experiment against production settled it.

    ``arrivals_measured: false`` means the rollup could not be read — the
    arrivals fields are then **absent, not zero**. Unreachable is not absent.

    Optional `since` (ISO-8601, epoch seconds, or epoch ms): additionally
    returns `usage_since` — the user's aggregate requests/tokens/cost from
    that moment to now. Aggregation is user-level (activity records carry no
    per-key linkage); per-key lifetime counters are unchanged.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        since_iso = None
        if since is not None:
            try:
                since_iso = _parse_since(since)
            except (ValueError, OverflowError, OSError):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Invalid `since` value — use ISO-8601 "
                        "(e.g. 2026-08-27T00:00:00Z), epoch seconds, or epoch ms"
                    ),
                )

        usage_stats = get_user_all_api_keys_usage(user["id"])

        if usage_stats is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve usage statistics")

        if since_iso is not None:
            usage_since = get_user_usage_since(user["id"], since_iso)
            if usage_since is None:
                raise HTTPException(
                    status_code=500, detail="Failed to aggregate usage since timestamp"
                )
            usage_stats = {**usage_stats, "usage_since": usage_since}

        # Add Phase 4 audit logging information
        enhanced_usage = usage_stats.copy()
        enhanced_usage["audit_logging"] = {
            "enabled": True,
            "last_audit_check": datetime.now(UTC).isoformat(),
            "security_events_tracked": True,
            "access_patterns_monitored": True,
        }
        enhanced_usage["phase4_integration"] = True

        return enhanced_usage

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting API key usage: %s", sanitize_for_logging(str(e)))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/usage", tags=["authentication"])
async def get_usage_by_tag_endpoint(
    api_key: str = Depends(get_api_key),
    tag: str | None = None,
    since: str | None = None,
):
    """Usage rolled up by attribution tag.

    The read half of request-tag passthrough. Without it the tag is write-only:
    a caller can set `x-gatewayz-tag` and has no way to confirm the gateway
    recorded it, which makes the feature unfalsifiable from outside.

    Optional `tag` narrows to one; optional `since` (ISO-8601, epoch seconds or
    epoch ms) bounds the window. Untagged traffic is not reported here -- this
    endpoint answers "what did this tag spend", and folding untagged calls into
    a bucket would invent an attribution nobody asserted.

    Failures are reported SEPARATELY from calls. A failed call cost real compute
    and averaging it into the totals flatters the initiative that spent it.
    """
    user = get_user(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    since_iso = None
    if since is not None:
        try:
            since_iso = _parse_since(since)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid `since` value — use ISO-8601 "
                    "(e.g. 2026-08-27T00:00:00Z), epoch seconds, or epoch ms"
                ),
            )

    rows = get_usage_by_tag(user["id"], tag=tag, since_iso=since_iso)
    if rows is None:
        # Not an empty list: "the rollup did not run" and "this tag spent
        # nothing" are different answers, and a dashboard must not render them
        # the same way.
        raise HTTPException(status_code=503, detail="Usage rollup unavailable")

    return {
        "object": "list",
        "tag": tag,
        "since": since_iso,
        "data": rows,
        # Said out loud so a reader never mistakes an empty list for a measured
        # zero -- the same three-state discipline the partner plan asks for.
        "measured": bool(rows),
    }


@router.get("/v1/usage/export", tags=["authentication"])
async def export_signed_usage(
    api_key: str = Depends(get_api_key),
    tag: str | None = None,
    since: str | None = None,
):
    """A signed usage rollup a third party can verify offline.

    The response BODY is the exact byte string that was signed; the signature,
    algorithm and key id travel in headers. Returning the signature inside a
    JSON wrapper would invite a consumer to re-serialize the payload before
    checking it, at which point the signature verifies nothing -- the failure is
    silent and the crypto is irrelevant.

    Verify with the key at GET /v1/usage/export/key:

        ed25519_verify(public_key, base64_decode(x-gatewayz-signature), body)
    """
    user = get_user(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    since_iso = None
    if since is not None:
        try:
            since_iso = _parse_since(since)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid `since` value — use ISO-8601 "
                    "(e.g. 2026-08-27T00:00:00Z), epoch seconds, or epoch ms"
                ),
            )

    rows = get_usage_by_tag(user["id"], tag=tag, since_iso=since_iso)
    if rows is None:
        raise HTTPException(status_code=503, detail="Usage rollup unavailable")

    payload = {
        "usage_export": "1",
        "issuer": "gatewayz.ai",
        "subject": f"user/{user['id']}",
        "tag": tag,
        "since": since_iso,
        "generated": datetime.now(UTC).isoformat(),
        "measured": bool(rows),
        "data": rows,
    }

    try:
        body, envelope = sign_usage(payload)
    except SigningUnavailable as e:
        # Never an unsigned export dressed as a signed one: the consumer's
        # verification step would silently become a no-op.
        raise HTTPException(status_code=503, detail=f"Signing unavailable: {e}")

    return Response(
        content=body,
        media_type="application/json",
        headers={
            "x-gatewayz-signature": envelope["signature"],
            "x-gatewayz-key-id": envelope["key_id"],
            "x-gatewayz-alg": envelope["alg"],
            "access-control-expose-headers": (
                "x-gatewayz-signature, x-gatewayz-key-id, x-gatewayz-alg"
            ),
        },
    )


@router.get("/v1/usage/export/key", tags=["authentication"])
async def usage_export_public_key():
    """The verifying key for /v1/usage/export. Public by design, no auth.

    A signed feed whose key needs a credential to fetch cannot be verified by
    the third party the signature exists for.
    """
    try:
        return {"alg": USAGE_ALG, "key_id": usage_key_id(), "public_key": usage_public_key_b64()}
    except SigningUnavailable as e:
        raise HTTPException(status_code=503, detail=f"Signing unavailable: {e}")
