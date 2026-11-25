"""Discount credits: the caller's balance and recent movements.

``GET /discount-credits`` is the read side of the discount credits balance
that billing draws on (src/services/discount_credits/billing.py). Discount
credits are granted only from the Gatewayz vault; there is no route that
grants or adjusts them.

``PUT /discount-credits/daily-cap`` lets the account owner set how much of
the balance may be used per UTC day. It changes a limit, never the balance.

Numbers are strings (6 dp), so nothing is lost to a float on the way out.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.db.inference_discount import get_balance, list_ledger, set_daily_cap
from src.security.deps import get_current_user
from src.services.discount_credits.billing import discount_enabled_for, discount_percent

logger = logging.getLogger(__name__)

router = APIRouter()

DISCLAIMER = (
    "Discount credits come only from yield the Gatewayz vault actually receives, can change "
    "at any time, and are not a guaranteed amount."
)

_DISCOUNT_HISTORY_LIMIT = 50
# The cap an account has until it sets one (the column default in
# supabase/migrations/20261125120000_discount_daily_cap.sql).
DEFAULT_DAILY_CAP_USD = Decimal("50")
# Same bound as the SQL function discount_set_daily_cap.
MAX_DAILY_CAP_USD = Decimal("1000000")


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _num(value: Any) -> str:
    return format(_decimal(value), "f")


def _usd(value: Any) -> str:
    """A discount credits amount (6 dp) as a string."""
    return format(_decimal(value).quantize(Decimal("0.000001")), "f")


# User-facing ledger refs: the stored ref of a use/reverse/adjust row is a
# billing request id or free text -- neither is shown.
_USER_REF = {"use": "inference", "reverse": "refund", "adjust": "adjustment"}


def _history_item(row: dict[str, Any]) -> dict[str, Any]:
    kind = str(row.get("kind") or "")
    return {
        "created_at": row.get("created_at"),
        "kind": kind,
        "amount_usd": _usd(row.get("amount_usd")),
        "balance_after": _usd(row.get("balance_after")),
        "ref": row.get("ref") if kind == "grant" else _USER_REF.get(kind, kind),
    }


def _balance_view(row: dict[str, Any] | None) -> dict[str, str]:
    row = row or {}
    return {
        "balance_usd": _usd(row.get("balance_usd")),
        "lifetime_granted_usd": _usd(row.get("lifetime_granted_usd")),
        "lifetime_used_usd": _usd(row.get("lifetime_used_usd")),
    }


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _cap_view(row: dict[str, Any] | None, today: date | None = None) -> dict[str, Any]:
    """The daily cap as the account should see it right now.

    The stored row is brought up to date only when billing next touches it,
    so this applies the same two rules while reading: use counted on an
    earlier day reads as 0, and a raise whose date has come is in force."""
    row = row or {}
    today = today or datetime.now(UTC).date()
    cap = _decimal(row.get("daily_cap_usd", DEFAULT_DAILY_CAP_USD))
    next_cap, next_from = row.get("next_daily_cap_usd"), _as_date(row.get("next_daily_cap_from"))
    if next_cap is not None and next_from is not None and next_from <= today:
        cap, next_cap, next_from = _decimal(next_cap), None, None

    cap_day = _as_date(row.get("cap_day"))
    used = _decimal(row.get("cap_used_usd")) if cap_day == today else Decimal(0)
    pending = next_cap is not None and next_from is not None
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return {
        "daily_cap_usd": _usd(cap),
        "used_today_usd": _usd(used),
        "left_today_usd": _usd(max(cap - used, Decimal(0))),
        "resets_at": tomorrow.isoformat().replace("+00:00", "Z"),
        # A raise the account asked for that is not in force yet, or null.
        "next_daily_cap_usd": _usd(next_cap) if pending else None,
        "next_daily_cap_from": next_from.isoformat() if pending else None,
    }


_NO_CAP = {
    "daily_cap_usd": None,
    "used_today_usd": "0.000000",
    "left_today_usd": "0.000000",
    "resets_at": None,
    "next_daily_cap_usd": None,
    "next_daily_cap_from": None,
}


def _discount_block(user_id: int) -> dict[str, Any]:
    """The `discount` and `discount_history` keys. No DB read while discount
    credits billing is off or the account is outside the pilot list -- zeros
    and an empty list, same shape."""
    enabled = discount_enabled_for(user_id)
    config = {
        "enabled": enabled,
        # The fraction (for example "0.99") of a request's list price that
        # discount credits pay. Shown only to accounts it applies to.
        "discount_percent": _num(discount_percent()) if enabled else None,
        # Deprecated: a field of the retired program, kept (always "0") so the
        # shape the app already parses is unchanged.
        "reward_share": "0",
    }
    if not enabled:
        return {
            "discount": {**config, **_balance_view(None), "daily_cap": dict(_NO_CAP)},
            "discount_history": [],
        }
    row = get_balance(user_id)
    return {
        "discount": {**config, **_balance_view(row), "daily_cap": _cap_view(row)},
        "discount_history": [
            _history_item(r) for r in list_ledger(user_id, limit=_DISCOUNT_HISTORY_LIMIT)
        ],
    }


@router.get("/discount-credits", tags=["discount_credits"])
async def get_discount_credits(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller's discount credits: whether billing draws on them, the part
    of each request they pay, the balance and lifetime totals, and the most
    recent movements."""
    return {
        "success": True,
        "data": {**_discount_block(user["id"]), "disclaimer": DISCLAIMER},
    }


class DailyCapRequest(BaseModel):
    """The most discount credits (USD) the account may use per UTC day."""

    daily_cap_usd: Decimal = Field(ge=0, le=MAX_DAILY_CAP_USD, max_digits=13, decimal_places=6)


@router.put("/discount-credits/daily-cap", tags=["discount_credits"])
async def put_daily_cap(
    body: DailyCapRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Set the caller's daily cap on discount credits use.

    Lowering the cap applies at once. Raising it applies from the next UTC
    day (`status: "scheduled"`). 0 stops discount credits from being used.
    Reaching the cap never fails a request: the rest is billed as usual."""
    user_id = user["id"]
    if not discount_enabled_for(user_id):
        raise HTTPException(status_code=404, detail="discount_credits_not_enabled")

    result = set_daily_cap(user_id, body.daily_cap_usd)
    if result is None:
        raise HTTPException(status_code=503, detail="discount_credits_unavailable")
    if result.get("status") not in ("set", "scheduled"):
        raise HTTPException(status_code=422, detail="invalid_daily_cap")
    return {
        "success": True,
        "data": {"status": result["status"], "daily_cap": _cap_view(get_balance(user_id))},
    }
