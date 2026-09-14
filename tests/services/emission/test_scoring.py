"""Tests for src.services.emission.scoring (Chutes-style WAYZ emission
rewards, gatewayz-backend tokenomics). Pure functions, no DB/mocking
needed."""

from __future__ import annotations

from decimal import Decimal

from src.services.emission.scoring import (
    EmissionSplit,
    ProviderMetricsInput,
    median,
    normalize_by_max,
    score_providers,
    speed_score,
    split_emission,
    wayz_to_wei,
)

_WEIGHTS_BPS = {"compute": 5500, "speed": 2000, "availability": 2000, "unique_models": 500}
_EXPONENT = "1.3"


def _no_tiers(volume_7d, tiers):
    """Stand-in for tier_multiplier_bps_for -- always full 1.0x, mirroring
    src/services/gpu/earnings.py::tier_multiplier_bps's "no tiers
    configured" behavior."""
    return 10000


# ---------------------------------------------------------------------------
# wayz_to_wei / split_emission
# ---------------------------------------------------------------------------


def test_wayz_to_wei_floors_fractional_wayz():
    assert wayz_to_wei("1.5") == int(1.5 * 10**18)
    assert wayz_to_wei(1) == 10**18
    # A fractional wei-scale remainder must floor, not round.
    assert wayz_to_wei("0.0000000000000000019") == 1


def test_split_emission_sums_to_exactly_emission_wei():
    split = split_emission("100000", 4100, 4100, 1800)
    assert isinstance(split, EmissionSplit)
    assert split.providers_wei + split.stakers_wei + split.treasury_wei == split.emission_wei
    assert split.emission_wei == 100000 * 10**18


def test_split_emission_routes_bps_floor_dust_to_treasury():
    # 3 WAYZ emission at 41/41/18 -- floor division on the providers/staker
    # legs leaves a remainder that must land in treasury, not vanish.
    split = split_emission("3", 4100, 4100, 1800)
    assert split.providers_wei + split.stakers_wei + split.treasury_wei == split.emission_wei
    # 3e18 * 4100 // 10000 = 1230000000000000000 exactly divisible here,
    # so use an emission amount that does NOT divide evenly to prove dust
    # routing: 1 wei of emission split 41/41/18 can't divide evenly.
    split_tiny = split_emission(Decimal(1) / Decimal(10**18), 4100, 4100, 1800)
    assert split_tiny.emission_wei == 1
    assert split_tiny.providers_wei == 0
    assert split_tiny.stakers_wei == 0
    assert split_tiny.treasury_wei == 1


def test_split_emission_tolerates_a_bps_sum_that_is_not_10000():
    """Never raises on a misconfigured sum -- the startup check is a
    separate, non-fatal guard (see src/services/emission/epoch.py::
    check_emission_config); this function just floors what it's given and
    always balances to emission_wei."""
    split = split_emission("100", 5000, 5000, 5000)
    assert split.providers_wei + split.stakers_wei + split.treasury_wei == split.emission_wei


# ---------------------------------------------------------------------------
# median / normalize_by_max / speed_score
# ---------------------------------------------------------------------------


def test_median_even_and_odd():
    assert median([Decimal(1), Decimal(3), Decimal(2)]) == Decimal(2)
    assert median([Decimal(1), Decimal(2), Decimal(3), Decimal(4)]) == Decimal("2.5")
    assert median([]) == Decimal(0)


def test_normalize_by_max_scales_to_the_peak():
    result = normalize_by_max({1: Decimal(10), 2: Decimal(5), 3: Decimal(0)})
    assert result == {1: Decimal(1), 2: Decimal("0.5"), 3: Decimal(0)}


def test_normalize_by_max_all_zero_avoids_division_by_zero():
    result = normalize_by_max({1: Decimal(0), 2: Decimal(0)})
    assert result == {1: Decimal(0), 2: Decimal(0)}


def test_speed_score_faster_than_median_is_perfect():
    assert speed_score(Decimal(50), Decimal(100)) == Decimal(1)


def test_speed_score_at_twice_median_is_zero():
    assert speed_score(Decimal(200), Decimal(100)) == Decimal(0)


def test_speed_score_beyond_twice_median_floors_at_zero():
    assert speed_score(Decimal(1000), Decimal(100)) == Decimal(0)


def test_speed_score_no_data_scores_zero():
    assert speed_score(None, Decimal(100)) == Decimal(0)
    assert speed_score(Decimal(50), None) == Decimal(0)


# ---------------------------------------------------------------------------
# score_providers
# ---------------------------------------------------------------------------


def _metrics(
    provider_id,
    compute_raw=0,
    p50=None,
    active_hours=0,
    window_hours=168,
    unique_models=0,
    volume=0,
):
    return ProviderMetricsInput(
        provider_id=provider_id,
        compute_raw=Decimal(compute_raw),
        p50_latency_ms=Decimal(p50) if p50 is not None else None,
        active_hours=active_hours,
        window_hours=window_hours,
        unique_models=unique_models,
        volume_7d_tokens=volume,
    )


