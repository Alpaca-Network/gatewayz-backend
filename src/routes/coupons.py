"""User-facing coupon redemption API.

#2341 built the whole ``/admin/coupons`` CRUD surface, but nothing could
*redeem* a coupon: the 2025 migration's ``is_coupon_redeemable()`` and
``get_available_coupons()`` had no callers, src/main.py recorded the
user-facing coupons route as cut ("growth-mechanics cut"), and no Python
anywhere incremented ``coupons.times_used`` or wrote ``coupon_redemptions``.
The admin panel managed coupons nobody could use and
``average_redemption_value`` read 0.00 by construction. This module is the
missing half.

Where the invariants live
=========================
All of them are enforced in Postgres, inside ``public.redeem_coupon()``
(supabase/migrations/20260917000000_add_redeem_coupon_rpc.sql), under a
``SELECT ... FOR UPDATE`` on the coupon row. Nothing in this file decides
whether a coupon may be redeemed -- it authenticates the caller, normalises
the code, calls the RPC once, and translates the verdict. That split is the
point: a check in Python and a write in Postgres is the TOCTOU window that
lets a max_uses=1 coupon pay out twice.

Failure reasons
===============
Each refusal carries its own ``error.code`` and its own HTTP status --
expired, exhausted, wrong account and already-redeemed are four different
answers, not one "invalid coupon". Collapsing them is the same class of
masking the Sep 15 phantom-column work spent the week removing, and it is
also a support cost: "invalid coupon" for an already-redeemed code generates
a ticket that the true reason would not.

Known tradeoff: ``COUPON_NOT_ASSIGNED``/403 tells the caller that the code
they typed is real but belongs to someone else, where ``COUPON_NOT_FOUND``/404
would not. That is an enumeration oracle, and it is accepted deliberately --
a user who was sent a coupon and mistypes the account it was issued to needs
to be told that, and "invalid coupon" would leave them retrying a code that
will never work for them. The compensating control is the rate limit below,
not a vaguer error.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.db.audit import record_audit
from src.db.coupon_redemptions import RedemptionUnavailable, redeem_coupon
from src.schemas.coupons import CouponValidationError, normalize_code
from src.security.deps import get_current_user
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# 5/60: the same budget src/routes/faucet.py gives its claim endpoint, and a
# twelfth of the 60/60 used for ordinary reads. A coupon code is a bearer
# secret in a small alphabet, so an unthrottled redeem endpoint is a
# code-guessing oracle that pays out; 5 per minute is still far above any
# honest use, because a person types one code, once.
coupon_redeem_rl = create_endpoint_rate_limit("coupon_redeem", max_requests=5, window_seconds=60)

# error_code from redeem_coupon() -> (HTTP status, error.type, error.code).
#
# Distinct statuses on purpose: a client that only reads the status still
# distinguishes "not yours" (403) from "used up" (409) from "expired" (410),
# and error.code carries the full reason for one that reads the body.
_REFUSAL_RESPONSES: dict[str, tuple[int, str, str]] = {
    "COUPON_NOT_FOUND": (404, "not_found_error", "coupon_not_found"),
    "COUPON_NOT_ASSIGNED": (403, "permission_error", "coupon_not_assigned"),
    "COUPON_INACTIVE": (409, "conflict_error", "coupon_inactive"),
    "COUPON_NOT_YET_ACTIVE": (409, "conflict_error", "coupon_not_yet_active"),
    "ALREADY_REDEEMED": (409, "conflict_error", "coupon_already_redeemed"),
    "MAX_USES_EXCEEDED": (409, "conflict_error", "coupon_max_uses_exceeded"),
    "COUPON_EXPIRED": (410, "conflict_error", "coupon_expired"),
    # Not a refusal the user can act on: we do not know what happened, so it
    # must not read as "your code is bad".
    "REDEMPTION_FAILED": (503, "service_unavailable", "coupon_redemption_failed"),
    "USER_NOT_FOUND": (503, "service_unavailable", "coupon_redemption_failed"),
}

_GENERIC_UNAVAILABLE = "Could not redeem this coupon right now. Please try again."

# coupon_redemptions.user_agent is unbounded TEXT, so an 8KB header (uvicorn's
# own ceiling) would be stored whole. Bound it here: the column is a fraud
# signal, not a log, and the RPC already does the same for ip_address via
# LEFT(p_ip_address, 45) against its VARCHAR(45).
_MAX_USER_AGENT_LENGTH = 512


class RedeemCouponRequest(BaseModel):
    """POST /coupons/redeem body.

    ``extra="forbid"``: the amount granted comes from the coupon row and
    nothing else, so a caller sending ``value_usd`` or ``user_id`` gets a loud
    422 rather than having it silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=50)


