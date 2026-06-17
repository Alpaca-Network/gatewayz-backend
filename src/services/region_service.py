"""Region service — wire the pure Phase 5 region router to runtime config.

Rollout phase 1 of the multi-region plan
(``docs/superpowers/specs/2026-06-17-phase5-multi-region-deploy-plan.md``):
feed a real region inventory (from config) into the pure selection core
(:mod:`src.services.region_router`) and expose the serving/selected region for
observability. **No traffic change** — until ``MULTI_REGION_ENABLED`` is true the
inventory is just this single region, so selection is a no-op and all traffic is
served locally.

This is the in-process decision point the later infra layers (GeoDNS/anycast,
read replicas, regional deploys) will consult. Those layers are owner-driven and
out of scope here.
"""

from __future__ import annotations

import logging

from src.config import Config
from src.services.region_router import Region, primary_region, select_regions

logger = logging.getLogger(__name__)


def current_region() -> str:
    """The region name THIS instance runs as."""
    return Config.GATEWAY_REGION


def home_region() -> str:
    """The home region for a user's billing-affecting writes (rollout step 3 pin)."""
    return Config.GATEWAY_HOME_REGION


def _parse_inventory(raw: str) -> list[Region]:
    """Parse ``"name" | "name:latency_ms"`` comma list into Region snapshots."""
    regions: list[Region] = []
    seen: set[str] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        name, _, lat = token.partition(":")
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            latency = float(lat) if lat.strip() else 0.0
        except ValueError:
            latency = 0.0
        regions.append(Region(name=name, is_active=True, healthy=True, latency_ms=latency))
    return regions


def region_inventory() -> list[Region]:
    """The deployment's region inventory.

    Single region (``GATEWAY_REGION``) unless ``MULTI_REGION_ENABLED`` is true, in
    which case ``GATEWAY_REGIONS`` is parsed (falling back to the single region when
    empty or unparseable). The current region is always guaranteed present.
    """
    single = [Region(name=current_region(), is_active=True, healthy=True, latency_ms=0.0)]
    if not Config.MULTI_REGION_ENABLED:
        return single
    parsed = _parse_inventory(Config.GATEWAY_REGIONS)
    if not parsed:
        return single
    if all(r.name != current_region() for r in parsed):
        parsed.append(Region(name=current_region(), is_active=True, healthy=True, latency_ms=0.0))
    return parsed


def selected_region(home: str | None = None) -> Region | None:
    """The region a request should be served from now (None if all are down)."""
    return primary_region(region_inventory(), home=home or home_region())


def failover_chain(home: str | None = None) -> list[Region]:
    """Ordered region failover chain (best first); home-region preferred."""
    return select_regions(region_inventory(), home=home or home_region())


def region_status() -> dict:
    """Observability snapshot of region wiring (for the X-Gatewayz-Region header / health)."""
    inv = region_inventory()
    sel = primary_region(inv, home=home_region())
    return {
        "current": current_region(),
        "home": home_region(),
        "selected": sel.name if sel else None,
        "multi_region_enabled": Config.MULTI_REGION_ENABLED,
        "inventory": [r.name for r in inv],
    }
