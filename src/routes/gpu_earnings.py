"""GET /gpu/providers/me/earnings (gatewayz-backend#2265, #2266;
m4/spec.md §5).

Deliberately its OWN route file, not appended to src/routes/gpu.py --
that file is owned by the parallel W-A1 workstream (registration/nodes)
and doesn't exist in this worktree yet; a shared file would be a merge
conflict waiting to happen (m4/WB-payouts.md). Registered under its own
("gpu_earnings", "GPU Earnings") entry in src/main.py.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.db.gpu_payouts import (
    earnings_totals,
    get_payout_tiers,
    get_provider_for_user,
    get_provider_verified_volume_7d,
    list_recent_work_for_provider,
    list_settlements_for_provider,
)
from src.security.deps import get_user_id
from src.services.emission.epoch import get_provider_emission_view
from src.services.gpu.earnings import next_tier_min_tokens_7d, tier_multiplier_bps
from src.services.gpu.payout_views import micros_to_usd, tx_url, usd_totals_view

logger = logging.getLogger(__name__)

router = APIRouter()

_WORK_HISTORY_LIMIT = 50


def _work_view(row: dict) -> dict[str, Any]:
    """No prompt_hash/response_hash -- there's no plaintext behind them to
    leak, but the hashes themselves aren't useful to a provider and the
    endpoint contract (WB-payouts.md) doesn't call for exposing them."""
    return {
        "billing_ref": row.get("billing_ref"),
        "model": row.get("model"),
        "prompt_tokens": row.get("prompt_tokens"),
        "completion_tokens": row.get("completion_tokens"),
        "verification": row.get("verification"),
        "created_at": row.get("created_at"),
    }


def _settlement_view(row: dict) -> dict[str, Any]:
    """`amount_wei` is in the settlement's own asset (ETH on Base for new
    rows, WAYZ on Fuji for legacy rows -- see `asset`/`chain`)."""
    tx_hash = row.get("tx_hash")
    amount_wei = row.get("amount_wei")
    asset = row.get("asset") or "WAYZ"
    return {
        "id": row.get("id"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "asset": asset,
        "chain": row.get("chain"),
        "amount_wei": str(amount_wei) if amount_wei is not None else None,
        "amount_usd": micros_to_usd(row.get("amount_usd_micros")),
        "eth_usd_price": (
            str(row["eth_usd_price"]) if row.get("eth_usd_price") is not None else None
        ),
        "status": row.get("status"),
        "tx_hash": tx_hash,
        "tx_url": tx_url(asset, tx_hash),
        "error": row.get("error"),
        "created_at": row.get("created_at"),
    }


@router.get("/gpu/providers/me/earnings", tags=["gpu_earnings"])
async def get_my_earnings(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    """The caller's own gpu_providers row's earnings -- always scoped via
    get_provider_for_user(user_id), never a raw provider_id path/query
    param, so there's no IDOR surface (mirrors src/routes/faucet.py's
    status-endpoint pattern)."""
    provider = get_provider_for_user(user_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="no_gpu_provider_for_account")

    provider_id = provider["id"]
    totals = earnings_totals(provider_id)
    work = list_recent_work_for_provider(provider_id, limit=_WORK_HISTORY_LIMIT)
    settlements = list_settlements_for_provider(provider_id)

    # Sliding-scale payout tier standing (m4/spec.md §5 follow-up) -- so an
    # operator can see where they sit and how much more trailing-7d
    # verified volume it takes to reach the next multiplier.
    volume_7d = get_provider_verified_volume_7d(provider_id)
    tiers = get_payout_tiers()
    multiplier_bps = tier_multiplier_bps(volume_7d, tiers)
    next_min = next_tier_min_tokens_7d(volume_7d, tiers)

    data: dict[str, Any] = {
        "totals": usd_totals_view(totals),
        "work": [_work_view(row) for row in work],
        "settlements": [_settlement_view(row) for row in settlements],
        "tier": {
            "current_volume_7d": volume_7d,
            "multiplier_bps": multiplier_bps,
            "next_tier_min_tokens_7d": next_min,
        },
    }

    # Chutes-style emission rewards (gatewayz-backend tokenomics) --
    # present only once this provider has been scored at least once
    # (None while the feature is off, or before the provider's first
    # qualifying epoch).
    emission = get_provider_emission_view(provider_id)
    if emission is not None:
        data["emission"] = emission

    return {"success": True, "data": data}
