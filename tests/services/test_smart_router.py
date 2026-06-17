"""Unit tests for the Phase 2 smart-router policy engine (pure, no I/O)."""

import pytest

from src.services.smart_router import (
    DEFAULT_MARKUP,
    HALF_OPEN_PENALTY,
    POLICY_WEIGHTS,
    CircuitState,
    ProviderOffer,
    RoutingPolicy,
    RoutingRequest,
    _normalize,
    build_failover_chain,
    is_eligible,
    is_margin_floor_eligible,
    score_offers,
)

MODEL = "openai/gpt-4o"


def offer(
    slug,
    *,
    cost=1.0,
    price=2.0,
    p50=100,
    p95=200,
    quality=0.5,
    active=True,
    circuit=CircuitState.CLOSED,
    caps=frozenset(),
    model=MODEL,
):
    return ProviderOffer(
        canonical_id=model,
        provider_slug=slug,
        native_id=f"{slug}/{model}",
        upstream_cost_per_1k=cost,
        price_per_1k=price,
        p50_ms=p50,
        p95_ms=p95,
        quality_prior=quality,
        is_active=active,
        circuit_state=circuit,
        capabilities=caps,
    )


def req(policy=RoutingPolicy.BALANCED, caps=frozenset(), model=MODEL):
    return RoutingRequest(canonical_id=model, required_capabilities=caps, policy=policy)


# --------------------------------------------------------------------------- #
# Policy weights sanity
# --------------------------------------------------------------------------- #
def test_every_policy_has_weights_summing_to_one():
    for policy in RoutingPolicy:
        w = POLICY_WEIGHTS[policy]
        assert len(w) == 3
        assert all(x >= 0 for x in w)
        assert abs(sum(w) - 1.0) < 1e-9, f"{policy} weights sum to {sum(w)}"


# --------------------------------------------------------------------------- #
# Margin floor
# --------------------------------------------------------------------------- #
def test_margin_floor_eligible_at_or_above_cost_times_markup():
    # price exactly cost * markup -> eligible (>=)
    assert is_margin_floor_eligible(offer("p", cost=1.0, price=1.25), markup=1.25)
    # price above floor -> eligible
    assert is_margin_floor_eligible(offer("p", cost=1.0, price=2.0), markup=1.25)


def test_margin_floor_rejects_below_cost_times_markup():
    # price below cost * markup -> would sell at a loss/thin margin -> ineligible
    assert not is_margin_floor_eligible(offer("p", cost=1.0, price=1.24), markup=1.25)
    # selling below cost outright
    assert not is_margin_floor_eligible(offer("p", cost=2.0, price=1.0), markup=1.25)


def test_default_markup_value():
    assert DEFAULT_MARKUP == 1.25


# --------------------------------------------------------------------------- #
# Eligibility filtering
# --------------------------------------------------------------------------- #
def test_eligible_happy_path():
    assert is_eligible(offer("p", cost=1.0, price=2.0), req())


def test_ineligible_wrong_model():
    o = offer("p", model="anthropic/claude")
    assert not is_eligible(o, req(model="openai/gpt-4o"))


def test_ineligible_inactive():
    assert not is_eligible(offer("p", active=False), req())


def test_ineligible_open_circuit():
    assert not is_eligible(offer("p", circuit=CircuitState.OPEN), req())


def test_half_open_is_eligible():
    assert is_eligible(offer("p", circuit=CircuitState.HALF_OPEN), req())


def test_ineligible_missing_capability():
    o = offer("p", caps=frozenset({"text"}))
    assert not is_eligible(o, req(caps=frozenset({"text", "vision"})))


def test_eligible_with_superset_capabilities():
    o = offer("p", caps=frozenset({"text", "vision", "tools"}))
    assert is_eligible(o, req(caps=frozenset({"text", "vision"})))


def test_ineligible_below_margin_floor():
    o = offer("p", cost=2.0, price=2.0)  # 2.0 < 2.0*1.25
    assert not is_eligible(o, req())


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def test_normalize_all_equal_is_neutral():
    assert _normalize([5.0, 5.0, 5.0], higher_is_better=True) == [1.0, 1.0, 1.0]
    assert _normalize([5.0, 5.0, 5.0], higher_is_better=False) == [1.0, 1.0, 1.0]


def test_normalize_empty():
    assert _normalize([], higher_is_better=True) == []


def test_normalize_lower_is_better_inverts():
    # value 10 (lowest) should score 1.0; value 20 (highest) should score 0.0
    assert _normalize([10.0, 20.0], higher_is_better=False) == [1.0, 0.0]


def test_normalize_higher_is_better():
    assert _normalize([10.0, 20.0], higher_is_better=True) == [0.0, 1.0]


