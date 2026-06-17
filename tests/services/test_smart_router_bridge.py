"""Unit tests for the smart-router bridge (Phase 2 wiring, item 3)."""

from __future__ import annotations

from src.services.smart_router_bridge import (
    reorder_chain_by_ranking,
    reorder_provider_chain,
)


def _offer(canonical_id, slug, upstream_cost, p95=100.0, quality=0.5):
    return {
        "canonical_id": canonical_id,
        "provider_slug": slug,
        "native_id": f"{slug}/{canonical_id}",
        "upstream_cost": upstream_cost,
        "p50_ms": p95 / 2,
        "p95_ms": p95,
        "quality_prior": quality,
        "is_active": True,
    }


# --------------------------------------------------------------------------- #
# reorder_chain_by_ranking — pure permutation, never drops/adds
# --------------------------------------------------------------------------- #

def test_reorder_puts_ranked_first_preserves_rest():
    chain = ["a", "b", "c", "d"]
    ranked = ["c", "a"]
    assert reorder_chain_by_ranking(chain, ranked) == ["c", "a", "b", "d"]


def test_reorder_ignores_ranked_not_in_chain():
    chain = ["a", "b"]
    ranked = ["z", "b"]  # z absent from chain
    assert reorder_chain_by_ranking(chain, ranked) == ["b", "a"]


def test_reorder_is_a_permutation_of_chain():
    chain = ["a", "b", "c"]
    out = reorder_chain_by_ranking(chain, ["b"])
    assert sorted(out) == sorted(chain)
    assert len(out) == len(chain)


def test_reorder_empty_ranking_is_identity():
    chain = ["a", "b", "c"]
    assert reorder_chain_by_ranking(chain, []) == chain


# --------------------------------------------------------------------------- #
# reorder_provider_chain — full bridge with injected offers
# --------------------------------------------------------------------------- #

def test_no_reorder_when_chain_too_short():
    assert reorder_provider_chain("m", ["only"], offers=[_offer("m", "only", 0.01)]) == ["only"]
    assert reorder_provider_chain("m", []) == []


def test_passthrough_when_no_offers():
    chain = ["openrouter", "together"]
    assert reorder_provider_chain("m", chain, offers=[]) == chain


def test_cost_policy_promotes_cheapest_offer():
    chain = ["expensive", "cheap"]
    offers = [_offer("m", "expensive", 0.10), _offer("m", "cheap", 0.01)]
    out = reorder_provider_chain("m", chain, policy="cost", offers=offers)
    assert out[0] == "cheap"
    assert sorted(out) == sorted(chain)  # no drops


def test_offers_for_other_models_do_not_affect_chain():
    chain = ["a", "b"]
    offers = [_offer("other", "a", 0.01), _offer("other", "b", 0.10)]
    # router filters by canonical_id == request model → none eligible → passthrough
    assert reorder_provider_chain("m", chain, policy="cost", offers=offers) == chain


def test_providers_without_offers_kept_at_tail():
    chain = ["no_offer", "cheap", "mid"]
    offers = [_offer("m", "cheap", 0.01), _offer("m", "mid", 0.05)]
    out = reorder_provider_chain("m", chain, policy="cost", offers=offers)
    assert out[0] == "cheap"      # ranked first
    assert out[1] == "mid"        # ranked second
    assert out[2] == "no_offer"   # unranked → tail
    assert sorted(out) == sorted(chain)


def test_unknown_policy_falls_back_to_balanced():
    chain = ["a", "b"]
    offers = [_offer("m", "a", 0.01), _offer("m", "b", 0.02)]
    # should not raise; returns a valid permutation
    out = reorder_provider_chain("m", chain, policy="nonsense", offers=offers)
    assert sorted(out) == sorted(chain)
