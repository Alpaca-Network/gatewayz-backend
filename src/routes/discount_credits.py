"""Discount credits: the caller's balance and recent movements.

``GET /discount-credits`` is the read side of the discount credits balance
that billing draws on (src/services/discount_credits/billing.py). Discount
credits are granted only from the Gatewayz vault; there is no route that
grants or adjusts them.

Numbers are strings (6 dp), so nothing is lost to a float on the way out.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends

from src.db.inference_discount import get_balance, list_ledger
from src.security.deps import get_current_user
from src.services.discount_credits.billing import discount_enabled, discount_percent

logger = logging.getLogger(__name__)

router = APIRouter()

DISCLAIMER = (
    "Discount credits come only from yield the Gatewayz vault actually receives, can change "
    "at any time, and are not a guaranteed amount."
)

_DISCOUNT_HISTORY_LIMIT = 50


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


def _discount_block(user_id: int) -> dict[str, Any]:
    """The `discount` and `discount_history` keys. No DB read while discount
    credits billing is off -- zeros and an empty list, same shape."""
    config = {
        "enabled": discount_enabled(),
        "discount_percent": _num(discount_percent()),
        # Deprecated: a field of the retired program, kept (always "0") so the
        # shape the app already parses is unchanged.
        "reward_share": "0",
    }
    if not discount_enabled():
        return {"discount": {**config, **_balance_view(None)}, "discount_history": []}
    return {
        "discount": {**config, **_balance_view(get_balance(user_id))},
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
