"""Daily WAYZ settlement of accrued community-GPU provider earnings
(gatewayz-backend#2266; m4/spec.md §5; PR #2288 review fix round 1).

Mirrors src/services/chain/wayz_staking_sync.py's split: this module holds
the pure settlement logic against an already-constructed
WayzProviderRewardsClient; deciding whether to build one at all (i.e.
whether WAYZ_REWARDS_POOL_PRIVATE_KEY is configured) is the scheduled
job's job (src/services/scheduled_sync.py), which catches
WayzProviderRewardsClientError separately from unexpected failures --
same pattern as run_scheduled_wayz_staking_sync.

**I4 fix (void-vs-settle race):** a provider's accrued earnings are no
longer summed-then-transferred against a snapshot that can go stale --
`mark_earnings_settling` atomically flips exactly the rows still
'accrued' (a single `UPDATE ... WHERE status='accrued'`) before anything
is transferred, tagging them with the settlement row's id. A concurrent
spot-check failure's `void_earning_for_work` (which also only ever
matches `status='accrued'`) can no longer touch a row after this has
claimed it into 'settling', and this can never claim a row a concurrent
void got to first. The authoritative amount transferred is always the sum
of what the atomic flip actually returned, never the earlier preview
read. A failure at any point after the flip reverts those rows back to
'accrued' (`mark_earnings_accrued`) so they're retried by a future run.
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
    list_settling_earnings_for_settlement,
    list_stuck_pending_settlements,
    mark_earnings_accrued,
    mark_earnings_settled,
    mark_earnings_settling,
    mark_settlement_failed,
    mark_settlement_sent,
    update_settlement_amount,
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
    """One settlement pass: per approved provider, preview accrued
    earnings; pay out iff the preview clears COMMUNITY_MIN_PAYOUT_WAYZ,
    the remaining per-run cap (COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ,
    decremented as the run progresses so multiple providers can't
    collectively blow it), and the pool's current balance (also
    decremented as the run progresses). The preview is a cheap filter,
    not the authoritative amount -- see the module docstring's I4 note:
    the real amount transferred is whatever `mark_earnings_settling`'s
    atomic flip actually claims, re-checked against the same three
    thresholds before any transfer is attempted.

    Idempotent: a provider with an already-'pending' settlement (a
    previous run that crashed mid-flight) is skipped entirely rather than
    double-settled -- see reconcile_stuck_settlements for how that gets
    resolved automatically after COMMUNITY_SETTLEMENT_STUCK_HOURS.
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
                "skipping this run; reconcile_stuck_settlements resolves it automatically "
                "once it's old enough",
                provider_id,
                pending.get("id"),
            )
            continue

        preview_earnings = list_accrued_earnings(provider_id)
        if not preview_earnings:
            continue
        preview_total_wei = sum(int(e["amount_wei"]) for e in preview_earnings)

        if preview_total_wei < min_payout_wei:
            result.providers_skipped_below_min += 1
            continue

        if preview_total_wei > remaining_cap_wei:
            result.providers_skipped_cap += 1
            logger.warning(
                "settlement: provider %s's accrued %s wei exceeds the remaining "
                "per-run cap (%s wei) -- deferred to a future run",
                provider_id,
                preview_total_wei,
                remaining_cap_wei,
            )
            continue

        if preview_total_wei > pool_balance_wei:
            result.providers_skipped_insufficient_pool += 1
            logger.error(
                "settlement: rewards pool balance (%s wei) insufficient for provider %s's "
                "%s wei -- deferred, NOT marked failed (earnings stay accrued)",
                pool_balance_wei,
                provider_id,
                preview_total_wei,
            )
            continue

        payout_wallet = provider.get("payout_wallet_address")
        if not payout_wallet:
            logger.warning(
                "settlement: provider %s is approved but has no payout_wallet_address -- skipping",
                provider_id,
            )
            continue

        settlement = create_settlement(provider_id, period_start, period_end, preview_total_wei)
        if settlement is None:
            logger.warning(
                "settlement: failed to create a settlement row for provider %s; earnings stay accrued",
                provider_id,
            )
            continue
        settlement_id = settlement["id"]

        # I4: the atomic claim. Whatever this returns is the ONLY set of
        # earnings this settlement is allowed to touch from here on.
        flipped = mark_earnings_settling(provider_id, settlement_id)
        if not flipped:
            mark_settlement_failed(
                settlement_id, "no accrued earnings remained at the atomic settling flip"
            )
            continue

        earning_ids = [row["id"] for row in flipped]
        total_wei = sum(int(row["amount_wei"]) for row in flipped)
        if total_wei != preview_total_wei:
            update_settlement_amount(settlement_id, total_wei)

        # Re-validate against the AUTHORITATIVE total -- a concurrent void
        # between the preview read and the atomic flip could have moved
        # this provider below/above a threshold since the preview.
        if total_wei < min_payout_wei:
            mark_earnings_accrued(earning_ids, settlement_id)
            mark_settlement_failed(
                settlement_id, "fell below minimum payout after atomic reconciliation"
            )
            result.providers_skipped_below_min += 1
            continue
        if total_wei > remaining_cap_wei:
            mark_earnings_accrued(earning_ids, settlement_id)
            mark_settlement_failed(
                settlement_id, "exceeded remaining per-run cap after atomic reconciliation"
            )
            result.providers_skipped_cap += 1
            continue
        if total_wei > pool_balance_wei:
            mark_earnings_accrued(earning_ids, settlement_id)
            mark_settlement_failed(
                settlement_id, "insufficient pool balance after atomic reconciliation"
            )
            result.providers_skipped_insufficient_pool += 1
            continue

        try:
            tx_hash = await client.transfer(payout_wallet, total_wei)
        except Exception as e:
            logger.error("settlement: transfer failed for provider %s: %s", provider_id, e)
            mark_settlement_failed(settlement_id, str(e))
            mark_earnings_accrued(earning_ids, settlement_id)
            result.settlements_failed += 1
            continue

        mark_settlement_sent(settlement_id, tx_hash)
        mark_earnings_settled(earning_ids, settlement_id)
        result.settlements_sent += 1
        result.total_sent_wei += total_wei
        pool_balance_wei -= total_wei
        remaining_cap_wei -= total_wei

    return result


