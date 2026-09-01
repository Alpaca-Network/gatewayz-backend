"""DB access for wallet_stakes and chain_sync_cursors (gatewayz-backend#2244).

Backs the on-chain WAYZStaking indexer (src/services/chain/wayz_staking_sync.py).
Mirrors src/db/routing_policies.py's try/except + logger.warning + safe-default
convention exactly -- callers must treat a lookup failure as "no data," never
as a hard failure, since this backs a background sync job that must never
crash the app.
"""

from __future__ import annotations

import logging

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_WALLET_STAKES_TABLE = "wallet_stakes"
_CURSOR_TABLE = "chain_sync_cursors"


def get_all_wallet_addresses() -> list[str]:
    """All wallet addresses currently tracked. Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = client.table(_WALLET_STAKES_TABLE).select("wallet_address").execute()
        return [row["wallet_address"] for row in (result.data or [])]
    except Exception as e:
        logger.warning(f"wallet_stakes lookup failed: {e}")
        return []


def insert_wallet_if_missing(wallet_address: str) -> bool:
    """Insert a new wallet_stakes row with zeroed balances if one doesn't exist.

    ignore_duplicates=True so a wallet discovered twice (same run or across
    overlapping runs) never errors and never clobbers an existing row's
    already-synced balance.

    Returns True on success, False on a caught exception -- the caller
    (sync_once) uses this to decide whether it's safe to advance the sync
    cursor past this discovery.
    """
    try:
        client = get_supabase_client()
        client.table(_WALLET_STAKES_TABLE).upsert(
            {
                "wallet_address": wallet_address,
                "staked_amount": "0",
                "daily_allowance": "0",
            },
            on_conflict="wallet_address",
            ignore_duplicates=True,
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"wallet_stakes insert failed for {wallet_address}: {e}")
        return False


def upsert_wallet_stake(
    wallet_address: str,
    staked_amount: int,
    daily_allowance: int,
    last_synced_block: int,
    last_synced_at: str,
) -> None:
    """Write a wallet's freshly-synced staked amount and computed allowance.

    last_synced_block is the block the sync RUN reached (to_block), not
    necessarily the exact block this wallet's balance was read at -- the
    view calls that produce staked_amount/daily_allowance read `latest`
    chain state, not a block pinned to last_synced_block. Fine for this
    indexer's purposes today, but not a precise historical snapshot.
    """
    try:
        client = get_supabase_client()
        client.table(_WALLET_STAKES_TABLE).upsert(
            {
                "wallet_address": wallet_address,
                "staked_amount": str(staked_amount),
                "daily_allowance": str(daily_allowance),
                "last_synced_block": last_synced_block,
                "last_synced_at": last_synced_at,
            },
            on_conflict="wallet_address",
        ).execute()
    except Exception as e:
        logger.warning(f"wallet_stakes upsert failed for {wallet_address}: {e}")


def get_sync_cursor(contract_address: str) -> int | None:
    """Last-synced block for this contract, or None if never synced (or on error)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CURSOR_TABLE)
            .select("last_synced_block")
            .eq("contract_address", contract_address.lower())
            .execute()
        )
        if not result.data:
            return None
        return int(result.data[0]["last_synced_block"])
    except Exception as e:
        logger.warning(f"chain_sync_cursors lookup failed: {e}")
        return None


def set_sync_cursor(contract_address: str, block: int, updated_at: str) -> None:
    try:
        client = get_supabase_client()
        client.table(_CURSOR_TABLE).upsert(
            {
                "contract_address": contract_address.lower(),
                "last_synced_block": block,
                "updated_at": updated_at,
            },
            on_conflict="contract_address",
        ).execute()
    except Exception as e:
        logger.warning(f"chain_sync_cursors update failed: {e}")
