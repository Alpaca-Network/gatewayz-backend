"""Region router — geo/health-based region selection (Gatewayz One, Phase 5).

STAGED / NOT WIRED. Pure logic mirroring the provider smart-router but for
REGIONS: given a snapshot of regions (active flag, health, estimated latency to
the client), produce an ordered region failover chain. Region failover is an
independent layer from provider failover (spec §4) — a request fails over to
another region only when its home region is unavailable.

This module has no I/O. The actual geo-DNS / anycast routing, regional
projection replication, and Postgres read-replica wiring are infrastructure
(see docs/superpowers/specs/2026-06-17-phase5-multi-region.md) and are not code
here. This is the in-process decision function those layers would consult.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    """A snapshot of one deployment region's routability."""

    name: str
    is_active: bool = True
    healthy: bool = True
    latency_ms: float = 0.0  # estimated client→region latency (lower is better)


def is_region_eligible(region: Region) -> bool:
    """A region can serve traffic only if it is active and healthy."""
    return region.is_active and region.healthy


def select_regions(regions: list[Region], *, home: str | None = None) -> list[Region]:
    """Return eligible regions as an ordered failover chain (best first).

    Ordering: the client's ``home`` region first when it is eligible (geo
    affinity keeps the hot path in-region), then the remaining eligible regions
    by ascending latency, breaking ties by name for determinism. Inactive or
    unhealthy regions are excluded entirely.
    """
    eligible = [r for r in regions if is_region_eligible(r)]
    eligible.sort(key=lambda r: (r.latency_ms, r.name))
    if home is not None:
        preferred = [r for r in eligible if r.name == home]
        others = [r for r in eligible if r.name != home]
        return preferred + others
    return eligible


def primary_region(regions: list[Region], *, home: str | None = None) -> Region | None:
    """The region a request should be served from now, or None if all are down."""
    chain = select_regions(regions, home=home)
    return chain[0] if chain else None