@dataclass
class ReconcileResult:
    settlements_checked: int = 0
    settlements_confirmed_sent: int = 0
    settlements_marked_failed: int = 0


async def reconcile_stuck_settlements(client: WayzProviderRewardsClient) -> ReconcileResult:
    """Resolve provider_settlements rows stuck 'pending' for longer than
    COMMUNITY_SETTLEMENT_STUCK_HOURS (default 2h) -- PR #2288 review I3.
    Crash-recovery for the window between create_settlement/
    mark_earnings_settling and mark_settlement_sent/mark_earnings_settled.
    Call this BEFORE run_settlement_once in the same scheduled run (see
    src/services/scheduled_sync.py) so a stuck row is freed up in time to
    be reconsidered the same day, not one day later.

    Rule (documented as the runbook in docs/gpu/VERIFICATION_AND_PAYOUTS.md):
    if tx_hash is present AND the on-chain receipt shows success
    (status == 1), confirm it (mark sent, flip its earnings to settled).
    In EVERY other case -- no tx_hash at all (crashed before transfer()
    even returned), a receipt showing an on-chain revert (status == 0), or
    no receipt found after being stuck this long (on a ~2s-block chain,
    very likely dropped or never broadcast) -- mark the settlement failed
    and revert its earnings to 'accrued' so a future run retries them.

    This deliberately treats "no receipt found after 2h+" as a failure
    rather than waiting indefinitely. The runbook calls out the one real
    risk this creates: if the original transaction is somehow still in
    flight (e.g. a slow/congested RPC) and lands on-chain LATER, a
    provider whose earnings were reverted-and-retried would be paid
    twice. Operators should check the pool EOA's transaction history on
    Snowtrace for the recorded tx_hash before manually re-enabling
    settlement for a provider this swept, if that ever looks ambiguous.
    """
    result = ReconcileResult()
    stuck_before = (
        datetime.now(UTC) - timedelta(hours=Config.COMMUNITY_SETTLEMENT_STUCK_HOURS)
    ).isoformat()

    for settlement in list_stuck_pending_settlements(stuck_before):
        result.settlements_checked += 1
        settlement_id = settlement["id"]
        tx_hash = settlement.get("tx_hash")
        earning_ids = [row["id"] for row in list_settling_earnings_for_settlement(settlement_id)]

        confirmed = False
        if tx_hash:
            try:
                receipt = await asyncio.to_thread(client.get_receipt, tx_hash)
            except Exception as e:
                logger.warning(
                    "settlement reconciliation: get_receipt failed for tx %s: %s", tx_hash, e
                )
                receipt = None
            if receipt is not None and receipt.get("status") == 1:
                confirmed = True

        if confirmed:
            mark_settlement_sent(settlement_id, tx_hash)
            mark_earnings_settled(earning_ids, settlement_id)
            result.settlements_confirmed_sent += 1
            logger.info(
                "settlement reconciliation: confirmed settlement %s as sent (tx=%s)",
                settlement_id,
                tx_hash,
            )
        else:
            mark_settlement_failed(
                settlement_id,
                "stuck pending beyond COMMUNITY_SETTLEMENT_STUCK_HOURS with no confirmed "
                "on-chain success -- reconciled by the automatic sweep; verify manually "
                "before relying on a retry (see docs/gpu/VERIFICATION_AND_PAYOUTS.md)",
            )
            mark_earnings_accrued(earning_ids, settlement_id)
            result.settlements_marked_failed += 1
            logger.warning(
                "settlement reconciliation: marked settlement %s failed (tx_hash=%s) and "
                "reverted %s earning(s) to accrued",
                settlement_id,
                tx_hash,
                len(earning_ids),
            )

    return result
