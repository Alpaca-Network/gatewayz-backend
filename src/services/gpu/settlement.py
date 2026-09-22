"""Daily settlement of accrued community-GPU provider earnings, paid in
native ETH on Base (gatewayz-backend#2266; m4/spec.md §5; PR #2288 review
fix round 1; payout asset switched from WAYZ to ETH on 2026-09-22 because
WAYZ is not going public for now).

Earnings accrue in USD micro-dollars (src/services/gpu/earnings.py,
src/services/emission/epoch.py). Each run reads the Chainlink ETH/USD
price on Base ONCE, refuses to pay anyone if that answer is stale or
non-positive (Config.ETH_USD_PRICE_MAX_AGE_SECONDS), and converts every
provider's USD total to wei at that single price (floor -- never overpay).
The price and its timestamp are recorded on each settlement row.

This module holds the pure settlement logic against an already-constructed
EthPayoutClient; deciding whether to build one at all (i.e. whether
PROVIDER_PAYOUT_POOL_PRIVATE_KEY is configured) is the scheduled job's job
(src/services/scheduled_sync.py), which catches EthPayoutClientError
separately from unexpected failures.

**I4 fix (void-vs-settle race):** a provider's accrued earnings are not
summed-then-transferred against a snapshot that can go stale --
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

Legacy WAYZ-denominated earnings (amount_usd_micros IS NULL) are never
claimed by this path -- see src/db/gpu_payouts.py.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from src.services.chain.eth_payout_client import (
    EthPayoutClient,
    StalePriceError,
    usd_micros_to_wei,
    validate_price,
)

logger = logging.getLogger(__name__)

_USD_MICROS = 1_000_000


def usd_to_micros(amount_usd: str | int | float | Decimal) -> int:
    """Whole/fractional USD (config value) -> integer micro-dollars (floor)."""
    return int(Decimal(str(amount_usd)) * _USD_MICROS)


def _sum_usd_micros(rows: list[dict]) -> int:
    return sum(int(r.get("amount_usd_micros") or 0) for r in rows)


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
    total_sent_usd_micros: int = field(default=0)
    eth_usd_price: str | None = None
    aborted_reason: str | None = None


async def run_settlement_once(client: EthPayoutClient) -> SettlementResult:
    """One settlement pass: per approved provider, preview accrued USD
    earnings; pay out iff the preview clears COMMUNITY_MIN_PAYOUT_USD, the
    remaining per-run cap (COMMUNITY_MAX_PAYOUT_PER_RUN_USD, decremented as
    the run progresses so multiple providers can't collectively blow it),
    and the pool's current ETH balance minus PROVIDER_PAYOUT_GAS_RESERVE_WEI
    (also decremented as the run progresses). The preview is a cheap
    filter, not the authoritative amount -- see the module docstring's I4
    note: the real amount transferred is whatever `mark_earnings_settling`'s
    atomic flip actually claims, re-checked against the same three
    thresholds before any transfer is attempted.

    Aborts the whole run (paying no one) if the ETH/USD price can't be
    read or is stale -- see `aborted_reason`.

    Idempotent: a provider with an already-'pending' settlement (a
    previous run that crashed mid-flight) is skipped entirely rather than
    double-settled -- see reconcile_stuck_settlements for how that gets
    resolved automatically after COMMUNITY_SETTLEMENT_STUCK_HOURS.
    """
    result = SettlementResult()
    now = datetime.now(UTC)
    period_start = (now - timedelta(hours=Config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS)).isoformat()
    period_end = now.isoformat()

    min_payout_usd_micros = usd_to_micros(Config.COMMUNITY_MIN_PAYOUT_USD)
    remaining_cap_usd_micros = usd_to_micros(Config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD)

    try:
        price = await asyncio.to_thread(client.eth_usd_price)
        validate_price(price, Config.ETH_USD_PRICE_MAX_AGE_SECONDS)
    except StalePriceError as e:
        logger.error("settlement: %s -- aborting this run, nobody paid", e)
        result.aborted_reason = f"stale_price: {e}"
        return result
    except Exception as e:
        logger.error("settlement: ETH/USD price read failed, aborting this run: %s", e)
        result.aborted_reason = f"price_unavailable: {e}"
        return result
    result.eth_usd_price = str(price.usd_per_eth)
    price_updated_at_iso = datetime.fromtimestamp(price.updated_at, UTC).isoformat()

    try:
        pool_balance_wei = await asyncio.to_thread(client.pool_balance_wei)
    except Exception as e:
        logger.warning("settlement: pool_balance_wei() failed, aborting this run: %s", e)
        result.aborted_reason = f"pool_balance_unavailable: {e}"
        return result
    # Always leave gas money behind -- every transfer below pays its own gas
    # out of this same EOA.
    spendable_wei = max(0, pool_balance_wei - Config.PROVIDER_PAYOUT_GAS_RESERVE_WEI)

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
        preview_usd = _sum_usd_micros(preview_earnings)
        preview_wei = usd_micros_to_wei(preview_usd, price)

        if preview_usd < min_payout_usd_micros:
            result.providers_skipped_below_min += 1
            continue

        if preview_usd > remaining_cap_usd_micros:
            result.providers_skipped_cap += 1
            logger.warning(
                "settlement: provider %s's accrued %s USD micros exceeds the remaining "
                "per-run cap (%s USD micros) -- deferred to a future run",
                provider_id,
                preview_usd,
                remaining_cap_usd_micros,
            )
            continue

        if preview_wei > spendable_wei:
            result.providers_skipped_insufficient_pool += 1
            logger.error(
                "settlement: payout pool spendable balance (%s wei) insufficient for "
                "provider %s's %s wei -- deferred, NOT marked failed (earnings stay accrued)",
                spendable_wei,
                provider_id,
                preview_wei,
            )
            continue

        payout_wallet = provider.get("payout_wallet_address")
        if not payout_wallet:
            logger.warning(
                "settlement: provider %s is approved but has no payout_wallet_address -- skipping",
                provider_id,
            )
            continue

        settlement = create_settlement(
            provider_id,
            period_start,
            period_end,
            preview_usd,
            preview_wei,
            result.eth_usd_price,
            price_updated_at_iso,
        )
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
        total_usd = _sum_usd_micros(flipped)
        total_wei = usd_micros_to_wei(total_usd, price)
        if total_usd != preview_usd:
            update_settlement_amount(settlement_id, total_usd, total_wei)

        # Re-validate against the AUTHORITATIVE total -- a concurrent void
        # between the preview read and the atomic flip could have moved
        # this provider below/above a threshold since the preview.
        if total_usd < min_payout_usd_micros:
            mark_earnings_accrued(earning_ids, settlement_id)
            mark_settlement_failed(
                settlement_id, "fell below minimum payout after atomic reconciliation"
            )
            result.providers_skipped_below_min += 1
            continue
        if total_usd > remaining_cap_usd_micros:
            mark_earnings_accrued(earning_ids, settlement_id)
            mark_settlement_failed(
                settlement_id, "exceeded remaining per-run cap after atomic reconciliation"
            )
            result.providers_skipped_cap += 1
            continue
        if total_wei > spendable_wei:
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
        result.total_sent_usd_micros += total_usd
        spendable_wei -= total_wei
        remaining_cap_usd_micros -= total_usd

    return result


@dataclass
class ReconcileResult:
    settlements_checked: int = 0
    settlements_confirmed_sent: int = 0
    settlements_marked_failed: int = 0


async def reconcile_stuck_settlements(client: EthPayoutClient) -> ReconcileResult:
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
    no receipt found after being stuck this long (on Base's ~2s blocks,
    very likely dropped or never broadcast) -- mark the settlement failed
    and revert its earnings to 'accrued' so a future run retries them.

    This deliberately treats "no receipt found after 2h+" as a failure
    rather than waiting indefinitely. The runbook calls out the one real
    risk this creates: if the original transaction is somehow still in
    flight (e.g. a slow/congested RPC) and lands on-chain LATER, a
    provider whose earnings were reverted-and-retried would be paid
    twice. Operators should check the pool EOA's transaction history on
    Basescan for the recorded tx_hash before manually re-enabling
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
