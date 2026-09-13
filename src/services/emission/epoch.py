"""Daily emission_epoch job -- Chutes-style WAYZ emission rewards
(gatewayz-backend tokenomics -- boss asks: split WAYZ rewards between
stakers and GPU providers the way Chutes/Bittensor does). See
docs/tokenomics/EMISSION.md for the full design and worked example.

Only takes effect once Config.REWARDS_MODE == 'emission' (default
'per_unit' -- ships dark). While in emission mode, provider payouts move
from src/services/gpu/earnings.py's per-verified-work accrual to this
job's daily per-provider share, and staker payouts move from
src/services/staking_rewards.py's rate-table job to this job's daily
pro-rata share -- both switches are enforced by a REWARDS_MODE check at
the top of each of those other paths, not here, so the two payout modes
can never both be live for the same day.

Idempotent: emission_epochs.epoch_date (PRIMARY KEY) plus
provider_earnings' idx_provider_earnings_emission (UNIQUE(provider_id,
epoch_date) WHERE source='emission') and staking_reward_accruals' existing
UNIQUE(wallet_address, reward_date) together make a re-run of the same
epoch_date safe: get_epoch() is checked first (the primary,
application-level guard, same convention as
src/services/staking_rewards.py's get_accrual() check), and the two
per-row unique indexes are the DB-level backstop -- letting a crash
mid-run recover cleanly on retry (whatever was already written is a
no-op 'duplicate'/'skipped' outcome, and whatever wasn't gets created).

Amounts are Decimal end-to-end via src.services.emission.scoring's pure
functions; float only appears at the add_credits_to_user() call boundary
for the staker credits path (same boundary staking_rewards.py already
crosses).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from src.config.config import Config
from src.db.emission import (
    create_epoch,
    create_provider_scores,
    get_epoch,
    get_latest_epoch,
    get_latest_provider_score,
    list_provider_scores_for_epoch,
    list_verified_work_window,
)
from src.db.gpu import list_active_nodes
from src.db.gpu_payouts import create_emission_earning, get_payout_tiers, list_approved_providers
from src.db.staking_rewards import create_accrual, get_accrual
from src.db.user_wallets import get_wallet
from src.db.wallet_stakes import list_wallets_with_stake
from src.services.emission.scoring import (
    ProviderMetricsInput,
    median,
    score_providers,
    split_emission,
)
from src.services.gpu.earnings import effective_model_class, tier_multiplier_bps
from src.services.staking_rewards import (
    StakingRewardsStaleError,
    is_stake_sync_stale,
    pay_pending_accrual,
)

logger = logging.getLogger(__name__)

_WEI_PER_WAYZ = Decimal(10) ** 18
_CREDITS_DP = Decimal("0.000001")  # 6 dp, matches staking_reward_accruals.credits numeric(18,6)
_WINDOW_DAYS = 7
_WINDOW_HOURS = _WINDOW_DAYS * 24

# Model-class -> compute weight (spec: small=1, medium=2, large=4). An
# unknown/not-on-the-allow-list model (effective_model_class returns None)
# contributes 0 to compute -- same "not payable" treatment
# src/services/gpu/earnings.py gives it for per_unit payouts.
_CLASS_WEIGHTS = {"small": 1, "medium": 2, "large": 4}

# Set by check_emission_config() (called from src/services/startup.py's
# lifespan). None means "config is valid"; any string is the reason the
# job is refusing to run -- logged loudly at startup, and surfaced in
# run_emission_epoch's 'skipped' result rather than a boot crash.
_config_disabled_reason: str | None = None


def check_emission_config() -> None:
    """Validate that both emission bps splits (the reward-pool split and
    the provider-score weights) sum to 10000. Never raises -- a bad sum
    is logged at ERROR and the job marks itself disabled until the env
    vars are fixed and the app restarts, mirroring
    src/db/gpu_payouts.py::check_payout_tiers_seeded's "loud warning, not
    a crash" convention for a startup-time config sanity check."""
    global _config_disabled_reason
    reward_sum = (
        Config.EMISSION_SPLIT_PROVIDERS_BPS
        + Config.EMISSION_SPLIT_STAKERS_BPS
        + Config.EMISSION_SPLIT_TREASURY_BPS
    )
    weight_sum = sum(Config.emission_score_weights_bps().values())

    problems = []
    if reward_sum != 10000:
        problems.append(
            f"EMISSION_SPLIT_PROVIDERS_BPS + EMISSION_SPLIT_STAKERS_BPS + "
            f"EMISSION_SPLIT_TREASURY_BPS = {reward_sum}, not 10000"
        )
    if weight_sum != 10000:
        problems.append(
            f"PROVIDER_SCORE_WEIGHT_{{COMPUTE,SPEED,AVAILABILITY,UNIQUE_MODELS}}_BPS sum "
            f"to {weight_sum}, not 10000"
        )

    if problems:
        _config_disabled_reason = "; ".join(problems)
        logger.error(
            "emission_epoch job DISABLED at startup -- %s. No WAYZ will be split or paid "
            "via the emission path until the env vars are corrected and the app restarts.",
            _config_disabled_reason,
        )
    else:
        _config_disabled_reason = None
        logger.info("emission_epoch config OK (both bps splits sum to 10000)")


