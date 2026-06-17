"""Smart Router — policy-based provider ranking (Gatewayz One, Phase 2).

A pure, deterministic function that ranks candidate provider offers into an
ordered failover chain according to a routing policy. It operates over a
*snapshot* of offers (the shape the Phase 1 registry projection will supply) and
performs no I/O, so it is fully unit-testable in isolation and has no dependency
on the live request path.

Selection pipeline (spec §5 / §6.2):
  1. Filter to ELIGIBLE offers — they must offer the requested model, be active,
     be healthy (circuit not open), match required capabilities, and clear the
     margin floor (``price >= upstream_cost * markup``, i.e. never sell at a loss).
  2. SCORE each eligible offer by the policy's weighted blend of
     {cost, latency, quality}, min-max normalized across the candidate set.
     A half-open circuit is allowed but penalized (probing).
  3. ORDER best-first → the failover chain (attempt #1, #2, …). Ties break
     deterministically by (price, p95 latency, provider slug).

Profitability is a property of step 1 (the margin floor), not of the score;
"cost" policy optimizes the customer's price, the floor guarantees our margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Mirror of services.pricing PRICING_MARKUP; callers pass the live value.
DEFAULT_MARKUP = 1.25

# Score penalty applied to a half-open (probing) circuit so it is tried only
# when no fully-healthy offer scores higher.
HALF_OPEN_PENALTY = 0.15


class CircuitState(str, Enum):  # noqa: UP042
    """Health/circuit-breaker state of an offer."""

    CLOSED = "closed"  # healthy — fully eligible
    HALF_OPEN = "half_open"  # probing after failure — eligible but penalized
    OPEN = "open"  # failing — excluded from the chain


class RoutingPolicy(str, Enum):  # noqa: UP042
    """User/key-selectable routing objective."""

    COST = "cost"
    LATENCY = "latency"
    QUALITY = "quality"
    BALANCED = "balanced"


# (w_cost, w_latency, w_quality) — non-negative weights summing to 1.0.
POLICY_WEIGHTS: dict[RoutingPolicy, tuple[float, float, float]] = {
    RoutingPolicy.COST: (1.0, 0.0, 0.0),
    RoutingPolicy.LATENCY: (0.0, 1.0, 0.0),
    RoutingPolicy.QUALITY: (0.0, 0.0, 1.0),
    RoutingPolicy.BALANCED: (0.4, 0.3, 0.3),
}


@dataclass(frozen=True)
class ProviderOffer:
    """A single ``(provider, model)`` offer the router may choose from.

    Costs/prices are per-1k-tokens (any consistent unit works — only relative
    ordering matters for scoring; the margin floor compares price to cost).
    """

    canonical_id: str
    provider_slug: str
    native_id: str
    upstream_cost_per_1k: float  # what the upstream provider charges us
    price_per_1k: float  # what we would charge the customer
    p50_ms: float
    p95_ms: float
    quality_prior: float  # 0..1, higher is better
    is_active: bool = True
    circuit_state: CircuitState = CircuitState.CLOSED
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RoutingRequest:
    """What the caller wants routed."""

    canonical_id: str
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    policy: RoutingPolicy = RoutingPolicy.BALANCED


def is_margin_floor_eligible(offer: ProviderOffer, markup: float = DEFAULT_MARKUP) -> bool:
    """True if charging this offer's price keeps us at/above cost × markup."""
    return offer.price_per_1k >= offer.upstream_cost_per_1k * markup


def is_eligible(
    offer: ProviderOffer, request: RoutingRequest, markup: float = DEFAULT_MARKUP
) -> bool:
    """True if the offer can serve the request profitably and healthily."""
    if offer.canonical_id != request.canonical_id:
        return False
    if not offer.is_active:
        return False
    if offer.circuit_state == CircuitState.OPEN:
        return False
    if not request.required_capabilities <= offer.capabilities:
        return False
    if not is_margin_floor_eligible(offer, markup):
        return False
    return True


def _normalize(values: list[float], *, higher_is_better: bool) -> list[float]:
    """Min-max normalize to [0, 1]; all-equal (or single) → neutral 1.0.

    For lower-is-better dimensions (cost, latency) the scale is inverted so a
    smaller raw value yields a higher normalized score.
    """
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    if higher_is_better:
        return [(v - lo) / (hi - lo) for v in values]
    return [(hi - v) / (hi - lo) for v in values]


def score_offers(offers: list[ProviderOffer], policy: RoutingPolicy) -> list[float]:
    """Return a policy-weighted score per offer (higher is better).

    Assumes ``offers`` is the already-filtered eligible set.
    """
    w_cost, w_lat, w_qual = POLICY_WEIGHTS[policy]
    cost_s = _normalize([o.price_per_1k for o in offers], higher_is_better=False)
    lat_s = _normalize([o.p95_ms for o in offers], higher_is_better=False)
    qual_s = _normalize([o.quality_prior for o in offers], higher_is_better=True)

    scores: list[float] = []
    for i, offer in enumerate(offers):
        score = w_cost * cost_s[i] + w_lat * lat_s[i] + w_qual * qual_s[i]
        if offer.circuit_state == CircuitState.HALF_OPEN:
            score -= HALF_OPEN_PENALTY
        scores.append(score)
    return scores


def build_failover_chain(
    offers: list[ProviderOffer],
    request: RoutingRequest,
    markup: float = DEFAULT_MARKUP,
) -> list[ProviderOffer]:
    """Filter, score, and order offers into a best-first failover chain.

    Returns an empty list when no offer is eligible. Ordering is deterministic:
    by descending score, then ascending price, p95 latency, and provider slug.
    """
    eligible = [o for o in offers if is_eligible(o, request, markup)]
    if not eligible:
        return []
    scores = score_offers(eligible, request.policy)
    paired = list(zip(eligible, scores, strict=True))
    paired.sort(key=lambda t: (-t[1], t[0].price_per_1k, t[0].p95_ms, t[0].provider_slug))
    return [offer for offer, _ in paired]
