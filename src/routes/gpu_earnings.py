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
    get_provider_for_user,
    list_recent_work_for_provider,
    list_settlements_for_provider,
)
from src.security.deps import get_user_id

logger = logging.getLogger(__name__)

router = APIRouter()

_SNOWTRACE_TX_URL = "https://testnet.snowtrace.io/tx/{tx_hash}"
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
    tx_hash = row.get("tx_hash")
    amount_wei = row.get("amount_wei")
    return {
        "id": row.get("id"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "amount_wei": str(amount_wei) if amount_wei is not None else None,
        "status": row.get("status"),
        "tx_hash": tx_hash,
        "tx_url": _SNOWTRACE_TX_URL.format(tx_hash=tx_hash) if tx_hash else None,
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

    return {
        "success": True,
        "data": {
            "totals": {
                "accrued_wei": str(totals["accrued"]),
                "settled_wei": str(totals["settled"]),
                "void_wei": str(totals["void"]),
            },
            "work": [_work_view(row) for row in work],
            "settlements": [_settlement_view(row) for row in settlements],
        },
    }