def is_emission_job_disabled() -> str | None:
    """The reason the job is disabled, or None if config is valid --
    exposed for GET /admin/emission/config."""
    return _config_disabled_reason


# ---------------------------------------------------------------------------
# Provider scoring
# ---------------------------------------------------------------------------


def _hour_bucket(created_at: str | None) -> str | None:
    """A cheap hour-bucket key from an ISO timestamp string (e.g.
    '2026-09-06T12:34:56.789+00:00' -> '2026-09-06T12'), used only to
    count DISTINCT hours a provider had activity in -- not for display.
    Assumes UTC (provider_work.created_at is timestamptz, returned by
    PostgREST already in UTC), same assumption src/db/gpu_rollups.py's
    hourly rollup makes."""
    if not created_at:
        return None
    return str(created_at)[:13]


def _empty_bucket() -> dict[str, Any]:
    return {
        "compute_raw": Decimal(0),
        "latencies": [],
        "hours": set(),
        "models": set(),
        "volume": 0,
    }


def _aggregate_provider_buckets(
    work_rows: list[dict[str, Any]], reference_provider_configured: bool
) -> dict[int, dict[str, Any]]:
    """Group the trailing-window provider_work rows by provider_id into
    the raw per-provider aggregates score_providers() needs -- pure over
    its input, no I/O."""
    buckets: dict[int, dict[str, Any]] = {}
    for row in work_rows:
        provider_id = row.get("provider_id")
        if provider_id is None:
            continue
        bucket = buckets.setdefault(provider_id, _empty_bucket())

        tokens = (row.get("prompt_tokens") or 0) + (row.get("completion_tokens") or 0)
        model = row.get("model") or ""
        effective_class = effective_model_class(
            model, bool(row.get("attested")), reference_provider_configured
        )
        weight = _CLASS_WEIGHTS.get(effective_class, 0)
        bucket["compute_raw"] += Decimal(tokens) * weight
        bucket["volume"] += tokens
        if model:
            bucket["models"].add(model)

        latency_ms = row.get("latency_ms")
        if latency_ms is not None:
            bucket["latencies"].append(Decimal(latency_ms))

        hour_key = _hour_bucket(row.get("created_at"))
        if hour_key is not None:
            bucket["hours"].add(hour_key)

    return buckets


def _build_provider_metrics(
    eligible_ids: set[int], buckets: dict[int, dict[str, Any]]
) -> list[ProviderMetricsInput]:
    metrics = []
    for provider_id in sorted(eligible_ids):
        bucket = buckets.get(provider_id, _empty_bucket())
        p50 = median(bucket["latencies"]) if bucket["latencies"] else None
        metrics.append(
            ProviderMetricsInput(
                provider_id=provider_id,
                compute_raw=bucket["compute_raw"],
                p50_latency_ms=p50,
                active_hours=len(bucket["hours"]),
                window_hours=_WINDOW_HOURS,
                unique_models=len(bucket["models"]),
                volume_7d_tokens=bucket["volume"],
            )
        )
    return metrics


