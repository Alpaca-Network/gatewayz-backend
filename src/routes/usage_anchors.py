"""Usage anchoring API (docs/usage-anchor/README.md).

Public, no auth (roots and transactions are public on-chain anyway):
- GET /.well-known/anchors.json -- chain, contract, anchorer, Merkle spec,
  latest anchors with explorer links. Machine-readable entry point.
- GET /v1/anchors, GET /v1/anchors/{day}

Authenticated:
- GET /v1/anchors/proof/{ledger_id} -- inclusion proof for one of the
  caller's OWN api_usage ledger rows (404 for anyone else's).

Admin:
- POST /admin/usage-anchor/run -- anchor a given finished day now (backfill
  / demo). Superadmin-only and audited, like the staking-rewards manual run.

Every DB/RPC call runs in a worker thread; nothing here blocks the loop.
Wording rule: responses state facts only -- no claim of any Avalanche
partnership or endorsement.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.config.config import Config
from src.db import usage_anchors as db
from src.db.audit import record_audit
from src.security.deps import get_current_user, require_superadmin
from src.services.chain.usage_anchor import (
    MERKLE_SPEC,
    ProofNotAvailableError,
    ProofNotFoundError,
    UsageAnchorError,
    anchor_view,
    build_proof,
    day_index,
    explorer_address_url,
    run_usage_anchor_once,
)
from src.services.chain.usage_anchor_client import (
    UsageAnchorClientError,
    anchorer_address_from_config,
)
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_PUBLIC_LIMIT_DEFAULT = 30
_PUBLIC_LIMIT_MAX = 366

# Public routes are anonymous, so the per-key endpoint limiter would skip
# them; they rely on the global IP-level limits (security_middleware.py) and
# are cheap (one indexed read). A proof rebuilds a whole day's tree, so the
# authenticated proof route gets its own per-key limit.
anchors_proof_rl = create_endpoint_rate_limit(
    "usage_anchors_proof", max_requests=10, window_seconds=60
)


class RunAnchorRequest(BaseModel):
    day: date | None = None


def _configured() -> bool:
    return bool(Config.USAGE_ANCHOR_CONTRACT_ADDRESS)


def _contract_block() -> dict[str, Any]:
    chain_id = Config.USAGE_ANCHOR_CHAIN_ID
    contract = Config.USAGE_ANCHOR_CONTRACT_ADDRESS
    anchorer = anchorer_address_from_config()
    return {
        "configured": _configured(),
        "enabled": Config.USAGE_ANCHOR_ENABLED,
        "chain": "avalanche-c-chain",
        "chain_id": chain_id,
        "network": "mainnet"
        if chain_id == 43114
        else "fuji-testnet"
        if chain_id == 43113
        else None,
        "contract_address": contract,
        "contract_explorer_url": explorer_address_url(chain_id, contract),
        "anchorer": anchorer,
        "anchorer_explorer_url": explorer_address_url(chain_id, anchorer),
    }


def _list(limit: int) -> list[dict[str, Any]]:
    if not _configured():
        return []
    rows = db.list_anchors(
        Config.USAGE_ANCHOR_CHAIN_ID,
        Config.USAGE_ANCHOR_CONTRACT_ADDRESS,
        limit=limit,
        statuses=["pending", "confirmed"],
    )
    return [anchor_view(r) for r in rows]


@router.get("/.well-known/anchors.json", tags=["anchors"])
async def well_known_anchors() -> dict[str, Any]:
    try:
        anchors = await asyncio.to_thread(_list, _PUBLIC_LIMIT_DEFAULT)
    except Exception as e:
        logger.warning("usage anchors listing failed: %s", e)
        anchors = []
    return {
        "version": 1,
        "publisher": "Gatewayz",
        "description": (
            "Daily Merkle roots of Gatewayz inference usage records, written to "
            "Avalanche C-Chain. Only roots are public; each user can fetch an "
            "inclusion proof for their own records."
        ),
        **_contract_block(),
        "merkle": {"spec": MERKLE_SPEC, "leaf_version": 1, "hash": "sha256"},
        "proof_endpoint": "/v1/anchors/proof/{ledger_id}",
        "anchors": anchors,
    }


@router.get("/v1/anchors", tags=["anchors"])
async def list_anchors(limit: int = _PUBLIC_LIMIT_DEFAULT) -> dict[str, Any]:
    limit = max(1, min(limit, _PUBLIC_LIMIT_MAX))
    anchors = await asyncio.to_thread(_list, limit)
    return {**_contract_block(), "anchors": anchors}


@router.get("/v1/anchors/proof/{ledger_id}", tags=["anchors"])
async def get_proof(
    ledger_id: int,
    user: dict[str, Any] = Depends(get_current_user),
    _rl: None = Depends(anchors_proof_rl),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(build_proof, ledger_id, int(user["id"]))
    except ProofNotFoundError as e:
        raise HTTPException(status_code=404, detail="Usage record not found") from e
    except (ProofNotAvailableError, UsageAnchorError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/v1/anchors/{day}", tags=["anchors"])
async def get_anchor_for_day(day: date) -> dict[str, Any]:
    if not _configured():
        raise HTTPException(status_code=404, detail="Usage anchoring is not configured")
    row = await asyncio.to_thread(
        db.get_anchor, Config.USAGE_ANCHOR_CHAIN_ID, Config.USAGE_ANCHOR_CONTRACT_ADDRESS, day
    )
    if not row or row.get("status") not in ("pending", "confirmed"):
        raise HTTPException(status_code=404, detail=f"No anchor for {day.isoformat()}")
    return {**_contract_block(), "anchor": anchor_view(row), "day_index": day_index(day)}


@router.post("/admin/usage-anchor/run", tags=["admin", "anchors"])
async def run_anchor_now(
    body: RunAnchorRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Anchor `day` (default: yesterday UTC) now. Idempotent -- a confirmed
    day is a no-op; a mismatched root is refused and flagged, never sent."""
    try:
        summary = await asyncio.to_thread(run_usage_anchor_once, body.day)
    except (UsageAnchorError, UsageAnchorClientError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    record_audit(
        admin_user,
        action="usage_anchor.run",
        target_type="usage_anchors",
        target_id=summary.get("day"),
        request=request,
        metadata=summary,
    )
    return {"success": True, "data": summary}
