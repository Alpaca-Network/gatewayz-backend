"""Shared pre-check + post-call charge helpers for flat/usage-priced routes.

Used by routes that are not token-metered chat (embeddings, paid /tools). They
follow the same path as chat and audio: ``deduct_credits`` (atomic RPC, keyed on
the server-minted billing ref so a retry cannot double-charge) and
``record_usage``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException

from src.db import users as users_module

logger = logging.getLogger(__name__)


def user_balance(user: dict[str, Any]) -> float:
    return float(user.get("subscription_allowance") or 0) + float(
        user.get("purchased_credits") or 0
    )


async def precheck_credits(api_key: str, estimated_cost: float) -> dict[str, Any]:
    """Return the user, or raise 401 (unknown key) / 402 (cannot afford).

    Requires a strictly positive balance that covers ``estimated_cost``.
    """
    user = await asyncio.to_thread(users_module.get_user, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    balance = user_balance(user)
    if balance <= 0 or balance < estimated_cost:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient credits. Estimated cost: ${estimated_cost:.6f}, "
                f"available: ${max(balance, 0.0):.6f}"
            ),
        )
    return user


async def charge_credits(
    *,
    api_key: str,
    user: dict[str, Any],
    cost: float,
    description: str,
    model: str,
    tokens: int,
    billing_ref: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Deduct ``cost`` (idempotent on ``billing_ref``) and record usage.

    Fails closed: insufficient credits -> 402, unexpected billing error -> 500.
    """
    try:
        await asyncio.to_thread(
            users_module.deduct_credits,
            api_key,
            cost,
            description,
            {**(metadata or {}), "model": model, "cost_usd": cost, "request_id": billing_ref},
            billing_ref,
        )
        await asyncio.to_thread(
            users_module.record_usage, user["id"], api_key, model, max(1, int(tokens)), cost
        )
    except ValueError as e:
        logger.error("[%s] Credit deduction failed: %s", billing_ref, e)
        raise HTTPException(status_code=402, detail=f"Payment required: {e}") from e
    except Exception as e:
        logger.error("[%s] Billing error: %s", billing_ref, e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Billing error occurred. Please try again."
        ) from e
