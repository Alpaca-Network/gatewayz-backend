"""Daily WAYZ settlement of accrued community-GPU provider earnings
(gatewayz-backend#2266; m4/spec.md §5).

Mirrors src/services/chain/wayz_staking_sync.py's split: this module holds
the pure settlement logic against an already-constructed
WayzProviderRewardsClient; deciding whether to build one at all (i.e.
whether WAYZ_REWARDS_POOL_PRIVATE_KEY is configured) is the scheduled
job's job (src/services/scheduled_sync.py), which catches
WayzProviderRewardsClientError separately from unexpected failures --
same pattern as run_scheduled_wayz_staking_sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from src.config.config import Config
from src.db.gpu_payouts import (
    create_settlement,
    get_pending_settlement,
    list_accrued_earnings,
    list_approved_providers,
    mark_earnings_settled,
    mark_settlement_failed,
    mark_settlement_sent,
)
from src.services.chain.wayz_rewards_client import WayzProviderRewardsClient

logger = logging.getLogger(__name__)

_WAYZ_DECIMALS = 18


@dataclass
class SettlementResult:
    providers_considered: int = 0
    settlements_sent: int = 0
    settlements_failed: int = 0
    providers_skipped_below_min: int = 0
    providers_skipped_pending: int = 0
    providers_skipped_cap: int = 0
    providers_skipped_insufficient_pool: int = 0
    total_sent_wei: int = field(default=0)


async def run_settlement_once(client: WayzProviderRewardsClient) -> SettlementResult:
    """One settlement pass: per approved provider, sum accrued earnings;
    pay out iff >= COMMUNITY_MIN_PAYOUT_WAYZ, within the remaining
    per-run cap (COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ, decremented as the run
    progresses so multiple providers can't collectively blow the cap),
    and within the pool's current balance (also decremented as the run
    progresses, for the same reason).

    Idempotent: a provider with an already-'pending' settlement (a
    previous run that crashed between creating the row and
    sending/marking it) is skipped entirely rather than double-settled --
    that stuck row needs manual resolution, not an automatic retry that
    could double-pay if the original transfer actually landed.
    """
    result = SettlementResult()
    now = datetime.now(UTC)
    period_start = (now - timedelta(hours=Config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS)).isoformat()
    period_end = now.isoformat()

    min_payout_wei = Config.COMMUNITY_MIN_PAYOUT_WAYZ * 10**_WAYZ_DECIMALS
    remaining_cap_wei = Config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ * 10**_WAYZ_DECIMALS

    try:
        pool_balance_wei = await asyncio.to_thread(client.pool_balance_wei)
    except Exception as e:
        logger.warning("settlement: pool_balance_wei() failed, aborting this run: %s", e)
        return result

    for provider in list_approved_providers():
        result.providers_considered += 1
        provider_id = provider["id"]

        pending = get_pending_settlement(provider_id)
        if pending is not None:
            result.providers_skipped_pending += 1
            logger.warning(
                "settlement: provider %s has a stuck pending settlement (id=%s) -- "
                "skipping until it's resolved manually",
                provider_id,
                pending.get("id"),
            )
            continue

        earnings = list_accrued_earnings(provider_id)
        if not earnings:
            continue

        total_wei = sum(int(e["amount_wei"]) for e in earnings)
        if total_wei < min_payout_wei:
            result.providers_skipped_below_min += 1
            continue

        if total_wei > remaining_cap_wei:
            result.providers_skipped_cap += 1
            logger.warning(
                "settlement: provider %s's accrued %s wei exceeds the remaining "
                "per-run cap (%s wei) -- deferred to a future run",
                provider_id,
                total_wei,
                remaining_cap_wei,
            )
            continue

        if total_wei > pool_balance_wei:
            result.providers_skipped_insufficient_pool += 1
            logger.error(
                "settlement: rewards pool balance (%s wei) insufficient for provider %s's "
                "%s wei -- deferred, NOT marked failed (earnings stay accrued)",
                pool_balance_wei,
                provider_id,
                total_wei,
            )
            continue

        payout_wallet = provider.get("payout_wallet_address")
        if not payout_wallet:
            logger.warning(
                "settlement: provider %s is approved but has no payout_wallet_address -- skipping",
                provider_id,
            )
            continue

        settlement = create_settlement(provider_id, period_start, period_end, total_wei)
        if settlement is None:
            logger.warning(
                "settlement: failed to create a settlement row for provider %s; earnings stay accrued",
                provider_id,
            )
            continue

        try:
            tx_hash = await client.transfer(payout_wallet, total_wei)
        except Exception as e:
            logger.error("settlement: transfer failed for provider %s: %s", provider_id, e)
            mark_settlement_failed(settlement["id"], str(e))
            result.settlements_failed += 1
            continue

        mark_settlement_sent(settlement["id"], tx_hash)
        mark_earnings_settled([e["id"] for e in earnings], settlement["id"])
        result.settlements_sent += 1
        result.total_sent_wei += total_wei
        pool_balance_wei -= total_wei
        remaining_cap_wei -= total_wei

    return result