# --------------------------------------------------------------------------- #
# Policy-driven ordering
# --------------------------------------------------------------------------- #
def test_cost_policy_picks_cheapest_first():
    offers = [
        offer("expensive", price=9.0, p95=10, quality=0.99),
        offer("cheap", price=2.0, p95=900, quality=0.10),
        offer("mid", price=5.0, p95=400, quality=0.50),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.COST))
    assert [o.provider_slug for o in chain] == ["cheap", "mid", "expensive"]


def test_latency_policy_picks_fastest_first():
    offers = [
        offer("slow", price=2.0, p95=900),
        offer("fast", price=9.0, p95=50),
        offer("mid", price=5.0, p95=400),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.LATENCY))
    assert [o.provider_slug for o in chain] == ["fast", "mid", "slow"]


def test_quality_policy_picks_highest_quality_first():
    offers = [
        offer("low", price=2.0, quality=0.1),
        offer("high", price=9.0, quality=0.95),
        offer("mid", price=5.0, quality=0.5),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.QUALITY))
    assert [o.provider_slug for o in chain] == ["high", "mid", "low"]


def test_balanced_policy_blends_dimensions():
    # A dominates on cost+latency+quality vs B -> A first under balanced.
    offers = [
        offer("A", price=2.0, p95=100, quality=0.9),
        offer("B", price=8.0, p95=800, quality=0.2),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.BALANCED))
    assert [o.provider_slug for o in chain] == ["A", "B"]


# --------------------------------------------------------------------------- #
# Chain construction edge cases
# --------------------------------------------------------------------------- #
def test_chain_excludes_ineligible_offers():
    offers = [
        offer("ok", price=2.0),
        offer("inactive", price=1.0, active=False),
        offer("open", price=1.0, circuit=CircuitState.OPEN),
        offer("loss", cost=2.0, price=2.0),  # below margin floor
        offer("wrongmodel", price=1.0, model="x/y"),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.COST))
    assert [o.provider_slug for o in chain] == ["ok"]


def test_empty_when_none_eligible():
    offers = [offer("inactive", active=False), offer("open", circuit=CircuitState.OPEN)]
    assert build_failover_chain(offers, req()) == []


def test_empty_offers_input():
    assert build_failover_chain([], req()) == []


def test_single_eligible_offer():
    chain = build_failover_chain([offer("solo")], req())
    assert [o.provider_slug for o in chain] == ["solo"]


def test_half_open_penalized_below_equal_closed():
    # Two identical offers except circuit state; closed must rank first.
    offers = [
        offer("probing", price=2.0, p95=200, quality=0.5, circuit=CircuitState.HALF_OPEN),
        offer("healthy", price=2.0, p95=200, quality=0.5, circuit=CircuitState.CLOSED),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.BALANCED))
    assert [o.provider_slug for o in chain] == ["healthy", "probing"]


def test_half_open_penalty_is_applied_in_score():
    closed = offer("c", circuit=CircuitState.CLOSED)
    half = offer("h", circuit=CircuitState.HALF_OPEN)
    # identical metrics -> normalized dims all neutral 1.0; half-open loses the penalty
    s_closed, s_half = score_offers([closed, half], RoutingPolicy.BALANCED)
    assert abs((s_closed - s_half) - HALF_OPEN_PENALTY) < 1e-9


def test_tie_break_is_deterministic():
    # Identical scores -> ordered by (price, p95, slug). Same price/p95 -> slug.
    offers = [
        offer("zeta", price=2.0, p95=200, quality=0.5),
        offer("alpha", price=2.0, p95=200, quality=0.5),
        offer("mike", price=2.0, p95=200, quality=0.5),
    ]
    chain = build_failover_chain(offers, req(RoutingPolicy.QUALITY))
    assert [o.provider_slug for o in chain] == ["alpha", "mike", "zeta"]


def test_chain_is_stable_across_input_order():
    a = offer("alpha", price=3.0, p95=150, quality=0.7)
    b = offer("bravo", price=2.0, p95=300, quality=0.4)
    c = offer("charlie", price=5.0, p95=80, quality=0.9)
    chain1 = [o.provider_slug for o in build_failover_chain([a, b, c], req(RoutingPolicy.COST))]
    chain2 = [o.provider_slug for o in build_failover_chain([c, b, a], req(RoutingPolicy.COST))]
    assert chain1 == chain2


@pytest.mark.parametrize("policy", list(RoutingPolicy))
def test_all_policies_return_all_eligible_offers(policy):
    offers = [offer("a", price=2.0), offer("b", price=3.0), offer("c", price=4.0)]
    chain = build_failover_chain(offers, req(policy))
    assert len(chain) == 3
    assert {o.provider_slug for o in chain} == {"a", "b", "c"}