def _score_row_for_db(epoch_date_str: str, result) -> dict[str, Any]:
    return {
        "epoch_date": epoch_date_str,
        "provider_id": result.provider_id,
        "compute": str(result.compute),
        "speed": str(result.speed),
        "availability": str(result.availability),
        "unique_models": str(result.unique_models),
        "raw_score": str(result.raw_score),
        "adjusted_score": str(result.adjusted_score),
        "share": str(result.share),
        "allocation_wei": str(result.allocation_wei),
        "tier_multiplier_bps": result.tier_multiplier_bps,
        "details": {},
    }


def _score_and_persist_providers(
    epoch_date_str: str, window_start_iso: str, window_end_iso: str, providers_wei: int
) -> tuple[list, int]:
    """Score every eligible provider over [window_start_iso, window_end_iso)
    and persist provider_scores + one provider_earnings(source='emission')
    row per provider with a nonzero allocation. Returns (per-provider
    score results, dust_wei to fold into treasury_wei)."""
    work_rows = list_verified_work_window(window_start_iso, window_end_iso)
    reference_provider_configured = bool(Config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER)
    buckets = _aggregate_provider_buckets(work_rows, reference_provider_configured)

    approved_provider_ids = {p["id"] for p in list_approved_providers()}
    active_node_provider_ids = {n["provider_id"] for n in list_active_nodes()}
    # Eligibility (spec): approved providers with >=1 verified work OR >=1
    # active node in the window.
    eligible_ids = approved_provider_ids & (set(buckets) | active_node_provider_ids)

    if not eligible_ids:
        logger.info(
            "emission_epoch: no eligible providers for %s -- the entire providers_wei "
            "pool (%s wei) is dust routed to treasury",
            epoch_date_str,
            providers_wei,
        )
        return [], providers_wei

    metrics = _build_provider_metrics(eligible_ids, buckets)
    tiers = get_payout_tiers()
    results, dust_wei = score_providers(
        metrics,
        providers_wei,
        Config.emission_score_weights_bps(),
        Config.PROVIDER_SCORE_EXPONENT_ABOVE_MEDIAN,
        tier_multiplier_bps,
        tiers,
    )

    score_rows = [_score_row_for_db(epoch_date_str, r) for r in results]
    if not create_provider_scores(score_rows):
        logger.warning(
            "emission_epoch: provider_scores bulk insert failed for %s (%d rows) -- "
            "provider_earnings allocations are still attempted below",
            epoch_date_str,
            len(score_rows),
        )

    for r in results:
        if r.allocation_wei <= 0:
            continue
        _earning, outcome = create_emission_earning(r.provider_id, epoch_date_str, r.allocation_wei)
        if outcome == "db_error":
            logger.warning(
                "emission_epoch: failed to accrue emission earning for provider %s/%s "
                "(%s wei) -- rerun via POST /admin/emission/run once the DB issue is fixed",
                r.provider_id,
                epoch_date_str,
                r.allocation_wei,
            )

    return results, dust_wei


# ---------------------------------------------------------------------------
# Staker payout (pro-rata to stake, out of the day's stakers_wei)
# ---------------------------------------------------------------------------


