"""WAYZ staking sync orchestration (gatewayz-backend#2244).

Every run: discover new stakers from Staked event logs, then re-read EVERY
known wallet's live on-chain balance -- this doubles as reconciliation (see
docs/superpowers/specs/2026-09-01-wayz-staking-indexer-design.md), so there
is no separate "drift repair" pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.config.config import Config
from src.db.wallet_stakes import (
    get_all_wallet_addresses,
    get_sync_cursor,
    insert_wallet_if_missing,
    set_sync_cursor,
    upsert_wallet_stake,
)
from src.services.chain.wayz_staking_client import WayzStakingClient

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    wallets_discovered: int
    wallets_synced: int
    wallets_failed: int
    total_staked: int
    from_block: int
    to_block: int


def sync_once(client: WayzStakingClient) -> SyncResult:
    """Run one full sync pass against an already-constructed client.

    Building the client (and deciding whether to run at all) is the
    caller's job -- see WayzStakingClient.from_config() and the scheduled
    job in scheduled_sync.py (Task 5), which catches WayzStakingClientError
    separately from unexpected failures.
    """
    contract_address = Config.WAYZ_STAKING_CONTRACT_ADDRESS

    cursor = get_sync_cursor(contract_address)
    from_block = cursor if cursor is not None else Config.WAYZ_STAKING_DEPLOY_BLOCK
    to_block = client.current_block()
    scan_start = from_block if cursor is None else from_block + 1

    new_addresses = client.staked_event_addresses(scan_start, to_block)
    known_addresses = set(get_all_wallet_addresses())
    discovered = [addr for addr in new_addresses if addr not in known_addresses]
    all_discoveries_ok = True
    for addr in discovered:
        if not insert_wallet_if_missing(addr):
            all_discoveries_ok = False

    all_addresses = known_addresses | set(discovered)
    total_staked = client.total_staked()

    now = datetime.now(UTC).isoformat()
    synced = 0
    failed = 0
    for addr in all_addresses:
        try:
            staked = client.staked_balance_of(addr)
            allowance = (
                0
                if total_staked == 0
                else (staked * Config.WAYZ_DAILY_INFERENCE_CAPACITY) // total_staked
            )
            upsert_wallet_stake(addr, staked, allowance, to_block, now)
            synced += 1
        except Exception as e:
            failed += 1
            logger.warning("WAYZ staking resync failed for wallet %s: %s", addr, e)

    if all_discoveries_ok:
        set_sync_cursor(contract_address, to_block, now)
    else:
        logger.warning(
            "WAYZ staking sync: skipping cursor advance to %s -- at least one "
            "newly-discovered wallet failed to insert; retrying discovery next run",
            to_block,
        )

    return SyncResult(
        wallets_discovered=len(discovered),
        wallets_synced=synced,
        wallets_failed=failed,
        total_staked=total_staked,
        from_block=scan_start,
        to_block=to_block,
    )
