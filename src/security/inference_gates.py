"""
Shared admission gates for inference routes.

Centralizes the abuse-control checks that should be applied uniformly across
/v1/chat/completions, /v1/images/generations, /v1/audio/* and any future
inference endpoints. Each gate is a no-op when its corresponding env flag is
disabled so behavior can be tuned without redeploying.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from src.config import Config

logger = logging.getLogger(__name__)


async def enforce_model_pricing_gate(
    model_id: str,
    request_id: str | None = None,
    api_key_mask: str | None = None,
) -> None:
    """
    Raise HTTPException 400 if the model has no pricing configured.

    Raises 503 with a distinct error code when the model is detected as
    "high-value pricing missing" — operators should treat this as an outage.
    """
    if not Config.REQUIRE_MODEL_PRICING:
        return

    # Lazy import to avoid pulling pricing into modules that never call this.
    import asyncio

    from src.services.pricing import model_has_pricing

    # Free models legitimately have no/zero pricing — exempt them so the
    # zero-price rejection in model_has_pricing only blocks PAID models whose
    # price is missing or zero (which would otherwise be served at a loss).
    # Covers both the `:free` suffix convention and the DB `is_free` flag.
    try:
        from src.services.cache.model_capabilities_cache import is_free_model

        if model_id and await asyncio.to_thread(is_free_model, model_id):
            return
    except Exception:  # noqa: BLE001 - free-check is best-effort; fall through on any error
        pass

    try:
        has_pricing = await asyncio.to_thread(model_has_pricing, model_id)
    except ValueError as e:
        logger.error(
            "Rejected high-value unpriced request (request_id=%s, model=%s, key=%s): %s",
            request_id,
            model_id,
            api_key_mask,
            e,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": f"Pricing for model '{model_id}' is not configured. Please contact support.",
                    "type": "service_unavailable",
                    "code": "pricing_not_configured",
                }
            },
        )

    if not has_pricing:
        logger.warning(
            "Rejected unpriced model request (request_id=%s, model=%s, key=%s)",
            request_id,
            model_id,
            api_key_mask,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": f"Model '{model_id}' is not available for inference (no pricing configured).",
                    "type": "invalid_request_error",
                    "code": "model_not_priced",
                }
            },
        )


def enforce_subscription_status_gate(
    user: dict[str, Any] | None,
    request_id: str | None = None,
) -> None:
    """
    Raise HTTPException 403 if user.subscription_status is in BLOCKED_SUBSCRIPTION_STATUSES.

    Handles None user (returns silently — caller is responsible for auth gating)
    and normalizes the DB value (lowercase + strip).
    """
    if not user:
        return
    raw = user.get("subscription_status")
    if not raw:
        return
    sub_status = str(raw).strip().lower()
    if sub_status in Config.BLOCKED_SUBSCRIPTION_STATUSES:
        # Payment-lapse statuses (canceled, past_due, ...) must not lock out
        # prepaid balances: purchased credits are retained on cancellation and
        # were already paid for. Only hard-blocked abuse statuses (bot,
        # suspended) block regardless of balance.
        if sub_status not in Config.HARD_BLOCKED_SUBSCRIPTION_STATUSES:
            purchased = float(user.get("purchased_credits") or 0)
            legacy = float(user.get("credits") or 0)
            allowance = float(user.get("subscription_allowance") or 0)
            has_prepaid_balance = purchased > 0 or (
                allowance == 0 and purchased == 0 and legacy > 0
            )
            if has_prepaid_balance:
                logger.info(
                    "Allowing request from lapsed-subscription user with prepaid credits "
                    "(request_id=%s, user_id=%s, subscription_status=%s)",
                    request_id,
                    user.get("id"),
                    sub_status,
                )
                return
        logger.warning(
            "Blocking request (request_id=%s, user_id=%s, subscription_status=%s)",
            request_id,
            user.get("id"),
            sub_status,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "message": (
                        f"Your account ({sub_status}) is not permitted to make API calls. "
                        f"Please contact support or renew your subscription."
                    ),
                    "type": "permission_error",
                    "code": f"subscription_{sub_status}",
                }
            },
        )


def enforce_anonymous_gate(
    is_anonymous: bool,
    request_id: str | None = None,
    model_id: str | None = None,
) -> None:
    """
    Raise HTTPException 401 if anonymous requests are disabled and this request is anonymous.
    """
    if not is_anonymous:
        return
    if Config.ANONYMOUS_ENABLED:
        return
    logger.warning(
        "Rejected anonymous request (request_id=%s, model=%s)",
        request_id,
        model_id,
    )
    raise HTTPException(
        status_code=401,
        detail={
            "error": {
                "message": "Authentication required. Provide a valid API key in the Authorization header.",
                "type": "authentication_error",
                "code": "missing_api_key",
            }
        },
    )


def enforce_community_auth_gate(
    is_anonymous: bool,
    model_id: str | None,
    request_id: str | None = None,
) -> None:
    """
    Raise HTTPException 403 if an anonymous caller requests a community/<model>
    (gatewayz-backend#2262 #2265, M4 spec §1).

    Community nodes are an elevated-trust, non-contractual party (the operator
    sees prompt content by construction) -- the model id prefix is the
    client's *explicit* consent to that trade-off, which an anonymous caller
    (no account, no accountability) cannot meaningfully give. It also has a
    practical consequence: without an authenticated request there is no
    reliable billing_ref-keyed accounting trail, so a community node would do
    unpaid, unattributable work. This runs independently of
    ``enforce_anonymous_gate`` (which only gates on ``Config.ANONYMOUS_ENABLED``
    being off) -- community is blocked for anonymous callers regardless of
    that flag.
    """
    if not is_anonymous:
        return
    if not model_id or not model_id.startswith("community/"):
        return
    logger.warning(
        "Rejected anonymous request for community model (request_id=%s, model=%s)",
        request_id,
        model_id,
    )
    raise HTTPException(
        status_code=403,
        detail={
            "error": {
                "message": (
                    "Community-provided models require an authenticated request. "
                    "Provide a valid API key in the Authorization header."
                ),
                "type": "permission_error",
                "code": "community_requires_auth",
            }
        },
    )