def _process_emission_wallet(
    wallet_address: str,
    epoch_date_str: str,
    staked_amount_wei: int,
    wayz_amount_wei: int,
    credit_rate: Decimal,
) -> tuple[str, bool, Decimal]:
    """One wallet's emission-mode payout for one epoch. Same
    insert-before-pay idempotency pattern as
    src/services/staking_rewards.py::_process_wallet_for_date (a 'pending'
    accrual row is written FIRST, then paid via pay_pending_accrual()) --
    reused here rather than duplicated. Returns
    (outcome, was_capped, credits_paid)."""
    existing = get_accrual(wallet_address, epoch_date_str)
    if existing is not None and existing["status"] != "pending":
        return existing["status"], False, Decimal(0)

    was_capped = False

    if existing is None:
        if Config.STAKER_REWARD_ASSET == "wayz":
            # No payout rail for raw WAYZ yet -- record the accrual as a
            # documented, visible limitation rather than silently dropping
            # it or crediting the wrong asset. See docs/tokenomics/EMISSION.md.
            create_accrual(
                wallet_address,
                epoch_date_str,
                str(staked_amount_wei),
                rate_id=None,
                credits="0",
                status="pending",
                source="emission",
                wayz_amount_wei=str(wayz_amount_wei),
                skip_reason="wayz_payout_not_implemented",
            )
            return "pending", False, Decimal(0)

        credits = (Decimal(wayz_amount_wei) / _WEI_PER_WAYZ * credit_rate).quantize(
            _CREDITS_DP, rounding=ROUND_DOWN
        )

        cap = Decimal(str(Config.STAKING_REWARDS_DAILY_CAP_CREDITS))
        if credits > cap:
            credits = cap.quantize(_CREDITS_DP, rounding=ROUND_DOWN)
            was_capped = True

        if credits < Decimal(str(Config.STAKING_REWARDS_MIN_CREDITS)):
            create_accrual(
                wallet_address,
                epoch_date_str,
                str(staked_amount_wei),
                rate_id=None,
                credits=str(credits),
                status="skipped",
                skip_reason="below_min",
                source="emission",
                wayz_amount_wei=str(wayz_amount_wei),
            )
            return "skipped", False, Decimal(0)

        wallet_row = get_wallet(wallet_address)
        user_id = None
        if wallet_row is not None and wallet_row.get("is_active") is not False:
            user_id = wallet_row["user_id"]

        created = create_accrual(
            wallet_address,
            epoch_date_str,
            str(staked_amount_wei),
            rate_id=None,
            credits=str(credits),
            status="pending",
            user_id=user_id,
            source="emission",
            wayz_amount_wei=str(wayz_amount_wei),
        )
        if created is None:
            # Lost a race with a concurrent run -- re-read rather than
            # assume anything about what the winner did.
            existing = get_accrual(wallet_address, epoch_date_str)
            if existing is None:
                return "skipped", False, Decimal(0)
            if existing["status"] != "pending":
                return existing["status"], False, Decimal(0)
        else:
            existing = created

    if existing.get("user_id") is None:
        return "pending", was_capped, Decimal(0)

    outcome, paid_credits = pay_pending_accrual(existing, capped=was_capped)
    return outcome, was_capped, paid_credits


