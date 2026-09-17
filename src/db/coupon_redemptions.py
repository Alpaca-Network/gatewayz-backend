"""Coupon redemption write path (src/routes/coupons.py).

Everything that grants the money happens inside ONE database call:
``public.redeem_coupon()`` (supabase/migrations/20260917010000_add_redeem_coupon_rpc.sql).
This module is the thin, deliberately dumb wrapper around it.

**There is no Python fallback, on purpose.** ``src/db/users.py``'s
``_atomic_add_credits_rpc`` degrades to a read-modify-write when the RPC is
unavailable, which is right for a grant that a human already authorised. It is
wrong here. The whole reason this path is an RPC is that the check and the
increment cannot be split without a TOCTOU window -- a "fallback" would be the
exact race the RPC exists to close, re-entered at the worst possible moment
(the RPC missing usually means a migration has not been applied, i.e. the
schema is not what the code thinks). A failed redemption costs a user one
retry. A double redemption costs money and has to be reconciled by hand.

The reads in this module raise rather than returning an empty/zero result, per
"Gatewayz - Phantom Column Failures, Admin Dashboard Batch 1 (Sep 15, 2026)":
a broken query and "nothing to report" must never render as the same thing.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

# Every error_code redeem_coupon() can return, mirroring the vocabulary in
# is_coupon_redeemable() from the 2025 coupon migration. Kept as an explicit
# set so an unrecognised code from a newer/older deployed RPC is caught here
# rather than silently mapping to a 200.
REDEMPTION_ERROR_CODES = frozenset(
    {
        "COUPON_NOT_FOUND",
        "COUPON_INACTIVE",
        "COUPON_NOT_YET_ACTIVE",
        "COUPON_EXPIRED",
        "COUPON_NOT_ASSIGNED",
        "MAX_USES_EXCEEDED",
        "ALREADY_REDEEMED",
        "USER_NOT_FOUND",
        "REDEMPTION_FAILED",
    }
)

# Redemption ledger columns, explicit rather than "*" -- an explicit list is
# what makes a dropped column fail in review instead of at runtime. Verified
# against prod (ynleroehyrmaafkgjgmr) on 2026-09-16 via PostgREST's OpenAPI
# document, not transcribed from the migration file.
REDEMPTION_COLUMNS = (
    "id, coupon_id, user_id, redeemed_at, value_applied, " "user_balance_before, user_balance_after"
)


class RedemptionUnavailable(RuntimeError):
    """The redemption RPC could not be reached, or answered unintelligibly.

    Distinct from a *refused* redemption, which is a successful call carrying
    an error_code. This one means we do not know whether anything happened, so
    the route must answer 503 and the caller must retry -- never "invalid
    coupon", which would tell a user their good code is bad.
    """


def redeem_coupon(
    *,
    code: str,
    user_id: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Redeem ``code`` for ``user_id``, atomically.

    Args:
        code: The coupon code as typed. Matched case-insensitively by the RPC.
        user_id: ``users.id`` of the redeeming account.
        ip_address: Client IP, stored on the redemption row for fraud review.
        user_agent: Client user-agent, likewise.

    Returns:
        The RPC's verdict dict. ``{"success": True, ...}`` with ``coupon_id``,
        ``code``, ``value_applied``, ``balance_before``, ``balance_after``,
        ``redemption_id`` and ``transaction_id`` on a real grant; otherwise
        ``{"success": False, "error_code": ..., "error_message": ...}`` with a
        code from REDEMPTION_ERROR_CODES.

    Raises:
        RedemptionUnavailable: the RPC is unreachable, undeployed, or returned
            a shape this code does not recognise. Never swallowed into a
            refusal.
    """
    params = {
        "p_coupon_code": code,
        "p_user_id": int(user_id),
        "p_ip_address": ip_address,
        "p_user_agent": user_agent,
    }

    try:
        client = get_supabase_client()
        result = client.rpc("redeem_coupon", params).execute()
    except Exception as e:
        # Includes PGRST202 (function not found) -- see the module docstring for
        # why that is a 503 and not a quiet fallback.
        logger.error("redeem_coupon RPC failed for user %s: %s", user_id, e, exc_info=True)
        raise RedemptionUnavailable("redeem_coupon RPC call failed") from e

    data = result.data
    # A scalar-returning Postgres function comes back bare through PostgREST,
    # but the client has historically wrapped single rows in a list; accept both
    # rather than depending on which.
    if isinstance(data, list):
        data = data[0] if data else None

    if not isinstance(data, dict):
        logger.error("redeem_coupon RPC returned unexpected payload type %s", type(data).__name__)
        raise RedemptionUnavailable("redeem_coupon RPC returned an unexpected payload")

    if data.get("success") is True:
        return data

    error_code = data.get("error_code")
    if error_code not in REDEMPTION_ERROR_CODES:
        # An unknown code means the deployed function is not the one this code
        # was written against. Refusing on an unrecognised verdict is safe;
        # treating it as a success would not be.
        logger.error("redeem_coupon RPC returned unknown error_code %r", error_code)
        raise RedemptionUnavailable(f"redeem_coupon returned unknown error_code {error_code!r}")

    # `debug` is for the server log only -- it can carry SQLSTATE/SQLERRM text.
    debug = data.get("debug")
    if debug:
        logger.warning(
            "coupon redemption refused (user=%s, code=%s): %s", user_id, error_code, debug
        )

    return data


def get_user_redemptions(user_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """A user's own redemption history, newest first.

    Raises:
        Exception: any database failure, deliberately unhandled -- an empty
        history and a broken query must stay distinguishable.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table("coupon_redemptions")
            .select(REDEMPTION_COLUMNS)
            .eq("user_id", user_id)
            .order("redeemed_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        logger.error("get_user_redemptions(%s) failed", user_id, exc_info=True)
        raise
    return result.data or []
