"""Pure scoring/split math for Chutes-style WAYZ emission rewards
(gatewayz-backend tokenomics -- boss asks: split WAYZ rewards between
stakers and GPU providers the way Chutes/Bittensor does). See
docs/tokenomics/EMISSION.md for the worked example and the Chutes/
Bittensor references this mirrors:
https://chutes.ai/docs/miner-resources/scoring and
https://docs.learnbittensor.org/learn/emissions.

No DB, no I/O, no Config reads -- every function here is a pure function
over plain values so it can be exhaustively unit tested (median/exponent
boundaries, a single provider, a zero-activity provider, dust routing)
without mocking Supabase. src/services/emission/epoch.py is the only
caller; it does the DB reads, builds the inputs below from them, and
persists the outputs.

Decimal end-to-end. The only integer-truncation points are the two
explicit floor-to-wei conversions (split_emission's bps split,
score_providers' per-provider allocation) -- both documented at the call
site, and both route their rounding remainder ("dust") to the caller as
an explicit return value rather than losing it silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

_WEI_PER_WAYZ = Decimal(10) ** 18
_BPS_DENOMINATOR = Decimal(10000)


def wayz_to_wei(amount_wayz: str | int | float | Decimal) -> int:
    """Floor amount_wayz (whole or fractional WAYZ) to an integer wei
    amount. str(amount_wayz) first so a caller passing a float doesn't
    smuggle binary-float imprecision into the Decimal conversion."""
    return int((Decimal(str(amount_wayz)) * _WEI_PER_WAYZ).to_integral_value(rounding=ROUND_DOWN))


@dataclass(frozen=True)
class EmissionSplit:
    emission_wei: int
    providers_wei: int
    stakers_wei: int
    treasury_wei: int


def split_emission(
    daily_emission_wayz: str | int | float | Decimal,
    providers_bps: int,
    stakers_bps: int,
    treasury_bps: int,
) -> EmissionSplit:
    """Split one day's WAYZ emission into (providers, stakers, treasury)
    wei amounts. providers_wei and stakers_wei are each floor(emission_wei
    * bps / 10000); treasury_wei is whatever is left over
    (emission_wei - providers_wei - stakers_wei) rather than its own floor
    division -- this both implements the spec's "dust -> treasury" rule
    and guarantees the three always sum to exactly emission_wei regardless
    of whether providers_bps + stakers_bps + treasury_bps happens to equal
    10000 (that sum is validated separately at startup -- see
    src/services/emission/epoch.py::check_emission_config -- this function
    itself never raises on a bad sum, it just floors what it's given)."""
    emission_wei = wayz_to_wei(daily_emission_wayz)
    providers_wei = (emission_wei * providers_bps) // 10000
    stakers_wei = (emission_wei * stakers_bps) // 10000
    treasury_wei = emission_wei - providers_wei - stakers_wei
    return EmissionSplit(
        emission_wei=emission_wei,
        providers_wei=providers_wei,
        stakers_wei=stakers_wei,
        treasury_wei=treasury_wei,
    )


def median(values: list[Decimal]) -> Decimal:
    """The median of a list of Decimals -- average of the two middle
    values on an even-length list. Decimal(0) for an empty list (callers
    treat "no data" as a zero score contribution, never as an error)."""
    if not values:
        return Decimal(0)
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def normalize_by_max(values: dict[int, Decimal]) -> dict[int, Decimal]:
    """Each value divided by the max value in the set (0..1 range), or all
    zeros if every value is <= 0 (avoids a division by zero when nobody in
    the window has done anything on this metric)."""
    if not values:
        return {}
    peak = max(values.values())
    if peak <= 0:
        return dict.fromkeys(values, Decimal(0))
    return {provider_id: value / peak for provider_id, value in values.items()}


def speed_score(provider_p50_ms: Decimal | None, network_median_p50_ms: Decimal | None) -> Decimal:
    """1.0 at or below the network's median p50 latency, linearly
    penalized down to 0.0 at 2x the median or worse (per spec: "faster
    than median -> 1; twice median -> 0"). Decimal(0) if either latency is
    unavailable (no verified work with a latency sample this window) --
    "no data" is treated as the worst score, not skipped, so a provider
    can't improve its score just by omitting latency."""
    if provider_p50_ms is None or network_median_p50_ms is None or network_median_p50_ms <= 0:
        return Decimal(0)
    ratio = provider_p50_ms / network_median_p50_ms
    penalty = min(max(ratio - Decimal(1), Decimal(0)), Decimal(1))
    return Decimal(1) - penalty


@dataclass(frozen=True)
class ProviderMetricsInput:
    """One provider's raw trailing-7-day metrics -- the input
    src/services/emission/epoch.py builds from provider_work +
    gpu_nodes before calling score_providers(). Eligibility (>=1 verified
    work OR >=1 active node in the window) is decided by the caller before
    this is constructed; a provider with zero activity is represented by
    an all-zero/None instance, not by omission, so it still gets a
    provider_scores row (share=0) for transparency."""

    provider_id: int
    compute_raw: Decimal  # Sigma(verified tokens * model-class weight)
    p50_latency_ms: Decimal | None  # None if no latency samples this window
    active_hours: int  # hours in the window with >=1 completed work item
    window_hours: int  # e.g. 168 for a 7-day window
    unique_models: int  # distinct models served (verified) this window
    volume_7d_tokens: int  # trailing-7d verified token volume, for the payout tier lookup


@dataclass(frozen=True)
class ProviderScoreResult:
    provider_id: int
    compute: Decimal
    speed: Decimal
    availability: Decimal
    unique_models: Decimal
    raw_score: Decimal
    adjusted_score: Decimal
    tier_multiplier_bps: int
    share: Decimal
    allocation_wei: int


def score_providers(
    metrics: list[ProviderMetricsInput],
    providers_wei: int,
    weights_bps: dict[str, int],
    exponent_above_median: str | float | Decimal,
    tier_multiplier_bps_for: callable[[int, list], int],
    tiers: list[dict],
) -> tuple[list[ProviderScoreResult], int]:
    """Score every provider in `metrics` and allocate providers_wei across
    them by share. Returns (per-provider results -- one per input, in the
    same order, including zero-activity providers with share=0 -- and the
    wei left over after flooring every provider's allocation, which the
    caller folds into treasury_wei rather than losing).

    Steps (per docs/tokenomics/EMISSION.md, mirroring Chutes'
    miner-scoring formula):
      1. compute/unique_models are normalized 0..1 against the max across
         this epoch's providers; speed is normalized against the network's
         median p50 latency (not a max -- see speed_score); availability
         is already 0..1 (active_hours / window_hours).
      2. raw_score = Sigma(weight_i * metric_i), weight_i = weights_bps[i] / 10000.
      3. adjusted_score = raw_score ** exponent_above_median for any
         provider whose raw_score >= the median raw_score across this
         epoch's providers (boundary is inclusive -- "at the median" counts
         as above it); everyone else keeps their raw_score unchanged.
      4. adjusted_score *= tier_multiplier_bps / 10000 -- the SAME
         sliding-scale volume tiers per_unit payouts use (provider_payout_tiers,
         src/services/gpu/earnings.py::tier_multiplier_bps), looked up by
         this provider's trailing-7d verified token volume.
      5. share_i = adjusted_score_i / Sigma(adjusted_score) (0 if the total
         is 0 -- e.g. every provider in the window had zero activity);
         allocation_wei_i = floor(providers_wei * share_i).

    An empty `metrics` list returns ([], providers_wei) -- nothing to
    score, so the entire pool is dust for the caller to route to treasury.
    """
    if not metrics:
        return [], providers_wei

    exponent = Decimal(str(exponent_above_median))

    compute_norm = normalize_by_max({m.provider_id: m.compute_raw for m in metrics})
    unique_norm = normalize_by_max({m.provider_id: Decimal(m.unique_models) for m in metrics})

    p50_samples = [m.p50_latency_ms for m in metrics if m.p50_latency_ms is not None]
    network_median_p50 = median(p50_samples) if p50_samples else None

    w_compute = Decimal(weights_bps.get("compute", 0)) / _BPS_DENOMINATOR
    w_speed = Decimal(weights_bps.get("speed", 0)) / _BPS_DENOMINATOR
    w_availability = Decimal(weights_bps.get("availability", 0)) / _BPS_DENOMINATOR
    w_unique = Decimal(weights_bps.get("unique_models", 0)) / _BPS_DENOMINATOR

    per_provider_metrics: dict[int, dict[str, Decimal]] = {}
    raw_scores: dict[int, Decimal] = {}
    for m in metrics:
        compute = compute_norm.get(m.provider_id, Decimal(0))
        speed = speed_score(m.p50_latency_ms, network_median_p50)
        availability = (
            min(Decimal(m.active_hours) / Decimal(m.window_hours), Decimal(1))
            if m.window_hours
            else Decimal(0)
        )
        unique_models = unique_norm.get(m.provider_id, Decimal(0))
        per_provider_metrics[m.provider_id] = {
            "compute": compute,
            "speed": speed,
            "availability": availability,
            "unique_models": unique_models,
        }
        raw_scores[m.provider_id] = (
            w_compute * compute
            + w_speed * speed
            + w_availability * availability
            + w_unique * unique_models
        )

    median_raw = median(list(raw_scores.values()))

    adjusted_scores: dict[int, Decimal] = {}
    tier_bps_by_provider: dict[int, int] = {}
    for m in metrics:
        raw = raw_scores[m.provider_id]
        adjusted = raw**exponent if raw >= median_raw else raw
        tier_bps = tier_multiplier_bps_for(m.volume_7d_tokens, tiers)
        tier_bps_by_provider[m.provider_id] = tier_bps
        adjusted_scores[m.provider_id] = adjusted * Decimal(tier_bps) / _BPS_DENOMINATOR

    total_adjusted = sum(adjusted_scores.values(), Decimal(0))

    results: list[ProviderScoreResult] = []
    allocated_total = 0
    for m in metrics:
        adjusted = adjusted_scores[m.provider_id]
        share = (adjusted / total_adjusted) if total_adjusted > 0 else Decimal(0)
        allocation_wei = int(
            (Decimal(providers_wei) * share).to_integral_value(rounding=ROUND_DOWN)
        )
        allocated_total += allocation_wei
        pm = per_provider_metrics[m.provider_id]
        results.append(
            ProviderScoreResult(
                provider_id=m.provider_id,
                compute=pm["compute"],
                speed=pm["speed"],
                availability=pm["availability"],
                unique_models=pm["unique_models"],
                raw_score=raw_scores[m.provider_id],
                adjusted_score=adjusted,
                tier_multiplier_bps=tier_bps_by_provider[m.provider_id],
                share=share,
                allocation_wei=allocation_wei,
            )
        )

    dust_wei = providers_wei - allocated_total
    return results, dust_wei
