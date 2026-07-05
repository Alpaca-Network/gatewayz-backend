"""Smart-router bridge — wire the pure Phase 2 router into the live chat path.

The pure router (:mod:`src.services.smart_router`) ranks ``ProviderOffer``
snapshots into an ordered failover chain. This bridge connects it to the live
request path, where the chain is a plain list of provider *slugs* built by
``prepare_upstream_request``:

  * loads the (model × provider) offers from the Phase 1 ``model_provider_offers``
    projection,
  * runs the pure router over them with the configured policy,
  * REORDERS the existing slug chain by the router's ranking — **without ever
    dropping a provider** (failover coverage is preserved): ranked providers that
    are also in the original chain come first (router order), then any remaining
    original providers in their original order.

Safety: if the offers table has no rows for the model (the current state until the
projection is populated), or anything fails, the original chain is returned
unchanged. Gated by ``Config.SMART_ROUTER_ENABLED`` (off by default) at the call
site, so the default path is byte-for-byte unchanged.
"""

from __future__ import annotations

import logging

from src.services.model_canonicalization import load_alias_map, offer_group_key
from src.services.smart_router import (
    DEFAULT_MARKUP,
    ProviderOffer,
    RoutingPolicy,
    RoutingRequest,
    build_failover_chain,
)

logger = logging.getLogger(__name__)


def _policy(name: str) -> RoutingPolicy:
    """Coerce a policy string to RoutingPolicy; unknown → BALANCED."""
    try:
        return RoutingPolicy(name)
    except ValueError:
        return RoutingPolicy.BALANCED


def reorder_chain_by_ranking(chain: list[str], ranked_slugs: list[str]) -> list[str]:
    """Reorder ``chain`` so providers in ``ranked_slugs`` lead (in ranked order).

    Pure. Providers present in the chain but absent from the ranking keep their
    original relative order and are appended after the ranked ones. No provider is
    added or dropped — the result is a permutation of ``chain``.
    """
    in_chain = set(chain)
    lead = [s for s in ranked_slugs if s in in_chain]
    lead_set = set(lead)
    tail = [s for s in chain if s not in lead_set]
    return lead + tail


def _offers_to_provider_offers(offers: list[dict], markup: float) -> list[ProviderOffer]:
    """Map ``model_provider_offers`` rows to ProviderOffer snapshots.

    ``price_per_1k`` is derived as ``upstream_cost * markup`` so every active offer
    clears the router's margin floor (we never sell at a loss); the router then
    orders by the policy. Missing latency/quality fall back to neutral values.
    """
    result: list[ProviderOffer] = []
    for o in offers:
        try:
            upstream = float(o.get("upstream_cost") or 0.0)
            result.append(
                ProviderOffer(
                    canonical_id=o["canonical_id"],
                    provider_slug=o["provider_slug"],
                    native_id=o.get("native_id") or o["provider_slug"],
                    upstream_cost_per_1k=upstream,
                    price_per_1k=upstream * markup,
                    p50_ms=float(o.get("p50_ms") or 0.0),
                    p95_ms=float(o.get("p95_ms") or 0.0),
                    quality_prior=float(o.get("quality_prior") if o.get("quality_prior") is not None else 0.5),
                    is_active=bool(o.get("is_active", True)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed offer rows
    return result


def _load_offers(canonical_id: str) -> list[dict]:
    """Fetch active offers for a model from the Phase 1 projection. [] on any failure."""
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()
        resp = (
            client.table("model_provider_offers")
            .select("*")
            .eq("canonical_id", canonical_id)
            .eq("is_active", True)
            .execute()
        )
        return getattr(resp, "data", None) or []
    except Exception as e:
        logger.debug("smart_router: offer load failed for %s: %s", canonical_id, e)
        return []


def reorder_provider_chain(
    model: str,
    provider_chain: list[str],
    *,
    policy: str = "balanced",
    markup: float = DEFAULT_MARKUP,
    offers: list[dict] | None = None,
) -> list[str]:
    """Reorder ``provider_chain`` for ``model`` via the smart router.

    Returns the chain unchanged when there is nothing to reorder (no offers, single
    provider, or an error). ``offers`` may be injected (tests); otherwise loaded
    from the Phase 1 projection.
    """
    if not provider_chain or len(provider_chain) < 2:
        return provider_chain
    try:
        if offers is not None:
            rows = offers
        else:
            key = offer_group_key(model, load_alias_map())
            rows = _load_offers(key)
        if not rows:
            return provider_chain
        provider_offers = _offers_to_provider_offers(rows, markup)
        if not provider_offers:
            return provider_chain
        ranked = build_failover_chain(
            provider_offers,
            RoutingRequest(canonical_id=model, policy=_policy(policy)),
            markup,
        )
        ranked_slugs = [o.provider_slug for o in ranked]
        reordered = reorder_chain_by_ranking(provider_chain, ranked_slugs)
        if reordered != provider_chain:
            logger.info(
                "smart_router reordered chain for %s: %s -> %s", model, provider_chain, reordered
            )
        return reordered
    except Exception as e:  # never break provider selection
        logger.warning("smart_router reorder failed for %s (using original chain): %s", model, e)
        return provider_chain
