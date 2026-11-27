"""Payment method required to link a wallet for discount credits.

Discount credits pay at most 99% of a request's list price; the rest is
always charged to the customer (the paid balance). So an account must be able
to pay that part before it links a Cardano wallet (decided 2026-11-24; vault
design "Payment method: required when a wallet is linked").

An account has a payment method when it has either:

* **a paid balance** above zero (subscription allowance + purchased
  credits), the balance billing charges first; or
* **a card on file**: an active Stripe subscription (a Stripe customer and
  subscription id, with ``subscription_status`` "active"). Stripe keeps the
  card that renews it.

Reads only the account row: no call to Stripe. A failed lookup is "unknown"
and linking is refused with 503 (never link on doubt).
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

PAYMENT_METHOD_REQUIRED = "payment_method_required"
PAYMENT_METHOD_UNAVAILABLE = "payment_method_check_unavailable"

_MESSAGE = (
    "Add a payment method before you link a wallet. Discount credits pay at most 99% of "
    "each request; the rest is charged to your paid balance, so you need a paid balance "
    "or an active subscription."
)


def _amount(value: Any) -> Decimal:
    try:
        d = Decimal(str(value)) if value is not None else Decimal(0)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


def has_paid_balance(user: dict[str, Any]) -> bool:
    return (
        _amount(user.get("subscription_allowance")) + _amount(user.get("purchased_credits"))
    ) > 0


def has_card_on_file(user: dict[str, Any]) -> bool:
    return (
        bool(user.get("stripe_customer_id"))
        and bool(user.get("stripe_subscription_id"))
        and user.get("subscription_status") == "active"
    )


def payment_method_status(user_id: int) -> dict[str, bool] | None:
    """{"has_payment_method", "paid_balance", "card"} for the account, or
    None when the account cannot be read."""
    from src.db.users import get_user_by_id

    try:
        user = get_user_by_id(user_id)
    except Exception as e:  # noqa: BLE001 - unknown, refused by the caller
        logger.warning("payment method lookup failed for user %s: %s", user_id, type(e).__name__)
        return None
    if not isinstance(user, dict):
        return None
    paid_balance = has_paid_balance(user)
    card = has_card_on_file(user)
    return {"has_payment_method": paid_balance or card, "paid_balance": paid_balance, "card": card}


def require_payment_method(user_id: int) -> None:
    """Gate for linking a Cardano wallet. Raises 402 payment_method_required
    when the account has neither a paid balance nor a card on file, and 503
    payment_method_check_unavailable when the account cannot be read."""
    status = payment_method_status(user_id)
    if status is None:
        raise HTTPException(status_code=503, detail=PAYMENT_METHOD_UNAVAILABLE)
    if status["has_payment_method"]:
        return
    # Already in the {"error": ...} shape, so the global HTTPException handler
    # (src/utils/error_handlers.py) returns it as-is.
    raise HTTPException(
        status_code=402,
        detail={
            "error": {
                "type": PAYMENT_METHOD_REQUIRED,
                "code": PAYMENT_METHOD_REQUIRED.upper(),
                "message": _MESSAGE,
                "status": 402,
            }
        },
    )
