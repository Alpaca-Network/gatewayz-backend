"""Payer-cohort analytics endpoints.

Feeds the weekly scorecard (GTM plan section 6) and the investor metrics sheet.
Separate from the existing ``/admin/monitoring/*`` routes because those answer
"is the gateway healthy" and these answer "is the business growing" -- mixing
them is how a deck ends up quoting request counts as traction.

Every number here is payer-derived. Nothing on this router reads signup counts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from src.security.deps import require_admin_or_env_key
from src.services.payer_metrics import (
    apply_epoch,
    build_weekly_scorecard,
    compute_new_paying_accounts,
    compute_paying_accounts,
    compute_revenue,
    compute_second_topup_rate,
    fetch_settled_payments,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/payers", tags=["payer-analytics"])


@router.get("/scorecard")
async def weekly_scorecard(_: str = Depends(require_admin_or_env_key)):
    """The weekly scorecard: the only numbers the operating cadence tracks.

    New paying accounts (WoW %), credit revenue (WoW %), second top-up rate,
    total payers, token volume.
    """
    try:
        return build_weekly_scorecard().to_dict()
    except Exception as e:
        logger.error("Failed to build weekly scorecard: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build scorecard") from e


@router.get("/trend")
async def payer_trend(
    weeks: int = Query(8, ge=1, le=52, description="Number of trailing weeks"),
    _: str = Depends(require_admin_or_env_key),
):
    """Week-by-week payer and revenue history.

    This is the "slope" the raise narrative rests on, so it returns every week
    including the empty ones -- silently omitting a flat week would overstate
    consistency.
    """
    try:
        payments = fetch_settled_payments()
        # Apply the same METRICS_EPOCH cutoff the scorecard uses. Without this
        # the two endpoints report different totals for the same metric, which
        # is exactly the disagreement docs/METRIC_DEFINITIONS.md exists to
        # prevent — and the kind a diligence question surfaces at the worst
        # possible moment.
        payments, epoch_note = apply_epoch(payments)
        now = datetime.now(UTC)

        series = []
        for i in range(weeks, 0, -1):
            end = now - timedelta(days=7 * (i - 1))
            start = end - timedelta(days=7)
            series.append(
                {
                    "week_start": start.date().isoformat(),
                    "week_end": end.date().isoformat(),
                    "new_paying_accounts": compute_new_paying_accounts(payments, start, end),
                    "revenue_usd": round(compute_revenue(payments, start, end), 2),
                }
            )

        return {
            "weeks": weeks,
            "total_paying_accounts": len(compute_paying_accounts(payments)),
            "second_topup_rate_pct": compute_second_topup_rate(payments),
            "series": series,
            "notes": [epoch_note] if epoch_note else [],
        }
    except Exception as e:
        logger.error("Failed to build payer trend: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build trend") from e


@router.get("/definitions")
async def metric_definitions(_: str = Depends(require_admin_or_env_key)):
    """Machine-readable metric definitions.

    Having these queryable means a dashboard, a deck and a diligence question
    can never disagree about what "paying account" means. Authenticated like
    every other /admin route; the public copy lives in
    ``docs/METRIC_DEFINITIONS.md``.
    """
    return {
        "paying_account": (
            "A user_id with at least one settled payment of any amount, ever. "
            "Not a signup, not an API key holder, not a trial."
        ),
        "new_paying_account": (
            "An account whose FIRST settled payment falls inside the reporting "
            "window. Returning customers are never counted as new."
        ),
        "settled_payment": (
            "A payment row with status in (succeeded, completed, paid). "
            "Pending and failed payments are excluded."
        ),
        "credit_revenue_usd": (
            "Sum of settled payment amounts inside the window, in USD. Gross "
            "credit purchases, not net of provider cost."
        ),
        "second_topup_rate_pct": (
            "Of all paying accounts, the percentage with >= 2 settled payments. "
            "Null when there are no payers (no data, not zero)."
        ),
        "tokens_through_gateway": (
            "Sum of input_tokens + output_tokens across chat completion requests "
            "in the window, regardless of who paid."
        ),
        "wow_pct": (
            "(current - previous) / previous * 100. Null when the previous "
            "period was zero — growth from zero is undefined, not infinite."
        ),
        "excluded": (
            "Signup counts and API key counts are deliberately absent. They were "
            "inflated by credit-farming bots and are not used in any metric here."
        ),
    }