def _pay_stakers(epoch_date_str: str, stakers_wei: int) -> dict[str, Any]:
    """Pay stakers_wei out pro-rata to each wallet's share of total staked
    WAYZ. Unlinked wallets fall to 'pending' exactly like the per_unit
    rate-table job -- see docs/staking/REWARDS.md."""
    wallets = list_wallets_with_stake()
    total_staked_wei = sum(int(w["staked_amount"]) for w in wallets)

    counts = {"paid": 0, "pending": 0, "skipped": 0}
    credits_paid = Decimal(0)
    capped = 0

    if total_staked_wei <= 0 or stakers_wei <= 0:
        return {
            "paid": 0,
            "pending": 0,
            "skipped": 0,
            "credits_paid": "0",
            "capped": 0,
            "asset": Config.STAKER_REWARD_ASSET,
        }

    credit_rate = Decimal(str(Config.WAYZ_CREDIT_RATE))

    for wallet in wallets:
        address = wallet["wallet_address"]
        staked_wei = int(wallet["staked_amount"])
        if staked_wei <= 0:
            continue
        share = Decimal(staked_wei) / Decimal(total_staked_wei)
        wayz_amount_wei = int((Decimal(stakers_wei) * share).to_integral_value(rounding=ROUND_DOWN))

        try:
            outcome, was_capped, paid_credits = _process_emission_wallet(
                address, epoch_date_str, staked_wei, wayz_amount_wei, credit_rate
            )
        except Exception as e:
            logger.warning("emission_epoch: staker wallet %s failed: %s", address, e)
            outcome, was_capped, paid_credits = "pending", False, Decimal(0)

        counts[outcome] = counts.get(outcome, 0) + 1
        if was_capped:
            capped += 1
        if outcome == "paid":
            credits_paid += paid_credits

    return {
        "paid": counts["paid"],
        "pending": counts["pending"],
        "skipped": counts["skipped"],
        "credits_paid": str(credits_paid),
        "capped": capped,
        "asset": Config.STAKER_REWARD_ASSET,
    }


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def run_emission_epoch(epoch_date: date | None = None) -> dict[str, Any]:
    """Run one pass of the daily emission_epoch job for `epoch_date`
    (default: yesterday UTC). Idempotent -- see module docstring. Raises
    StakingRewardsStaleError if the on-chain stake sync is too stale to
    trust today's staker split (same guard staking_rewards.py's own job
    uses, since this job's staker path reads the exact same
    wallet_stakes table); callers turn that into a failed job run / a 409,
    never a crash."""
    if Config.REWARDS_MODE != "emission":
        return {"skipped": "disabled"}
    if _config_disabled_reason is not None:
        return {"skipped": "bad_config", "reason": _config_disabled_reason}

    effective_date = epoch_date or (datetime.now(UTC).date() - timedelta(days=1))
    epoch_date_str = effective_date.isoformat()

    existing = get_epoch(epoch_date_str)
    if existing is not None:
        return {"skipped": "already_run", "epoch_date": epoch_date_str, "epoch": existing}

    if is_stake_sync_stale():
        raise StakingRewardsStaleError("stake_sync_stale")

    split = split_emission(
        Config.WAYZ_DAILY_EMISSION,
        Config.EMISSION_SPLIT_PROVIDERS_BPS,
        Config.EMISSION_SPLIT_STAKERS_BPS,
        Config.EMISSION_SPLIT_TREASURY_BPS,
    )

    window_end = datetime(
        effective_date.year, effective_date.month, effective_date.day, tzinfo=UTC
    ) + timedelta(days=1)
    window_start = window_end - timedelta(days=_WINDOW_DAYS)

    provider_results, provider_dust_wei = _score_and_persist_providers(
        epoch_date_str, window_start.isoformat(), window_end.isoformat(), split.providers_wei
    )
    staker_summary = _pay_stakers(epoch_date_str, split.stakers_wei)

    treasury_wei = split.treasury_wei + provider_dust_wei
    top_share = max((r.share for r in provider_results), default=Decimal(0))

    summary = {
        "epoch_date": epoch_date_str,
        "emission_wayz": str(Config.WAYZ_DAILY_EMISSION),
        "emission_wei": str(split.emission_wei),
        "split": {
            "providers_wei": str(split.providers_wei),
            "stakers_wei": str(split.stakers_wei),
            "treasury_wei": str(treasury_wei),
        },
        "providers_scored": len(provider_results),
        "top_share": str(top_share),
        "stakers_paid": staker_summary["paid"],
        "stakers_pending": staker_summary["pending"],
        "stakers_skipped": staker_summary["skipped"],
        "credits_paid": staker_summary["credits_paid"],
        "staker_asset": staker_summary["asset"],
        "dust_wei": str(provider_dust_wei),
    }

    epoch_row = create_epoch(
        epoch_date_str,
        split.emission_wei,
        split.providers_wei,
        split.stakers_wei,
        treasury_wei,
        len(provider_results),
        staker_summary["paid"],
        status="allocated",
        summary=summary,
    )
    if epoch_row is None:
        logger.warning(
            "emission_epoch: failed to persist the emission_epochs summary row for %s -- "
            "providers/stakers were still allocated (see this run's summary); a retry will "
            "attempt the write again since get_epoch() found nothing for this date",
            epoch_date_str,
        )

    return summary


