"""Discount credits at billing time.

An account can hold a balance of discount credits. They come only from the
Gatewayz vault: the vault mirror job grants them from on-chain mints (see
src/db/inference_discount.py::grant_discount). Each billed request then pays
(1 - d) x list price from the paid balance and d x list price is drawn from
the discount credits balance until it runs out, d = VAULT_DISCOUNT_PERCENT.
1 discount credit = $1 of list price.

Billing reaches this from src/db/users.py::deduct_credits (the draw), the
pre-request sufficiency checks (the hold) and refunds (the reversal), and the
rules are:

* **Dark by default.** :func:`discount_enabled` is a pure config read; with
  VAULT_DISCOUNT_BILLING_ENABLED off nothing here touches the database and
  billing is unchanged.
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


def discount_percent() -> Decimal:
    return min(_clamped(Config.VAULT_DISCOUNT_PERCENT, "0.99"), MAX_DISCOUNT_PERCENT)


def discount_enabled() -> bool:
    """True when billing should draw on discount credits balances
    (VAULT_DISCOUNT_BILLING_ENABLED). Config only -- never a DB call."""
    return bool(Config.VAULT_DISCOUNT_BILLING_ENABLED)


def account_in_pilot(user_id: Any) -> bool:
    """True when billing may draw on this account's discount credits: the
    account is named in VAULT_DISCOUNT_PILOT_USER_IDS, or that setting is "*"
    (every account). An empty or invalid setting admits nobody. Config only --
    never a DB call."""
    if Config.VAULT_DISCOUNT_ALL_ACCOUNTS:
        return True
    return (
        isinstance(user_id, int)
        and not isinstance(user_id, bool)
        and user_id in Config.VAULT_DISCOUNT_PILOT_USER_IDS
    )


def discount_enabled_for(user_id: Any) -> bool:
    """The switch and the pilot list together, for one account. This is what
    every billing call site checks, so an account outside the list takes
    exactly the same path as with the switch off."""
    return discount_enabled() and account_in_pilot(user_id)


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
    account is outside the pilot list, when the request has no UUID
    request_id, when there is no balance, and on ANY error (full price).
    Never raises. Never more than trunc(cost x d, 6) with
    d <= MAX_DISCOUNT_PERCENT, so the paid-balance charge is always positive."""
    if not discount_enabled_for(user_id) or cost <= 0:
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
            "discount credits unavailable for user %s; charging full price: %s", user_id, reason
        )
        return Decimal(0)
    ceiling = (cost * percent).quantize(_DISCOUNT_DP, rounding=ROUND_DOWN)
    if applied > ceiling:
        # The SQL never returns more than this; if it somehow did, charge
        # full price rather than trust it, and put back whatever it drew.
        logger.warning("discount credits above its ceiling for user %s; reversing", user_id)
        reverse_for_request(key)
        return Decimal(0)
    return max(applied, Decimal(0)).quantize(_DISCOUNT_DP, rounding=ROUND_DOWN)


def reverse_or_none(request_id: Any) -> Decimal | None:
    """Like :func:`reverse_for_request` but None -- not 0 -- when the reversal
    could not be done, so a refund can tell "nothing was drawn" from "do not
    know"."""
    key = _uuid_or_none(request_id)
    if key is None:
        return Decimal(0)
    from src.db.inference_discount import reverse_discount

    try:
        return reverse_discount(key)
    except Exception as e:  # noqa: BLE001
        logger.warning("discount credits reversal failed: %s", type(e).__name__)
        return None


def charged_credits_for(request_id: Any) -> Decimal | None:
    """The credits a billed request was actually charged (its
    credit_transactions row, keyed by the billing request_id), or None when
    that cannot be established."""
    if _uuid_or_none(request_id) is None:
        return None
    from src.db.credit_transactions import get_transaction_by_request_id

    try:
        row = get_transaction_by_request_id(str(request_id))
        if not isinstance(row, dict):
            return None
        charged = -Decimal(str(row.get("amount")))
    except Exception:  # noqa: BLE001
        return None
    return charged if charged.is_finite() and charged >= 0 else None


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
        logger.warning("discount credits reversal failed: %s", reason)
        return Decimal(0)


def discount_aware_credits(user_id: Any, credits: float, estimate: float, request_id: Any) -> float:
    """The list-price spend `credits` can cover for ONE request once its
    discount is counted, for pre-request sufficiency checks.

    The discount counted is not the balance but a HOLD reserved for this
    request (discount_hold, keyed by its billing request_id): trunc(estimate
    x d, 6), capped at what other in-flight requests have not already held.
    Concurrent pre-checks therefore cannot each count the same balance --
    the sum of live holds never exceeds it -- and deduct_credits settles the
    request against its own hold. With hold H, discount d and credits C a
    request of list price X needs X - min(dX, H) <= C, i.e.
    X <= min(C / (1 - d), C + H), so every request it admits charges at most C
    in credits, exactly like a request without the discount.

    Returns `credits` unchanged -- and makes no DB call -- while the discount
    is disabled, the account is outside the pilot list, or the request has no
    UUID request_id; on any error, too (the check then behaves exactly as
    before)."""
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return credits
    if not discount_enabled_for(user_id):
        return credits
    percent = discount_percent()
    key = _uuid_or_none(request_id)
    if percent <= 0 or credits <= 0 or key is None:
        return credits
    try:
        cost = Decimal(str(estimate))
    except (InvalidOperation, TypeError, ValueError):
        return credits
    if not cost.is_finite() or cost <= 0:
        return credits
    from src.db.inference_discount import hold_discount

    try:
        held = hold_discount(user_id, cost, percent, key, hold_ttl_seconds())
    except Exception as e:  # noqa: BLE001 - no hold -> the check counts credits only
        logger.warning("discount credits hold unavailable for user %s: %s", user_id, e)
        return credits
    if held <= 0:
        return credits
    c = Decimal(str(credits))
    # percent <= MAX_DISCOUNT_PERCENT < 1, so the division is always defined.
    capacity = min(c + held, c / (Decimal(1) - percent))
    return float(max(capacity, c))


def hold_ttl_seconds() -> int:
    try:
        ttl = int(Config.VAULT_DISCOUNT_HOLD_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = 900
    return min(max(ttl, 60), 3600)


async def discount_aware_credits_async(
    user_id: Any, credits: float, estimate: float, request_id: Any
) -> float:
    """:func:`discount_aware_credits` for async request paths: returns
    `credits` immediately (no thread hop, no DB call) while the discount is
    disabled or the account is outside the pilot list, else places the hold
    off the event loop."""
    if not discount_enabled_for(user_id):
        return credits
    import asyncio

    return await asyncio.to_thread(discount_aware_credits, user_id, credits, estimate, request_id)
