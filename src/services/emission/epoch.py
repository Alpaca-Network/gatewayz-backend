"""Daily emission_epoch job -- score-based GPU provider emission rewards.
See docs/tokenomics/EMISSION.md for the full design and worked example.

The emission is split between GPU providers and the treasury. A staker
share existed in an earlier design and was removed on 2026-11-25.

Only takes effect once Config.REWARDS_MODE == 'emission' (default
'per_unit' -- ships dark). While in emission mode, provider payouts move
from src/services/gpu/earnings.py's per-verified-work accrual to this
job's daily per-provider share. The provider switch is enforced by a
REWARDS_MODE check at the top of that other path, not here, so the two
provider payout modes can never both be live for the same day.

Idempotent: emission_epochs.epoch_date (PRIMARY KEY) plus
provider_earnings' idx_provider_earnings_emission (UNIQUE(provider_id,
epoch_date) WHERE source='emission') together make a re-run of the same
epoch_date safe: get_epoch() is checked first (the primary,
application-level guard), and the per-row unique index is the DB-level
backstop -- letting a crash mid-run recover cleanly on retry (whatever
was already written is a no-op 'duplicate' outcome, and whatever wasn't
gets created).

**Provider payouts are USD, paid in ETH (2026-09-22):** WAYZ is not
going public for now, so providers do not receive the WAYZ providers
leg. Instead Config.PROVIDER_EMISSION_USD_PER_DAY (integer USD
micro-dollars internally) is allocated by the 7-day score, written as
provider_earnings.amount_usd_micros, and paid in native ETH on Base by
src/services/gpu/settlement.py. The unpaid WAYZ providers leg is
recorded as providers_wei=0 and folded into treasury_wei, so
providers + treasury == emission_wei holds exactly.

Amounts are Decimal end-to-end via src.services.emission.scoring's pure
functions.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
from src.services.emission.scoring import (
    ProviderMetricsInput,
    median,
    score_providers,
    split_emission,
)
from src.services.gpu.earnings import effective_model_class, tier_multiplier_bps

logger = logging.getLogger(__name__)

_WEI_PER_WAYZ = Decimal(10) ** 18
_USD_MICROS = Decimal(1_000_000)
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
    reward_sum = Config.EMISSION_SPLIT_PROVIDERS_BPS + Config.EMISSION_SPLIT_TREASURY_BPS
    weight_sum = sum(Config.emission_score_weights_bps().values())

    problems = []
    if reward_sum != 10000:
        problems.append(
            f"EMISSION_SPLIT_PROVIDERS_BPS + EMISSION_SPLIT_TREASURY_BPS = "
            f"{reward_sum}, not 10000"
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
        # score_providers is unit-agnostic; the pool handed to it is USD
        # micros, so its `allocation_wei` field is USD micros here.
        "allocation_wei": "0",
        "allocation_usd_micros": result.allocation_wei,
        "tier_multiplier_bps": result.tier_multiplier_bps,
        "details": {},
    }


def _score_and_persist_providers(
    epoch_date_str: str, window_start_iso: str, window_end_iso: str, providers_usd_micros: int
) -> tuple[list, int]:
    """Score every eligible provider over [window_start_iso, window_end_iso)
    and persist provider_scores + one provider_earnings(source='emission')
    row per provider with a nonzero allocation of the day's USD pool.
    Returns (per-provider score results, unallocated dust in USD micros)."""
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
            "emission_epoch: no eligible providers for %s -- the provider USD pool "
            "(%s USD micros) goes unallocated",
            epoch_date_str,
            providers_usd_micros,
        )
        return [], providers_usd_micros

    metrics = _build_provider_metrics(eligible_ids, buckets)
    tiers = get_payout_tiers()
    results, dust_usd_micros = score_providers(
        metrics,
        providers_usd_micros,
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
                "(%s USD micros) -- rerun via POST /admin/emission/run once the DB issue "
                "is fixed",
                r.provider_id,
                epoch_date_str,
                r.allocation_wei,
            )

    return results, dust_usd_micros


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def run_emission_epoch(epoch_date: date | None = None) -> dict[str, Any]:
    """Run one pass of the daily emission_epoch job for `epoch_date`
    (default: yesterday UTC). Idempotent -- see module docstring."""
    if Config.REWARDS_MODE != "emission":
        return {"skipped": "disabled"}
    if _config_disabled_reason is not None:
        return {"skipped": "bad_config", "reason": _config_disabled_reason}

    effective_date = epoch_date or (datetime.now(UTC).date() - timedelta(days=1))
    epoch_date_str = effective_date.isoformat()

    existing = get_epoch(epoch_date_str)
    if existing is not None:
        return {"skipped": "already_run", "epoch_date": epoch_date_str, "epoch": existing}

    split = split_emission(
        Config.WAYZ_DAILY_EMISSION,
        Config.EMISSION_SPLIT_PROVIDERS_BPS,
        Config.EMISSION_SPLIT_TREASURY_BPS,
    )

    window_end = datetime(
        effective_date.year, effective_date.month, effective_date.day, tzinfo=UTC
    ) + timedelta(days=1)
    window_start = window_end - timedelta(days=_WINDOW_DAYS)

    provider_pool_usd_micros = int(Decimal(str(Config.PROVIDER_EMISSION_USD_PER_DAY)) * _USD_MICROS)
    provider_results, provider_dust_usd_micros = _score_and_persist_providers(
        epoch_date_str, window_start.isoformat(), window_end.isoformat(), provider_pool_usd_micros
    )
    providers_usd_micros = provider_pool_usd_micros - provider_dust_usd_micros

    # Persist the ACTUAL allocated WAYZ amounts. Providers are paid from the
    # USD pool above, so the WAYZ providers leg is not emitted to anyone:
    # it's recorded as providers_wei=0 and lands in treasury, so
    # providers_wei + treasury_wei always sums to exactly emission_wei.
    provider_dust_wei = split.providers_wei
    providers_wei = 0
    treasury_wei = split.treasury_wei + provider_dust_wei
    top_share = max((r.share for r in provider_results), default=Decimal(0))

    summary = {
        "epoch_date": epoch_date_str,
        "emission_wayz": str(Config.WAYZ_DAILY_EMISSION),
        "emission_wei": str(split.emission_wei),
        "split": {
            "providers_wei": str(providers_wei),
            "treasury_wei": str(treasury_wei),
        },
        "providers_scored": len(provider_results),
        "top_share": str(top_share),
        "dust_wei": str(provider_dust_wei),
        "provider_dust_wei": str(provider_dust_wei),
        "provider_payout_asset": "ETH",
        "provider_pool_usd": str(Config.PROVIDER_EMISSION_USD_PER_DAY),
        "providers_usd_micros": providers_usd_micros,
        "provider_dust_usd_micros": provider_dust_usd_micros,
    }

    epoch_row = create_epoch(
        epoch_date_str,
        split.emission_wei,
        providers_wei,
        treasury_wei,
        len(provider_results),
        status="allocated",
        summary=summary,
        providers_usd_micros=providers_usd_micros,
    )
    if epoch_row is None:
        logger.warning(
            "emission_epoch: failed to persist the emission_epochs summary row for %s -- "
            "providers were still allocated (see this run's summary); a retry will "
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

    allocation_usd_micros = latest.get("allocation_usd_micros")
    allocation_usd = allocation_eth = legacy_wayz = None
    if allocation_usd_micros is not None:
        from src.services.gpu.payout_views import (
            allocation_eth_equivalent,
            get_display_eth_usd_price,
        )

        allocation_usd = str(Decimal(int(allocation_usd_micros)) / _USD_MICROS)
        allocation_eth = allocation_eth_equivalent(
            int(allocation_usd_micros), get_display_eth_usd_price()
        )
    else:
        legacy_wayz = str(Decimal(str(latest.get("allocation_wei") or 0)) / _WEI_PER_WAYZ)

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
        "allocation_usd": allocation_usd,
        "allocation_eth": allocation_eth,
        # Deprecated alias kept for existing clients (PR #2364 review):
        # the ETH equivalent for USD-paid epochs, WAYZ for legacy epochs.
        "allocation_wayz": allocation_eth if allocation_usd_micros is not None else legacy_wayz,
        "payout_asset": "ETH",
        "rank": rank,
        "providers_scored": len(epoch_scores),
    }


def get_public_emission_summary() -> dict[str, Any]:
    """The `emission` block for GET /gpu/public/summary -- aggregate-only,
    no per-provider or per-user data (mode/split config + the most recent
    epoch_date, nothing else)."""
    latest = get_latest_epoch()
    return {
        "mode": Config.REWARDS_MODE,
        "daily_emission_wayz": str(Config.WAYZ_DAILY_EMISSION),
        "provider_pool_usd_per_day": str(Config.PROVIDER_EMISSION_USD_PER_DAY),
        "provider_payout_asset": "ETH",
        "providers_bps": Config.EMISSION_SPLIT_PROVIDERS_BPS,
        "treasury_bps": Config.EMISSION_SPLIT_TREASURY_BPS,
        "last_epoch": latest["epoch_date"] if latest else None,
    }