def _error(status: int, message: str, code: str, error_type: str) -> HTTPException:
    """The repo's structured error envelope (matches src/routes/admin_coupons.py)."""
    return HTTPException(
        status_code=status,
        detail={"error": {"message": message, "type": error_type, "code": code}},
    )


def _client_ip(request: Request) -> str | None:
    """First hop of X-Forwarded-For, else the direct peer.

    Same convention as src/db/audit.py and the security middleware: Railway
    sits in front of this service, so request.client.host alone is the proxy.
    """
    try:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip() or None
        return request.client.host if request.client else None
    except Exception:
        return None


def _user_agent(request: Request) -> str | None:
    """The caller's User-Agent, bounded, with an absent or empty header as None."""
    raw = request.headers.get("User-Agent")
    return raw[:_MAX_USER_AGENT_LENGTH] if raw else None


@router.post("/coupons/redeem", tags=["coupons"])
async def redeem_coupon_endpoint(
    body: RedeemCouponRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    _rl: None = Depends(coupon_redeem_rl),
) -> dict[str, Any]:
    """Redeem a coupon code for credits on the authenticated user's account.

    The credits land in ``purchased_credits`` (they must survive a
    subscription cancellation), and every success writes three rows in one
    transaction: the ``coupon_redemptions`` ledger row, the
    ``credit_transactions`` row, and the ``coupons.times_used`` increment.
    """
    user_id = user.get("id")
    if user_id is None:
        # get_current_user returns a real `users` row; no id means the auth
        # layer handed us something unexpected, not that the user is unknown.
        logger.error("coupon redemption: authenticated principal has no id")
        raise _error(503, _GENERIC_UNAVAILABLE, "coupon_redemption_failed", "service_unavailable")

    try:
        code = normalize_code(body.code)
    except CouponValidationError as e:
        # A malformed code (whitespace, punctuation, over-long) can never match
        # a stored coupon, so this is a 422 about the input rather than a 404
        # about the catalog -- and it never reaches the database.
        raise _error(422, str(e), e.code, "invalid_request_error")

    try:
        verdict = redeem_coupon(
            code=code,
            user_id=int(user_id),
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except RedemptionUnavailable as e:
        # We do not know whether anything was written. Answering "invalid
        # coupon" here would tell a user their good code is bad and burn it in
        # their mind; 503 says "retry", which is true and safe -- the RPC is
        # idempotent per (coupon, user), so the retry cannot double-grant.
        logger.error("coupon redemption unavailable for user %s: %s", user_id, e)
        raise _error(503, _GENERIC_UNAVAILABLE, "coupon_redemption_failed", "service_unavailable")

    if not verdict.get("success"):
        error_code = verdict.get("error_code")
        status, error_type, api_code = _REFUSAL_RESPONSES[error_code]
        # 5xx reasons carry an internal message; never echo it to the caller.
        message = (
            _GENERIC_UNAVAILABLE
            if status >= 500
            else (verdict.get("error_message") or "This coupon could not be redeemed.")
        )

        record_audit(
            user,
            action="coupon.redemption_refused",
            target_type="coupon",
            target_id=verdict.get("coupon_id"),
            request=request,
            metadata={"code": code, "reason": error_code},
        )
        raise _error(status, message, api_code, error_type)

    record_audit(
        user,
        action="coupon.redeemed",
        target_type="coupon",
        target_id=verdict.get("coupon_id"),
        request=request,
        metadata={
            "code": verdict.get("code"),
            "value_applied": float(verdict.get("value_applied") or 0),
            "redemption_id": verdict.get("redemption_id"),
            "transaction_id": verdict.get("transaction_id"),
        },
    )

    return {
        "success": True,
        "coupon_id": verdict.get("coupon_id"),
        "code": verdict.get("code"),
        "value_applied": float(verdict.get("value_applied") or 0),
        "balance_before": float(verdict.get("balance_before") or 0),
        "balance_after": float(verdict.get("balance_after") or 0),
        "redemption_id": verdict.get("redemption_id"),
        "transaction_id": verdict.get("transaction_id"),
    }
