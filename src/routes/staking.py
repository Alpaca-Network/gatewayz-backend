"""Public read API over wallet_stakes / chain_sync_cursors (supports
gatewayz-backend#2246). The indexer (src/services/chain/wayz_staking_sync.py)
writes these tables; nothing read them back before this. Additive, no
auth -- must return sane zeros / configured:false when the WAYZ staking
contract address is unset, which is the case in production today.
See docs/superpowers/specs/2026-09-01-wayz-staking-indexer-design.md.
"""

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.config.config import Config
from src.db.wallet_stakes import get_stake_totals, get_sync_cursor_row, get_wallet_stake
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# Chain the WAYZ contracts are deployed to (Avalanche Fuji testnet).
_CHAIN_ID = 43113

# WAYZStaking.sol's unstake cooldown. Not read from chain (no contract call
# in this read path) or from Config -- a fixed protocol constant, same as
# the contract enforces on-chain.
_UNSTAKE_COOLDOWN_SECONDS = 604800

staking_wallet_rl = create_endpoint_rate_limit("staking_wallet", max_requests=60, window_seconds=60)
staking_summary_rl = create_endpoint_rate_limit(
    "staking_summary", max_requests=60, window_seconds=60
)

# Same pattern as src/routes/faucet.py's _WALLET_ADDRESS_RE -- kept local
# rather than shared since faucet.py's own note parks that extraction until
# it's no longer premature; duplicating one regex line is cheaper than
# introducing a cross-route-module dependency for it now.
_WALLET_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _validate_wallet_address(wallet_address: str) -> str:
    if not _WALLET_ADDRESS_RE.match(wallet_address):
        raise HTTPException(
            status_code=422, detail="wallet_address must be a 0x-prefixed 40-character hex address"
        )
    return wallet_address.lower()


def _contracts() -> dict[str, Any]:
    return {
        "chain_id": _CHAIN_ID,
        "token": Config.WAYZ_TOKEN_CONTRACT_ADDRESS,
        "staking": Config.WAYZ_STAKING_CONTRACT_ADDRESS,
    }


@router.get("/staking/wallets/{wallet_address}", tags=["staking"])
async def get_wallet_staking(
    wallet_address: str,
    _rl: None = Depends(staking_wallet_rl),
) -> dict[str, Any]:
    """A single wallet's synced staking state. Unknown wallet -> 200 with
    zeros and synced:false, not 404 -- simpler for the dashboard to render."""
    wallet_address = _validate_wallet_address(wallet_address)
    row = get_wallet_stake(wallet_address)
    total_staked, _wallet_count = get_stake_totals()

    if row is None:
        staked_amount = "0"
        daily_allowance = "0"
        last_synced_block = None
        last_synced_at = None
        synced = False
    else:
        staked_amount = row["staked_amount"]
        daily_allowance = row["daily_allowance"]
        last_synced_block = row["last_synced_block"]
        last_synced_at = row["last_synced_at"]
        synced = True

    return {
        "success": True,
        "data": {
            "wallet_address": wallet_address,
            "staked_amount": staked_amount,
            "daily_allowance": daily_allowance,
            "last_synced_block": last_synced_block,
            "last_synced_at": last_synced_at,
            "synced": synced,
            "total_staked": total_staked,
            "daily_inference_capacity": str(Config.WAYZ_DAILY_INFERENCE_CAPACITY),
            "contracts": _contracts(),
            "configured": bool(Config.WAYZ_STAKING_CONTRACT_ADDRESS),
        },
    }


@router.get("/staking/summary", tags=["staking"])
async def get_staking_summary(
    _rl: None = Depends(staking_summary_rl),
) -> dict[str, Any]:
    """Protocol-wide staking totals."""
    total_staked, wallet_count = get_stake_totals()

    cursor_row = None
    if Config.WAYZ_STAKING_CONTRACT_ADDRESS:
        cursor_row = get_sync_cursor_row(Config.WAYZ_STAKING_CONTRACT_ADDRESS)

    return {
        "success": True,
        "data": {
            "total_staked": total_staked,
            "wallet_count": wallet_count,
            "daily_inference_capacity": str(Config.WAYZ_DAILY_INFERENCE_CAPACITY),
            "unstake_cooldown_seconds": _UNSTAKE_COOLDOWN_SECONDS,
            "last_synced_block": cursor_row["last_synced_block"] if cursor_row else None,
            "last_synced_at": cursor_row["updated_at"] if cursor_row else None,
            "contracts": _contracts(),
            "configured": bool(Config.WAYZ_STAKING_CONTRACT_ADDRESS),
        },
    }