def test_score_providers_empty_metrics_returns_all_dust():
    results, dust = score_providers([], 1000, _WEIGHTS_BPS, _EXPONENT, _no_tiers, [])
    assert results == []
    assert dust == 1000


def test_score_providers_single_provider_gets_the_whole_pool():
    metrics = [_metrics(1, compute_raw=100, p50=50, active_hours=168, unique_models=3, volume=1000)]
    results, dust = score_providers(metrics, 1_000_000, _WEIGHTS_BPS, _EXPONENT, _no_tiers, [])
    assert len(results) == 1
    assert results[0].share == Decimal(1)
    assert results[0].allocation_wei == 1_000_000
    assert dust == 0


def test_score_providers_zero_activity_provider_gets_zero_share():
    metrics = [
        _metrics(1, compute_raw=1000, p50=50, active_hours=168, unique_models=5, volume=1_000_000),
        _metrics(2),  # zero activity across every metric
    ]
    results, dust = score_providers(metrics, 1_000_000, _WEIGHTS_BPS, _EXPONENT, _no_tiers, [])
    by_id = {r.provider_id: r for r in results}
    assert by_id[2].raw_score == Decimal(0)
    assert by_id[2].share == Decimal(0)
    assert by_id[2].allocation_wei == 0
    # The active provider gets everything the inactive one didn't.
    assert by_id[1].allocation_wei + dust == 1_000_000


def test_score_providers_exponent_applies_at_and_above_the_median_inclusive():
    # Three providers with distinct raw scores by construction (compute
    # only, so raw_score ranking == compute ranking): low < mid < high.
    # mid IS the median -- boundary must count as "at/above" and get the
    # exponent applied, same as high.
    metrics = [
        _metrics(1, compute_raw=10, active_hours=0),  # raw < median -> unchanged
        _metrics(2, compute_raw=50, active_hours=0),  # raw == median -> exponent applies
        _metrics(3, compute_raw=100, active_hours=0),  # raw > median -> exponent applies
    ]
    results, _dust = score_providers(metrics, 1_000_000, _WEIGHTS_BPS, _EXPONENT, _no_tiers, [])
    by_id = {r.provider_id: r for r in results}
    # Below median: raw_score == adjusted_score (before the tier multiplier,
    # which is 1.0x here via _no_tiers).
    assert by_id[1].adjusted_score == by_id[1].raw_score
    # At/above median: adjusted_score == raw_score ** 1.3 != raw_score
    # (raw_score > 0 for both, and x**1.3 != x for x != 0, 1).
    assert by_id[2].adjusted_score == by_id[2].raw_score ** Decimal("1.3")
    assert by_id[3].adjusted_score == by_id[3].raw_score ** Decimal("1.3")
    assert by_id[2].adjusted_score != by_id[2].raw_score
    assert by_id[3].adjusted_score != by_id[3].raw_score


def test_score_providers_all_zero_activity_dusts_the_entire_pool_to_treasury():
    metrics = [_metrics(1), _metrics(2)]
    results, dust = score_providers(metrics, 1_000_000, _WEIGHTS_BPS, _EXPONENT, _no_tiers, [])
    assert all(r.share == Decimal(0) and r.allocation_wei == 0 for r in results)
    assert dust == 1_000_000


def test_score_providers_shares_sum_to_one_and_allocations_sum_within_dust():
    metrics = [
        _metrics(1, compute_raw=30, p50=80, active_hours=100, unique_models=2, volume=500_000),
        _metrics(2, compute_raw=70, p50=40, active_hours=150, unique_models=4, volume=2_000_000),
        _metrics(3, compute_raw=10, p50=200, active_hours=20, unique_models=1, volume=10_000),
    ]
    results, dust = score_providers(metrics, 1_000_000, _WEIGHTS_BPS, _EXPONENT, _no_tiers, [])
    assert sum((r.share for r in results), Decimal(0)) == Decimal(1)
    assert sum(r.allocation_wei for r in results) + dust == 1_000_000
    assert 0 <= dust < len(metrics)  # floor dust is bounded by the number of providers


def test_score_providers_applies_the_tier_multiplier():
    def half_tier(volume_7d, tiers):
        return 5000  # 0.5x, regardless of volume

    metrics = [_metrics(1, compute_raw=100, active_hours=168, volume=999)]
    results, _dust = score_providers(metrics, 1_000_000, _WEIGHTS_BPS, _EXPONENT, half_tier, [])
    assert results[0].tier_multiplier_bps == 5000
    # Sole provider still gets the whole pool -- the tier multiplier only
    # matters relative to OTHER providers' adjusted scores, and there's
    # only one here.
    assert results[0].share == Decimal(1)
    assert results[0].allocation_wei == 1_000_000
