# Community models in the public catalog (`serving_tier`)

**Status:** implemented. Extends gatewayz-backend#2262's catalog top-up
(`_append_community_models`) with the label and the filter that top-up was
missing.

Gatewayz serves models from two different kinds of infrastructure. Until now
`GET /v1/models` described only one of them, and community models — when the
flag was on — were merged into the same flat list with nothing to tell them
apart but an id prefix. This document is the contract for the label that
fixes that.

## The two tiers

| `serving_tier` | Served by | Priced | Reachable how |
|---|---|---|---|
| `provider` | A contracted upstream provider (OpenAI, Anthropic, xAI, Moonshot, Meta, …) | Yes — `pricing` populated | Normal model id; eligible for failover and auto-routing |
| `community` | The community GPU network: open-weight models on operator-run nodes, beta, verified by sampled replay (`VERIFICATION_AND_PAYOUTS.md`) | **No** — `pricing: null`, `pricing_status: "unpriced"` | Only by naming `community/<model>` explicitly, with an API key |

## `GET /v1/models`

### Every row carries `serving_tier`

Unconditional and additive, on provider rows too, so a client switches on the
value rather than treating "field absent" as a third state.

```jsonc
{
  "id": "community/llama-3.1-8b-instruct",
  "name": "llama-3.1-8b-instruct",
  "serving_tier": "community",
  "provider_slug": "community",
  "source_gateway": "community",
  "context_length": 8192,
  "pricing": null,
  "pricing_status": "unpriced",
  "is_free": false,
  "available_node_count": 2,
  "servable": false
}
```

### `?tier=` filters on it

- `?tier=provider` — only provider-served models. **This is the value to pin**
  if your integration must be unaffected by the community network: its result
  is byte-identical whether or not `COMMUNITY_ROUTING_ENABLED` is on and
  whatever nodes come and go.
- `?tier=community` — only community models.
- `?tier=all` — both. **The default**, which preserves #2262's merged
  behaviour exactly: with `COMMUNITY_ROUTING_ENABLED` off (production's
  default) the response is today's response plus the additive
  `serving_tier` field, and nothing else.
- Anything else is a `400`. An unrecognised filter value must never quietly
  widen the result set.

`total`, `returned`, `has_more` and `next_offset` all describe the **filtered**
set, and `tier` is echoed on the response envelope. The tier is part of the
response-cache key, so a `?tier=provider` caller can never be served a
community-inclusive cache entry.

`?gateway=community` is *not* the way to ask for this: `gateway` values are
validated against the `providers` registry, and `community` is deliberately
not a registered provider (see below). Use `?tier=community`.

## Pricing: unpriced, never zero

Community rows carry `pricing: null` with `pricing_status: "unpriced"`.

They are **not** given `{"prompt": 0, "completion": 0}`. A zero price is a
positive claim — "free to serve" — and it is a claim every downstream cost
calculation would believe. `src/services/pricing/pricing.py::model_has_pricing`
already rejects a row whose prompt and completion are both zero for exactly
this reason; the catalog must not manufacture the shape it rejects.

They are likewise **not** given `is_free: true`. `is_free` is the flag that
exempts a request from the credit check and admits a model for anonymous
callers (`src/services/anonymous_rate_limiter.py`). Unpriced does not borrow
it.

The consequence, stated plainly: because a community model has no pricing row,
`enforce_model_pricing_gate` refuses it, so the catalog reports
`servable: false` for it. The tier is **published and beta, not yet callable**.
Making it callable is a pricing/metering decision, not a catalog one.

## Availability honesty

`community_catalog_models` projects only nodes with `status == "active"`.

- A model whose every node has gone offline **stops being listed**, rather
  than lingering as an advertisement nobody can serve.
- `available_node_count` says how many active nodes declare the model at the
  moment the projection ran. It is a snapshot, not a promise.
- The projection is re-run **live on every catalog request** (community has no
  scheduled `<slug>_catalog.py` sync), so the listing tracks the node set
  rather than the last sync.
- It fails open: if the `gpu_nodes` read fails, community rows are omitted and
  the rest of the catalog is served normally.

## Guarantees that did not change

1. **Resolution is never substitution.** The model-resolution index is built
   from the *cached* catalog (`get_cached_models("all")` and the unique-models
   cache). Community rows are a serve-time top-up in `src/routes/catalog.py`
   and enter neither cache, so a bare `llama-3.1-8b-instruct` cannot
   suffix-match onto `community/llama-3.1-8b-instruct`. An unknown id is still
   a `400 model_not_found`.
2. **No silent routing to a community node.** `"community"` is added to
   `PROVIDER_ROUTING` only when `COMMUNITY_ROUTING_ENABLED` is true, and is
   never added to `FALLBACK_PROVIDER_PRIORITY` or the multi-provider registry
   — it can never be chosen by failover or auto-routing
   (`src/handlers/provider_registry.py`,
   `tests/services/test_community_routing_exclusion.py`). A request reaches a
   community node only by naming `community/<model>` itself.
3. **Authenticated callers only.** `enforce_community_auth_gate`
   (`src/security/inference_gates.py`) rejects an anonymous `community/<model>`
   request with `403 community_requires_auth`, regardless of
   `Config.ANONYMOUS_ENABLED`. The node operator sees prompt content by
   construction; the explicit id prefix is the caller's consent to that, and
   an anonymous caller cannot meaningfully give it.

## Code

- `src/services/gpu/catalog.py` — the node-list → catalog-row projection
  (`community_catalog_models`), including the tier label, the unpriced
  markers and `available_node_count`.
- `src/routes/catalog.py` — `_serving_tier_of` / `_annotate_serving_tier` /
  `_filter_by_tier` / `_validate_tier`, and the `?tier=` wiring in
  `get_models`.
- `tests/routes/test_catalog_serving_tier.py` — the four load-bearing
  properties (labelled, provider-only is exactly today's set, unpriced never
  bills zero, unknown id still 400s) plus availability.
- `tests/routes/test_catalog_community.py`,
  `tests/services/test_community_catalog.py` — the #2262 top-up itself.
