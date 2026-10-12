"""DB access for the inference discount -- inference_discount_balances and
inference_discount_ledger (supabase/migrations/
20261011120000_delegation_reward_share_discount.sql).

Every balance change goes through one of the SQL functions (discount_apply,
discount_reverse, discount_adjust; discount_grant via
delegation_grant_attribution), each of which takes the account's balance row
lock and is idempotent on a UUID request_id. Nothing here reads a balance and
writes it back.

Two conventions, chosen per caller:

* the billing calls (:func:`apply_discount`, :func:`reverse_discount`) RAISE
  :class:`DiscountUnavailableError` on any failure. Billing must know the
  call failed so it charges full price (fail open to full price); a silent 0
  would be indistinguishable from "no balance", which is fine, but a silent
  0 from a call that DID apply would double-charge.
* the reads return a safe default (None / empty) and log, like
  src/db/delegation.py.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.db.delegation import _rpc_payload, _select_all

logger = logging.getLogger(__name__)

_BALANCES_TABLE = "inference_discount_balances"
_LEDGER_TABLE = "inference_discount_ledger"


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


def adjust_discount(
    user_id: int,
    amount: Decimal,
    request_id: str,
    reason: str,
    admin_user_id: int | None,
) -> dict[str, Any] | None:
    """An admin correction (supabase function discount_adjust): signed,
    never below zero, idempotent on `request_id`. Returns {status,
    amount_usd?, balance_after?}; None on failure."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "discount_adjust",
            {
                "p_user_id": user_id,
                "p_amount": str(amount),
                "p_request_id": request_id,
                "p_reason": reason,
                "p_admin_user_id": admin_user_id,
            },
        ).execute()
        return _rpc_payload(result.data)
    except Exception as e:
        logger.warning(f"discount_adjust failed for user {user_id}: {type(e).__name__}")
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


def list_balances() -> list[dict[str, Any]] | None:
    """Every balance row. None on a failed read, so a total can never be
    reported from a partial listing."""
    try:
        return _select_all(lambda c: c.table(_BALANCES_TABLE).select("*").order("user_id"))
    except Exception as e:
        logger.warning(f"inference_discount_balances listing failed: {e}")
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
