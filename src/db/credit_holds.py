"""DB access for credit holds and unpaid balances -- credit_holds and
credit_debts (supabase/migrations/20261126130000_credit_holds.sql).

Every change goes through one of the SQL functions (credit_hold,
credit_release, charge_with_debt), each of which takes the account's users
row lock and is idempotent on the billing request_id. The application calls
them only through src/services/billing/credit_holds.py, which checks the
CREDIT_HOLDS_ENABLED / CREDIT_DEBT_ENABLED switches first.

Every function here RAISES :class:`CreditHoldUnavailableError` on failure,
so a caller can tell "refused" from "could not ask".
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_DEBTS_TABLE = "credit_debts"


class CreditHoldUnavailableError(Exception):
    """A credit hold / debt call failed or returned something unreadable. The
    message names the operation and the exception class only -- safe to log."""


def _scalar(data: Any, op: str) -> Decimal:
    """A numeric RPC return as a Decimal (PostgREST may wrap a scalar as [v],
    [{fn: v}] or {fn: v})."""
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and len(data) == 1:
        data = next(iter(data.values()))
    if data is None or isinstance(data, bool):
        raise CreditHoldUnavailableError(f"{op} returned no value")
    try:
        value = Decimal(str(data))
    except (InvalidOperation, TypeError, ValueError):
        raise CreditHoldUnavailableError(f"{op} returned a non-numeric value") from None
    if not value.is_finite() or value < 0:
        raise CreditHoldUnavailableError(f"{op} returned an invalid amount")
    return value


def _payload(data: Any, op: str) -> dict[str, Any]:
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and len(data) == 1 and "success" not in data:
        data = next(iter(data.values()))
    if not isinstance(data, dict):
        raise CreditHoldUnavailableError(f"{op} returned an unreadable result")
    return data


def hold_credits(user_id: int, amount: Decimal, request_id: str, ttl_seconds: int) -> Decimal:
    """Reserve `amount` of paid credits for one request (supabase function
    credit_hold). Returns the amount held, or 0 when the balance less the
    account's other live holds cannot cover it. Idempotent on `request_id`."""
    try:
        result = (
            get_supabase_client()
            .rpc(
                "credit_hold",
                {
                    "p_user_id": user_id,
                    "p_amount": str(amount),
                    "p_request_id": request_id,
                    "p_ttl_seconds": int(ttl_seconds),
                },
            )
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        raise CreditHoldUnavailableError(f"credit_hold failed: {type(e).__name__}") from None
    return _scalar(result.data, "credit_hold")


def release_credits(request_id: str, user_id: int) -> Decimal:
    """Drop this account's hold for a request that ended without being charged
    (supabase function credit_release). Returns the amount the live hold
    reserved, 0 when there was none. Never changes a balance."""
    try:
        result = (
            get_supabase_client()
            .rpc("credit_release", {"p_request_id": request_id, "p_user_id": user_id})
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        raise CreditHoldUnavailableError(f"credit_release failed: {type(e).__name__}") from None
    return _scalar(result.data, "credit_release")


def charge_with_debt(
    user_id: int,
    amount: float,
    transaction_type: str,
    description: str,
    metadata: dict[str, Any] | None,
    request_id: str,
) -> dict[str, Any]:
    """Charge a delivered request: min(amount, balance) is deducted and the
    rest recorded as an unpaid balance (supabase function charge_with_debt).
    Idempotent on `request_id`. Returns the function's result
    (``charged_usd``, ``debt_usd``, ``idempotent``, ...); raises when the
    call fails or the function reports an error."""
    try:
        result = (
            get_supabase_client()
            .rpc(
                "charge_with_debt",
                {
                    "p_user_id": user_id,
                    "p_amount": float(amount),
                    "p_transaction_type": transaction_type,
                    "p_description": description,
                    "p_metadata": metadata or {},
                    "p_request_id": str(request_id),
                },
            )
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        raise CreditHoldUnavailableError(f"charge_with_debt failed: {type(e).__name__}") from None
    data = _payload(result.data, "charge_with_debt")
    if data.get("success") is not True:
        raise CreditHoldUnavailableError(
            f"charge_with_debt refused: {str(data.get('error') or 'unknown')[:120]}"
        )
    return data


def has_unpaid_debt(user_id: int) -> bool:
    """True when the account has at least one unsettled credit_debts row."""
    try:
        result = (
            get_supabase_client()
            .table(_DEBTS_TABLE)
            .select("id")
            .eq("user_id", user_id)
            .is_("settled_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        raise CreditHoldUnavailableError(
            f"credit_debts lookup failed: {type(e).__name__}"
        ) from None
    return bool(result.data)
