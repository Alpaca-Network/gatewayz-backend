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

# Cap on rows scanned by get_stake_totals -- fine for testnet scale (a
# handful of wallets), but a real limit rather than an unbounded select.
_STAKE_TOTALS_ROW_CAP = 10000


def get_all_wallet_addresses() -> list[str]:
    """All wallet addresses currently tracked. Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = client.table(_WALLET_STAKES_TABLE).select("wallet_address").execute()
        return [row["wallet_address"] for row in (result.data or [])]
    except Exception as e:
        logger.warning(f"wallet_stakes lookup failed: {e}")
        return []


def upsert_wallet_stake(
    wallet_address: str,
    staked_amount: int,
    daily_allowance: int,
    last_synced_block: int,
    last_synced_at: str,
) -> bool:
    """Write a wallet's freshly-synced staked amount and computed allowance.

    This is the ONLY write path for a wallet_stakes row -- there is no
    separate "insert if missing" step. Supabase upsert(on_conflict=...)
    creates the row on first write and updates it thereafter, so a
    newly-discovered wallet's first call here both creates and correctly
    populates its row in one write. (An earlier version had a redundant
    insert_wallet_if_missing() step; removed because this upsert already
    subsumed it -- the extra step only added a gate that didn't protect
    what it claimed to.)

    last_synced_block is the block the sync RUN reached (to_block), not
    necessarily the exact block this wallet's balance was read at -- the
    view calls that produce staked_amount/daily_allowance read `latest`
    chain state, not a block pinned to last_synced_block. Fine for this
    indexer's purposes today, but not a precise historical snapshot.

    Returns True on success, False on a caught exception -- the caller
    (sync_once) uses this to decide whether it's safe to advance the sync
    cursor past a newly-discovered wallet.
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
        return True
    except Exception as e:
        logger.warning(f"wallet_stakes upsert failed for {wallet_address}: {e}")
        return False


def get_wallet_stake(wallet_address: str) -> dict | None:
    """A single wallet_stakes row by primary key, or None if unsynced (or on error)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WALLET_STAKES_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"wallet_stakes lookup failed for {wallet_address}: {e}")
        return None


def get_stake_totals() -> tuple[str, int]:
    """(total_staked_wei_str, wallet_count) across all tracked wallets.

    staked_amount is numeric(78,0); PostgREST returns it as a string, so
    this sums with Python int() rather than float() to avoid precision
    loss on wei-scale values. Returns ("0", 0) on any lookup error.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_WALLET_STAKES_TABLE)
            .select("staked_amount")
            .limit(_STAKE_TOTALS_ROW_CAP)
            .execute()
        )
        rows = result.data or []
        if len(rows) >= _STAKE_TOTALS_ROW_CAP:
            logger.warning(
                f"get_stake_totals hit the {_STAKE_TOTALS_ROW_CAP}-row cap; totals may be incomplete"
            )
        total = sum(int(row["staked_amount"]) for row in rows)
        return str(total), len(rows)
    except Exception as e:
        logger.warning(f"wallet_stakes totals lookup failed: {e}")
        return "0", 0


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


def get_sync_cursor_row(contract_address: str) -> dict | None:
    """Full chain_sync_cursors row (block + updated_at), or None if never
    synced (or on error)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CURSOR_TABLE)
            .select("*")
            .eq("contract_address", contract_address.lower())
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"chain_sync_cursors row lookup failed: {e}")
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
