"""The inference discount at billing time (delegated staking v3).

A delegated-staking account accrues a discount balance -- DELEGATION_REWARD_SHARE
of the staking revenue its stake actually earned us (attribution.py). Each
billed request then pays (1 - d) x list price in credits and d x list price
is drawn from that balance until it runs out, d = DELEGATION_DISCOUNT_PERCENT.
1 discount unit = $1 of list price.

Billing reaches this from exactly one place, src/db/users.py::deduct_credits,
and the rules there are:

* **Dark by default.** :func:`discount_enabled` is a pure config read; with
  DELEGATION_DISCOUNT_ENABLED or DELEGATED_STAKING_ENABLED off nothing here
  touches the database and billing is unchanged.
* **Fail open to full price.** Any discount error -> 0 applied -> the user is
  charged the full list price and the request never fails because of the
  discount.
* **Idempotent on the billing request_id.** discount_apply returns the
  original amount on a repeat, so a retried deduction charges the same
  (cost - applied) and never draws twice. A request without a UUID
  request_id gets no discount (there is nothing to make it idempotent on).
"""

from __future__ import annotations

import logging
import uuid
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from src.config.config import Config

logger = logging.getLogger(__name__)

PAYOUT_MODELS = ("reward_share", "allowance")
_DISCOUNT_DP = Decimal("0.000001")
# d is capped below 1 so every billed request still pays something in credits
# -- which writes its credit_transactions row (the retry guard and the usage
# record) and goes through the daily usage limit. d = 1 would make a request
# free, unrecorded and unlimited while the balance lasts. The SQL
# (discount_apply) applies the same cap.
MAX_DISCOUNT_PERCENT = Decimal("0.99")


def _clamped(value: Any, default: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        d = Decimal(default)
    if not d.is_finite():
        d = Decimal(default)
    return min(max(d, Decimal(0)), Decimal(1))


def payout_model() -> str:
    """'reward_share' (default) or 'allowance'. Anything unrecognised is
    reward_share -- the model that never pays more than we received."""
    model = str(Config.DELEGATION_PAYOUT_MODEL or "").strip().lower()
    return model if model in PAYOUT_MODELS else "reward_share"


def reward_share() -> Decimal:
    return _clamped(Config.DELEGATION_REWARD_SHARE, "0.95")


def discount_percent() -> Decimal:
    return min(_clamped(Config.DELEGATION_DISCOUNT_PERCENT, "0.99"), MAX_DISCOUNT_PERCENT)


def discount_enabled() -> bool:
    """True when billing should draw on discount balances. Config only --
    never a DB call."""
    return bool(Config.DELEGATED_STAKING_ENABLED) and bool(Config.DELEGATION_DISCOUNT_ENABLED)


def _uuid_or_none(value: Any) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def apply_for_request(
    user_id: int, cost: Decimal, request_id: Any, metadata: dict[str, Any] | None = None
) -> Decimal:
    """The discount drawn for one billed request -- 0 when disabled, when the
    request has no UUID request_id, when there is no balance, and on ANY
    error (full price). Never raises. Never more than trunc(cost x d, 6) with
    d <= MAX_DISCOUNT_PERCENT, so the credit charge is always positive."""
    if not discount_enabled() or cost <= 0:
        return Decimal(0)
    percent = discount_percent()
    key = _uuid_or_none(request_id)
    if percent <= 0 or key is None:
        return Decimal(0)
    from src.db.inference_discount import DiscountUnavailableError, apply_discount

    try:
        applied = apply_discount(user_id, cost, percent, key, metadata)
    except Exception as e:  # noqa: BLE001 - fail open to full price, always
        # DiscountUnavailableError messages are pre-redacted; anything else is
        # described by class only.
        reason = str(e) if isinstance(e, DiscountUnavailableError) else type(e).__name__
        logger.warning(
            "inference discount unavailable for user %s; charging full price: %s", user_id, reason
        )
        return Decimal(0)
    ceiling = (cost * percent).quantize(_DISCOUNT_DP, rounding=ROUND_DOWN)
    if applied > ceiling:
        # The SQL never returns more than this; if it somehow did, charge
        # full price rather than trust it, and put back whatever it drew.
        logger.warning("inference discount above its ceiling for user %s; reversing", user_id)
        reverse_for_request(key)
        return Decimal(0)
    return max(applied, Decimal(0)).quantize(_DISCOUNT_DP, rounding=ROUND_DOWN)


def reverse_for_request(request_id: Any) -> Decimal:
    """Put back the discount a request drew (it was refused or refunded).
    Returns the amount restored; 0 when there was none or on error. Never
    raises. Runs whenever there might be something to put back, even with the
    flag since switched off."""
    key = _uuid_or_none(request_id)
    if key is None:
        return Decimal(0)
    from src.db.inference_discount import DiscountUnavailableError, reverse_discount

    try:
        return reverse_discount(key)
    except Exception as e:  # noqa: BLE001
        reason = str(e) if isinstance(e, DiscountUnavailableError) else type(e).__name__
        logger.warning("inference discount reversal failed: %s", reason)
        return Decimal(0)


def discount_aware_credits(user_id: Any, credits: float) -> float:
    """The list-price spend `credits` can cover once the discount is counted,
    for pre-request sufficiency checks: with balance B, discount d and
    credits C a request of list price X needs X - min(dX, B) <= C, i.e.
    X <= min(C / (1 - d), C + B). Returns `credits` unchanged -- and makes no
    DB call -- while the discount is disabled; on any error, too (the check
    then behaves exactly as before)."""
    if not discount_enabled() or not isinstance(user_id, int) or isinstance(user_id, bool):
        return credits
    percent = discount_percent()
    if percent <= 0 or credits <= 0:
        return credits
    from src.db.inference_discount import get_balance

    try:
        row = get_balance(user_id)
        balance = Decimal(str((row or {}).get("balance_usd") or 0))
    except Exception:  # noqa: BLE001
        return credits
    if not balance.is_finite() or balance <= 0:
        return credits
    c = Decimal(str(credits))
    # percent <= MAX_DISCOUNT_PERCENT < 1, so the division is always defined
    # and capacity <= 100 x credits: a request it admits always charges a
    # positive (1 - d) share in credits.
    capacity = min(c + balance, c / (Decimal(1) - percent))
    return float(max(capacity, c))


async def discount_aware_credits_async(user_id: Any, credits: float) -> float:
    """:func:`discount_aware_credits` for async request paths: returns
    `credits` immediately (no thread hop, no DB call) while the discount is
    disabled, else runs the balance read off the event loop."""
    if not discount_enabled():
        return credits
    import asyncio

    return await asyncio.to_thread(discount_aware_credits, user_id, credits)
