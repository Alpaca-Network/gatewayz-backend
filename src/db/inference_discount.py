"""DB access for the discount credits balance -- inference_discount_balances,
inference_discount_ledger and inference_discount_holds (supabase/migrations/
20261011120000_delegation_reward_share_discount.sql; the tables and SQL
functions keep their historical names).

Every balance change goes through one of the SQL functions (discount_grant,
discount_hold, discount_apply, discount_reverse), each of which takes the
account's balance row lock and is idempotent on a UUID request_id. Nothing
here reads a balance and writes it back.

Two conventions, chosen per caller:

* the billing calls (:func:`hold_discount`, :func:`apply_discount`,
  :func:`reverse_discount`) RAISE
  :class:`DiscountUnavailableError` on any failure. Billing must know the
  call failed so it charges full price (fail open to full price); a silent 0
  would be indistinguishable from "no balance", which is fine, but a silent
  0 from a call that DID apply would double-charge.
* the reads and :func:`grant_discount` return a safe default (None / empty)
  and log.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_BALANCES_TABLE = "inference_discount_balances"
_LEDGER_TABLE = "inference_discount_ledger"


def _rpc_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and len(data) == 1 and not data.get("status"):
        # Some PostgREST versions wrap a scalar jsonb return as {fn_name: value}.
        data = next(iter(data.values()))
    return data if isinstance(data, dict) else None


class DiscountUnavailableError(Exception):
    """A discount RPC failed or returned something unreadable. The message
    names the operation and the exception class only -- safe to log."""


def _scalar(data: Any) -> Decimal:
    """A numeric RPC return as a Decimal. PostgREST returns a scalar
    function's value bare, but some versions wrap it ([v], [{fn: v}],
    {fn: v}); anything unreadable raises."""
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and len(data) == 1:
        data = next(iter(data.values()))
    if data is None or isinstance(data, bool):
        raise DiscountUnavailableError("discount RPC returned no value")
    try:
        value = Decimal(str(data))
    except (InvalidOperation, TypeError, ValueError):
        raise DiscountUnavailableError("discount RPC returned a non-numeric value") from None
    if not value.is_finite() or value < 0:
        raise DiscountUnavailableError("discount RPC returned an invalid amount")
    return value


def apply_discount(
    user_id: int,
    cost: Decimal,
    discount_percent: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> Decimal:
    """Draw min(cost x d, balance) for one billed request (supabase function
    discount_apply). Idempotent on `request_id`: a repeat returns what the
    first call applied. Raises DiscountUnavailableError on any failure."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "discount_apply",
            {
                "p_user_id": user_id,
                "p_cost": str(cost),
                "p_discount_percent": str(discount_percent),
                "p_request_id": request_id,
                "p_metadata": metadata or {},
            },
        ).execute()
    except Exception as e:  # noqa: BLE001
        raise DiscountUnavailableError(f"discount_apply failed: {type(e).__name__}") from None
    return _scalar(result.data)


def hold_discount(
    user_id: int,
    estimate: Decimal,
    discount_percent: Decimal,
    request_id: str,
    ttl_seconds: int,
) -> Decimal:
    """Reserve, at pre-check time, the discount a request will draw
    (supabase function discount_hold): min(trunc(estimate x d, 6), balance
    minus every other live hold), for `ttl_seconds`. Idempotent on
    `request_id`. Raises DiscountUnavailableError on any failure."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "discount_hold",
            {
                "p_user_id": user_id,
                "p_estimate": str(estimate),
                "p_discount_percent": str(discount_percent),
                "p_request_id": request_id,
                "p_ttl_seconds": int(ttl_seconds),
            },
        ).execute()
    except Exception as e:  # noqa: BLE001
        raise DiscountUnavailableError(f"discount_hold failed: {type(e).__name__}") from None
    return _scalar(result.data)


def reverse_discount(request_id: str) -> Decimal:
    """Put back what a request drew (supabase function discount_reverse);
    returns the amount restored, 0 when nothing was drawn or it was already
    reversed. Raises DiscountUnavailableError on any failure."""
    try:
        client = get_supabase_client()
        result = client.rpc("discount_reverse", {"p_request_id": request_id}).execute()
    except Exception as e:  # noqa: BLE001
        raise DiscountUnavailableError(f"discount_reverse failed: {type(e).__name__}") from None
    return _scalar(result.data)


def grant_discount(
    user_id: int,
    amount_usd: Decimal,
    request_id: str,
    ref: str,
) -> dict[str, Any] | None:
    """Add discount credits to an account (supabase function discount_grant).

    In production, discount credits may only be granted by the vault mirror
    job from on-chain mints. Tests and a developer-run script may call this
    directly.

    Idempotent on `request_id` (a UUID): a repeat adds nothing and returns
    the first grant's amount with status 'exists'. `amount_usd` is in USD of
    list price, truncated to 6 dp by the SQL; a non-positive amount returns
    status 'invalid'. `ref` is a short label stored on the ledger row.
    Returns {status, amount_usd?, balance_after?}; None on failure."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "discount_grant",
            {
                "p_user_id": user_id,
                "p_amount": str(amount_usd),
                "p_request_id": request_id,
                "p_ref": ref,
                "p_metadata": {},
            },
        ).execute()
        return _rpc_payload(result.data)
    except Exception as e:
        logger.warning(f"discount_grant failed for user {user_id}: {type(e).__name__}")
        return None


def get_balance(user_id: int) -> dict[str, Any] | None:
    """The account's balance row, or None when it has none (or on error)."""
    try:
        client = get_supabase_client()
        result = client.table(_BALANCES_TABLE).select("*").eq("user_id", user_id).limit(1).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"inference_discount_balances lookup failed for user {user_id}: {e}")
        return None


def list_ledger(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """The account's most recent balance movements, newest first."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_LEDGER_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"inference_discount_ledger lookup failed for user {user_id}: {e}")
        return []
