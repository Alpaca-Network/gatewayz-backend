"""Unit tests for the Phase 5 region-selection logic (pure, no I/O)."""

from src.services.region_router import (
    Region,
    is_region_eligible,
    primary_region,
    select_regions,
)


def r(name, *, active=True, healthy=True, latency=100.0):
    return Region(name=name, is_active=active, healthy=healthy, latency_ms=latency)


def test_eligible_requires_active_and_healthy():
    assert is_region_eligible(r("a"))
    assert not is_region_eligible(r("a", active=False))
    assert not is_region_eligible(r("a", healthy=False))


def test_orders_by_latency():
    regions = [r("slow", latency=300), r("fast", latency=50), r("mid", latency=150)]
    assert [x.name for x in select_regions(regions)] == ["fast", "mid", "slow"]


def test_excludes_inactive_and_unhealthy():
    regions = [
        r("ok", latency=200),
        r("down", active=False, latency=1),
        r("sick", healthy=False, latency=1),
    ]
    assert [x.name for x in select_regions(regions)] == ["ok"]


def test_home_region_first_when_eligible():
    regions = [r("us-west", latency=10), r("us-east", latency=200)]
    # even though us-west is faster, the client's home us-east goes first
    assert [x.name for x in select_regions(regions, home="us-east")] == ["us-east", "us-west"]


def test_home_ignored_when_not_eligible():
    regions = [
        r("us-east", healthy=False, latency=10),
        r("us-west", latency=200),
        r("eu", latency=100),
    ]
    # home us-east is down -> fall back to latency order over the rest
    assert [x.name for x in select_regions(regions, home="us-east")] == ["eu", "us-west"]


def test_empty_when_none_eligible():
    regions = [r("a", active=False), r("b", healthy=False)]
    assert select_regions(regions) == []


def test_single_region():
    assert [x.name for x in select_regions([r("solo")])] == ["solo"]


def test_tie_break_by_name():
    regions = [r("zeta", latency=100), r("alpha", latency=100), r("mike", latency=100)]
    assert [x.name for x in select_regions(regions)] == ["alpha", "mike", "zeta"]


def test_primary_region_returns_first_of_chain():
    regions = [r("slow", latency=300), r("fast", latency=50)]
    assert primary_region(regions).name == "fast"


def test_primary_region_respects_home():
    regions = [r("us-west", latency=10), r("us-east", latency=200)]
    assert primary_region(regions, home="us-east").name == "us-east"


def test_primary_region_none_when_all_down():
    assert primary_region([r("a", active=False), r("b", healthy=False)]) is None