# ---------------------------------------------------------------------------
# Read views for the routes
# ---------------------------------------------------------------------------


def get_provider_emission_view(provider_id: int) -> dict[str, Any] | None:
    """The `emission` block for GET /gpu/providers/me/earnings, or None if
    this provider has never been scored (feature off, or provider too new/
    inactive to have qualified for any epoch yet)."""
    latest = get_latest_provider_score(provider_id)
    if latest is None:
        return None

    epoch_date_str = latest["epoch_date"]
    epoch_scores = list_provider_scores_for_epoch(epoch_date_str)  # already share-desc ordered
    rank = next(
        (i + 1 for i, row in enumerate(epoch_scores) if row["provider_id"] == provider_id),
        None,
    )

    return {
        "last_epoch": epoch_date_str,
        "score": {
            "compute": latest["compute"],
            "speed": latest["speed"],
            "availability": latest["availability"],
            "unique_models": latest["unique_models"],
            "raw": latest["raw_score"],
            "adjusted": latest["adjusted_score"],
            "share": latest["share"],
        },
        "allocation_wayz": str(Decimal(str(latest["allocation_wei"])) / _WEI_PER_WAYZ),
        "rank": rank,
        "providers_scored": len(epoch_scores),
    }


def get_staker_emission_view(total_staked_wei: int) -> dict[str, Any] | None:
    """The `emission` block for GET /staking/rewards, or None if the
    feature is off (mode != 'emission') or no epoch has run yet.
    `total_staked_wei` is the caller's own linked-wallet total (0 if none)
    -- this function has no user/wallet-link lookups of its own, matching
    src/services/staking_rewards.py::estimate_rewards_for_stake's
    "safe on data the caller already scoped" convention."""
    if Config.REWARDS_MODE != "emission":
        return None
    latest = get_latest_epoch()
    if latest is None:
        return None

    stakers_wei = int(latest["stakers_wei"])
    wallets = list_wallets_with_stake()
    network_staked_wei = sum(int(w["staked_amount"]) for w in wallets)

    your_share = Decimal(0)
    estimated_credits_per_day = Decimal(0)
    if network_staked_wei > 0 and total_staked_wei > 0:
        your_share = Decimal(total_staked_wei) / Decimal(network_staked_wei)
        your_wayz_wei = int(
            (Decimal(stakers_wei) * your_share).to_integral_value(rounding=ROUND_DOWN)
        )
        if Config.STAKER_REWARD_ASSET == "credits":
            credit_rate = Decimal(str(Config.WAYZ_CREDIT_RATE))
            estimated_credits_per_day = (
                Decimal(your_wayz_wei) / _WEI_PER_WAYZ * credit_rate
            ).quantize(_CREDITS_DP, rounding=ROUND_DOWN)

    return {
        "daily_emission_wayz": str(Config.WAYZ_DAILY_EMISSION),
        "stakers_share_bps": Config.EMISSION_SPLIT_STAKERS_BPS,
        "your_share": str(your_share),
        "estimated_credits_per_day": str(estimated_credits_per_day),
    }


def get_public_emission_summary() -> dict[str, Any]:
    """The `emission` block for GET /gpu/public/summary -- aggregate-only,
    no per-provider or per-user data (mode/split config + the most recent
    epoch_date, nothing else)."""
    latest = get_latest_epoch()
    return {
        "mode": Config.REWARDS_MODE,
        "daily_emission_wayz": str(Config.WAYZ_DAILY_EMISSION),
        "providers_bps": Config.EMISSION_SPLIT_PROVIDERS_BPS,
        "stakers_bps": Config.EMISSION_SPLIT_STAKERS_BPS,
        "treasury_bps": Config.EMISSION_SPLIT_TREASURY_BPS,
        "last_epoch": latest["epoch_date"] if latest else None,
    }
