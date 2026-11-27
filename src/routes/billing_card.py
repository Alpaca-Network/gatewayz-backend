"""Saved card and card auto top-up (src/services/billing/card_auto_topup.py).

* ``GET /user/billing/card`` -- the saved card (brand, last 4, expiry), the
  auto top-up settings, what it charged today and the allowed range.
* ``POST /user/billing/card/setup-session`` -- a Stripe Checkout session in
  setup mode; the app redirects the customer to its ``url``.
* ``POST /user/billing/card/confirm`` -- after Checkout, store the card.
* ``DELETE /user/billing/card`` -- stop using the saved card.
* ``PUT /user/billing/auto-topup`` -- switch auto top-up on or off; amount and
  daily limit.

Everything returns 404 ``card_auto_topup_not_enabled`` while
CARD_AUTO_TOPUP_ENABLED is off or the account is outside
CARD_AUTO_TOPUP_USER_IDS (the GET then reports ``feature_enabled: false``).
Changes need the account's own sign-in key, never an inference API key.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.security.deps import get_account_owner_id, get_current_user
from src.services.billing import card_auto_topup
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing_card"])

card_rl = create_endpoint_rate_limit("billing_card", max_requests=10, window_seconds=60)


def _raise(e: card_auto_topup.CardSetupError) -> None:
    raise HTTPException(status_code=e.status_code, detail=e.code) from e


async def _call(fn, *args, **kwargs) -> Any:
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except card_auto_topup.CardSetupError as e:
        _raise(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("billing card call failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="card_service_unavailable") from e


def _require_feature(user_id: int) -> None:
    if not card_auto_topup.feature_enabled_for(user_id):
        raise HTTPException(status_code=404, detail="card_auto_topup_not_enabled")


@router.get("/user/billing/card")
async def get_card(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"success": True, "data": await _call(card_auto_topup.status_view, user["id"])}


@router.post("/user/billing/card/setup-session")
async def post_setup_session(
    user_id: int = Depends(get_account_owner_id), _rl: None = Depends(card_rl)
) -> dict[str, Any]:
    _require_feature(user_id)
    return {"success": True, "data": await _call(card_auto_topup.create_setup_session, user_id)}


class ConfirmRequest(BaseModel):
    session_id: str = Field(min_length=3, max_length=255)


@router.post("/user/billing/card/confirm")
async def post_confirm(
    body: ConfirmRequest,
    user_id: int = Depends(get_account_owner_id),
    _rl: None = Depends(card_rl),
) -> dict[str, Any]:
    _require_feature(user_id)
    await _call(card_auto_topup.confirm_setup_session, user_id, body.session_id)
    return {"success": True, "data": await _call(card_auto_topup.status_view, user_id)}


@router.delete("/user/billing/card")
async def delete_card(
    user_id: int = Depends(get_account_owner_id), _rl: None = Depends(card_rl)
) -> dict[str, Any]:
    _require_feature(user_id)
    await _call(card_auto_topup.remove_card, user_id)
    return {"success": True, "data": await _call(card_auto_topup.status_view, user_id)}


class AutoTopupRequest(BaseModel):
    enabled: bool
    amount_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    daily_limit_cents: int | None = Field(default=None, ge=0, le=100_000_000)


@router.put("/user/billing/auto-topup")
async def put_auto_topup(
    body: AutoTopupRequest,
    user_id: int = Depends(get_account_owner_id),
    _rl: None = Depends(card_rl),
) -> dict[str, Any]:
    _require_feature(user_id)
    data = await _call(
        card_auto_topup.set_preferences,
        user_id,
        enabled=body.enabled,
        amount_cents=body.amount_cents,
        daily_limit_cents=body.daily_limit_cents,
    )
    return {"success": True, "data": data}
