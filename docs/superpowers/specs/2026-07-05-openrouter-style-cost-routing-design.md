# OpenRouter-style cost routing — design

**Date:** 2026-07-05
**Branch:** `feat/openrouter-style-cost-routing`
**Goal:** Replicate OpenRouter's routing so Gatewayz auto-routes to the best model for the task and to the cheapest provider for each model — so the 25% spread is captured on every request and can never go negative.

## Decisions (locked)

1. **Money model:** keep the spread (user pays `canonical_price × PRICING_MARKUP`) AND route to the cheapest healthy provider, so we capture markup + provider-cost delta. Bill from the **served** provider so margin can't go negative.
2. **Auto model-for-task:** opt-in via `router` / `auto` alias (named models stay passthrough, matching `openrouter/auto`). Cost-first **provider** routing applies to **every** request regardless.

## Audit findings (why this is needed)

- Live provider chain is ordered by a **static priority list** (`provider_failover.FALLBACK_PROVIDER_PRIORITY`), never by cost.
- A correct cost-aware ranker exists (`smart_router.py`) + an offers projection (`model_offers_projection.py`) + a bridge (`smart_router_bridge.py`), but:
  - `SMART_ROUTER_ENABLED` defaults **false**;
  - the bridge only **reorders** and runs **before** health routing, which overrides it;
  - the offers table is **empty** because the projection reads `models.pricing_original_prompt` (null for all 1086 models) instead of the real `model_pricing` table.
- Billing resolves price from an arbitrary `.limit(1)` catalog row, decoupled from the served provider → margin can be negative.

**Validated data (staging):** 583 models priced; **63 canonical models served by ≥2 providers**; spreads up to **250×** (e.g. `minimax-m2.5`: 1.2e-7 vs 3.0e-5).

## Approach A (chosen)

Promote and harden the existing smart_router as the primary cost-first selector; do not rewrite the dispatch sinks. Phased.

## P1 — the money core (this plan)

### §1 Fix the offers projection (the unblocker)
`model_offers_projection.py`: read per-provider upstream cost from the `model_pricing` join
(`price_per_input_token`), not `models.pricing_original_prompt`. Keep the `(canonical_id, provider_slug)`
grouping and cheapest-collapse. Normalize per-token → per-1k. Then run `refresh_offers_projection`
to populate `model_provider_offers`.

### §2 Cost-first provider ranking on the live path
`chat_request.py`: restructure ordering so:
1. build the failover chain (existing);
2. **circuit-breaker + health = a filter** (drop unhealthy providers), not a re-sort that overrides cost;
3. **smart_router ranks the survivors by cost** (default policy `price`; `balanced` configurable) and **leads** the chain.
Default-on via a new `COST_ROUTING_ENABLED` (default true) with a kill-switch env; legacy static path preserved behind the flag.

### §3 Served-provider billing guarantee
`chat_handler._loss_proof_cost_split`: already takes `max(requested, served)` when the served provider
has a real price; harden so it never silently keeps a below-cost base, and add a **negative-margin guard**
(never route to / bill a provider whose cost ≥ what we can bill). Verify both auth and anon dispatch pass the
**served** `provider_model_id` into billing.

### §4 Observability
Per-request structured log/metric: chosen provider, upstream cost, billed price, realized margin.

### Tests
- projection builds offers from `model_pricing` with correct per-1k cost + cheapest-collapse;
- cost-first ordering picks the cheapest healthy provider; unhealthy filtered first;
- billing never bills below served cost; negative-margin provider rejected;
- named-model passthrough unchanged; flag-off restores legacy behavior.

## Out of scope for P1 (later phases)
- P2: harden auto model-for-task (`router`/`auto`), fix degenerate `general_router`.
- P3: OpenRouter-style provider preference knobs (`provider.sort/order/allow_fallbacks/max_price/only/ignore`, `:floor`/`:nitro`).
- Unifying the 3 dispatch sinks (auth-stream / auth-nonstream / anon) into one selector.
