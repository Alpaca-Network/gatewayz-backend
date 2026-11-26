"""Credit holds and unpaid balances at billing time.

Two switches, both off by default (src/config/config.py):

* **CREDIT_HOLDS_ENABLED** -- the pre-request credit check reserves the
  request's maximum paid cost (after any discount credits) as a hold keyed by
  its billing request_id. A hold is refused when the balance less the
  account's other live holds cannot cover it, and the request then gets the
  usual 402. The charge settles the request's own hold; every exit that does
  not bill releases it; an unreleased hold expires after
  CREDIT_HOLD_TTL_SECONDS. Holds never block the charge of the request that
  placed them.
* **CREDIT_DEBT_ENABLED** -- when the final charge for a delivered response
  exceeds the balance, the balance is charged and the rest is recorded as an
  unpaid balance (credit_debts). An account with an unpaid balance is refused
  new paid requests (402, code ``outstanding_balance``) until credits are
  added; adding credits pays unpaid balances first.

With a switch off its functions here return at once and make no DB call.
Errors never fail a request on their own: a hold or debt lookup that cannot
be made is logged and the request proceeds as it would with the switch off.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.config import Config

logger = logging.getLogger(__name__)

OUTSTANDING_BALANCE_CODE = "outstanding_balance"
OUTSTANDING_BALANCE_MESSAGE = (
    "Your account has an unpaid balance from earlier usage. Add credits to continue."
)
# How long the answer to "does this account have an unpaid balance?" is reused
# per process before the database is asked again.
DEBT_LOOKUP_CACHE_SECONDS = 15.0

_debt_cache: dict[int, tuple[float, bool]] = {}


def holds_enabled() -> bool:
    """CREDIT_HOLDS_ENABLED. Config only -- never a DB call."""
    return bool(getattr(Config, "CREDIT_HOLDS_ENABLED", False))


def debt_enabled() -> bool:
    """CREDIT_DEBT_ENABLED. Config only -- never a DB call."""
    return bool(getattr(Config, "CREDIT_DEBT_ENABLED", False))


def hold_ttl_seconds() -> int:
    try:
        ttl = int(getattr(Config, "CREDIT_HOLD_TTL_SECONDS", 900))
    except (TypeError, ValueError):
        ttl = 900
    return min(max(ttl, 60), 3600)


def _uuid_or_none(value: Any) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _user_id_or_none(user_id: Any) -> int | None:
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        return None
    return user_id


def paid_part(max_cost: float, user_credits: float, credit_capacity: float) -> float:
    """The part of a request's maximum list-price cost paid from credits once
    its discount credits hold is counted.

    ``credit_capacity`` is what discount_aware_credits returned: the
    list-price spend the credits can cover with this request's discount hold
    H, min(C + H, C / (1 - d)). The request draws min(d x cost, H) at
    settlement, and capacity - C equals H or C x d / (1 - d) (the larger of
    which never limits a cost the check admitted), so the paid part is
    cost - min(d x cost, capacity - C). Equal to ``max_cost`` when no
    discount applies."""
    discount_room = max(0.0, float(credit_capacity) - float(user_credits))
    if discount_room <= 0:
        return float(max_cost)
    from src.services.discount_credits.billing import discount_percent

    d = float(discount_percent())
    return max(0.0, float(max_cost) - min(d * float(max_cost), discount_room))


def place_hold(user_id: Any, amount: float, request_id: Any) -> bool:
    """Reserve `amount` of paid credits for one request. True when the
    request may proceed: the hold was placed, there was nothing to hold, the
    switch is off, or the hold could not be placed because of an error
    (logged). False only when the hold was refused for lack of balance."""
    uid = _user_id_or_none(user_id)
    if not holds_enabled() or uid is None:
        return True
    key = _uuid_or_none(request_id)
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return True
    if key is None or not value.is_finite() or value <= 0:
        return True
    from src.db.credit_holds import hold_credits

    try:
        held = hold_credits(uid, value, key, hold_ttl_seconds())
    except Exception as e:  # noqa: BLE001 - never fail a request on a hold error
        logger.warning("credit hold unavailable for user %s: %s", uid, e)
        return True
    return held > 0


async def place_hold_async(user_id: Any, amount: float, request_id: Any) -> bool:
    """:func:`place_hold` off the event loop; returns True at once (no thread
    hop, no DB call) while the switch is off."""
    if not holds_enabled():
        return True
    return await asyncio.to_thread(place_hold, user_id, amount, request_id)


def release_hold(user_id: Any, request_id: Any) -> Decimal:
    """Free the hold a request placed when it ends without being billed.
    Returns the amount released; 0 when there was none, on error, and -- with
    no DB call -- while the switch is off. Never raises; never changes a
    balance; safe after billing (the charge already removed the hold)."""
    uid = _user_id_or_none(user_id)
    key = _uuid_or_none(request_id)
    if not holds_enabled() or uid is None or key is None:
        return Decimal(0)
    from src.db.credit_holds import release_credits

    try:
        return release_credits(key, uid)
    except Exception as e:  # noqa: BLE001 - best effort; the hold still expires
        logger.warning("credit hold release failed for user %s: %s", uid, e)
        return Decimal(0)


async def release_hold_async(user_id: Any, request_id: Any) -> Decimal:
    """:func:`release_hold` for async paths; returns 0 at once while the
    switch is off. Never raises."""
    if not holds_enabled():
        return Decimal(0)
    try:
        return await asyncio.to_thread(release_hold, user_id, request_id)
    except Exception:  # noqa: BLE001 - best effort
        return Decimal(0)


def note_unpaid_debt(user_id: Any) -> None:
    """Remember (in this process) that the account now has an unpaid balance."""
    uid = _user_id_or_none(user_id)
    if uid is not None:
        _debt_cache[uid] = (time.monotonic(), True)


def forget_debt_lookup(user_id: Any) -> None:
    """Drop the cached unpaid-balance answer for an account (credits added)."""
    uid = _user_id_or_none(user_id)
    if uid is not None:
        _debt_cache.pop(uid, None)


def has_unpaid_debt(user_id: Any) -> bool:
    """True when the account has an unpaid balance. False -- with no DB call --
    while CREDIT_DEBT_ENABLED is off, and on a lookup error (logged). Answers
    are reused for DEBT_LOOKUP_CACHE_SECONDS."""
    uid = _user_id_or_none(user_id)
    if not debt_enabled() or uid is None:
        return False
    now = time.monotonic()
    cached = _debt_cache.get(uid)
    if cached is not None and now - cached[0] < DEBT_LOOKUP_CACHE_SECONDS:
        return cached[1]
    from src.db.credit_holds import has_unpaid_debt as _lookup

    try:
        owed = _lookup(uid)
    except Exception as e:  # noqa: BLE001 - never fail a request on a lookup error
        logger.warning("unpaid balance lookup failed for user %s: %s", uid, e)
        return False
    _debt_cache[uid] = (now, owed)
    return owed


async def has_unpaid_debt_async(user_id: Any) -> bool:
    """:func:`has_unpaid_debt` off the event loop; False at once while the
    switch is off."""
    if not debt_enabled():
        return False
    return await asyncio.to_thread(has_unpaid_debt, user_id)


def outstanding_balance_error():
    """402 for an account with an unpaid balance."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=402,
        detail={
            "error": {
                "message": OUTSTANDING_BALANCE_MESSAGE,
                "type": "insufficient_credits",
                "code": OUTSTANDING_BALANCE_CODE,
            }
        },
    )
