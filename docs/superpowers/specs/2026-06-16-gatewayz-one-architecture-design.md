# Gatewayz One — Unified Inference Platform Architecture

**Date:** 2026-06-16
**Status:** Design approved, pending written-spec review
**Scope:** Platform-level architecture. This is the top-level spec; each build phase below becomes its own spec → plan → implementation cycle.

---

## 1. Problem

The backend has accreted ~31 routable "providers" plus a long tail of tooling, three rate-limit layers, scattered routing/failover logic, env-var-driven enablement (`ENABLED_PROVIDERS` defaults to `openrouter` only), and provider entries that are not actually inference sources. The goal is a single coherent architecture for an OpenRouter-class platform that is **profitable, low-latency, highly available**, supports **portable cross-chat context**, and routes intelligently — built by *evolving the current FastAPI/Supabase/Redis stack into a clean core*, not rewriting it.

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Build approach | **Evolve to a clean core** — refactor routing/provider layer; keep working billing, auth, RLS, observability; no data migration |
| Infra / availability | **Multi-region active-active** — geo-routed, replicated projections, region + provider failover independent |
| Cross-chat context | **Portable context + user memory** — thread history plus model-agnostic per-user memory injected into new chats |
| Smart routing | **Policy-based, user-selectable** — cost / latency / quality / balanced, with a hard margin floor always enforced |
| Core structure | **Control-plane / data-plane split (Approach B)** — stateless hot data plane per region + region-primary control plane |

## 3. Non-goals (v1)

- Edge gateway tier (Cloudflare Workers) — designed for, adopted in Phase 6.
- Embedding/RAG memory retrieval — v1 memory is recency + rolling summary + explicit user memory; RAG is a Phase 6 upgrade.
- No rewrite of billing, auth, RLS, or the observability stack — these are reused.

## 4. Topology

```
                         GLOBAL (geo-DNS / anycast)
        ┌────────────────────────────┐        ┌────────────────────────────┐
        │   REGION A (active)         │        │   REGION B (active)         │
        │  ── DATA PLANE (stateless) ─│        │  ── DATA PLANE (stateless) ─│
        │   1 Edge Gateway            │  same  │   1 Edge Gateway            │
        │   2 Smart Router            │ replica│   2 Smart Router            │
        │   3 Inference Dispatch      │        │   3 Inference Dispatch      │
        │   4 Context Injector        │        │   4 Context Injector        │
        │   reads▲      writes(async)▼│        │                             │
        │  ── REGIONAL PROJECTION ────│        │  ── REGIONAL PROJECTION ────│
        │   Redis: registry, pricing, │◀─push─▶│   (same)                    │
        │   health, balance, context  │        │                             │
        └──────────────┬─────────────┘        └─────────────────────────────┘
                       │ events (usage, ledger, health)
                       ▼
        ── CONTROL PLANE (region-primary) ───────────────────────────────────
         A Registry & Sync (DB = source of truth)   D Billing Ledger + Reconcile
         B Pricing & Margin engine                  E Analytics / Observability sink
         C Health Aggregator + Autonomous failover  F Admin / provider management
         Postgres (Supabase) primary + read replicas · Stripe · provider model APIs
```

**Invariant that makes it work:** the hot path (1→4) only *reads* the regional projection (Redis) and only *writes asynchronously* (usage/ledger/health events). It never makes a synchronous call to the control plane or primary DB. If the entire control plane is down, inference keeps serving; only sync, reconciliation, and admin pause. This is the HA + low-latency guarantee in one sentence.

## 5. Hot-path request lifecycle (SLO-critical)

```
1. Edge Gateway      auth (API key/JWT) · rate-limit (3-layer) · normalize → canonical request
                     · reject fast (bad key / no balance / abuse) — cheapest work first
2. Context Injector  load thread history + per-user memory from regional cache → assemble prompt
3. Smart Router      policy(cost|latency|quality|balanced) → rank eligible candidates by
                     {capability match, health, p50/p95 latency, price×margin-floor} → ordered chain
4. Credit precheck   optimistic reserve of estimated cost against regional balance cache
5. Inference Dispatch call provider #1 (canonical adapter); stream out; on error/timeout → #2, #3 …
6. Settle (async)    usage event → atomic deduction + double-entry ledger write;
                     persist turn to chat_history; update user memory; feed health/latency stats
```

Properties baked in:
- **Profitable** — margin floor applied inside step 3: a provider is eligible only if `upstream_cost × markup ≤ price_charged`. Reconciliation trues up estimate vs actual.
- **Low latency** — steps 1–4 are in-region cache reads; only egress is to the chosen provider; failover chain is pre-computed, not re-decided.
- **HA** — stateless data plane (any replica serves any request); provider failover and region failover are independent layers.
- **Portable context** — step 2 injects model-agnostic memory, so switching providers/models mid-thread preserves continuity.

## 6. Subsystems

### Control plane

**A · Registry & Sync** — DB is the single source of truth (completes the dynamic-catalog work already audited).
- Tables: `providers`, `models_catalog`, `model_provider_offers` (see §7).
- Sync pulls each provider `/models`, normalizes via `model_transformations`, upserts, deprecates stale models (TTL).
- **`ENABLED_PROVIDERS` env var is retired** — enablement becomes `providers.is_active` (admin-flippable, no redeploy). `provider_filter.is_provider_enabled()` reads the projection.
- Writes the regional projection (registry + capability map) to each region's Redis.

**B · Pricing & Margin engine**
- Owns `upstream_cost` per (provider, model); charged price = `upstream × PRICING_MARKUP` (currently 1.25) with per-model/per-customer overrides.
- Emits a price table to the projection; the router's margin floor is derived here — "never sell at a loss" is a data property, not scattered logic.

**C · Health Aggregator + Autonomous failover**
- Consumes latency/error events from all regions → per-(provider,model) health score + p50/p95 latency + circuit state.
- Pushes a health snapshot to the projection every few seconds. Evolution of `intelligent_health_monitor` / `autonomous_monitor` into the authority the router trusts.

### Data plane

**2 · Smart Router** — evolves `provider_failover.py` + `prompt_router` into a policy engine.
- Input: canonical request + `routing_policy` (cost | latency | quality | balanced; per-request header or per-key default).
- Candidate set = providers offering the model that are `is_active`, healthy (circuit closed), capability-matched, and margin-floor-eligible.
- Score: `w_cost·price + w_latency·p95 + w_quality·quality_prior`, weights from policy → **ordered failover chain**, not a single pick.
- Pure function over the projection snapshot → fast, testable, deterministic.

**3 · Inference Dispatch** — keeps the `PROVIDER_ROUTING` dispatch-table pattern.
- Each provider = a thin adapter implementing `{request, stream, process}` against one canonical contract (OpenAI + Anthropic compat in, normalized out).
- Walks the router's chain: #1 → on timeout/5xx/rate-limit → #2 … Emits usage + latency/error events per attempt.
- All policy/pricing/health logic lives outside the adapters.

**4 · Context & Memory service** — new subsystem (portable context + user memory).
- **Thread store**: hardened `chat_history`, append-only turns keyed by `conversation_id`.
- **User memory store**: model-agnostic facts/preferences/summaries per user, written post-turn via rolling summarization, injected into new chats regardless of serving model/provider.
- **Injection policy**: budgeted — recent turns verbatim + summarized older context + relevant memory, capped to a per-request token budget. (RAG retrieval is a Phase 6 upgrade.)

**D · Billing Ledger + Reconciliation**
- Hot path does an optimistic reserve against the regional balance cache (sub-ms), serves, then emits a settle event.
- Control plane writes the authoritative append-only double-entry ledger over `subscription_allowance` + `purchased_credits`, reconciles estimate vs actual, corrects drift. Stripe feeds the ledger.
- Cross-region safety: balance is eventually consistent; margin floor + reserve buffer absorb the race; reconciliation is exact.

### Cross-cutting

- **Caching/latency**: regional Redis projection (registry/pricing/health/balance/context), connection-pool warming for active providers, optional response cache for identical deterministic requests, pass-through of provider prompt-caching where supported.
- **HA / multi-region**: geo-DNS to nearest healthy region; stateless data plane; Postgres primary + read replicas (control plane writes primary, regions read projections); region failover and provider failover independent.
- **Observability**: Prometheus/Grafana/Sentry/Arize/OTel as the events sink + dashboards (reused).

## 7. Data-model changes (control plane = source of truth)

| Table | Change | Purpose |
|---|---|---|
| `providers` | add `auth_type`, `base_url`, `is_active`, `tier` (core/aggregator/niche), `region_affinity`, `async_streaming` | DB-driven enablement; retires `ENABLED_PROVIDERS` |
| `models_catalog` | add `canonical_id`, `capabilities` (jsonb), `modality`, `context_length`, `deprecated_at` | canonical registry; stale-model TTL |
| `model_provider_offers` | **new** — `(canonical_id, provider_slug, native_id, upstream_cost, quality_prior, p50, p95)` | the join the router scores over |
| `routing_policies` | **new** — per-key/per-request default policy + weights | policy-based routing |
| `user_memory` | **new** — `(user_id, kind, content, salience, updated_at)` | portable, model-agnostic memory |
| `conversations` / `chat_history` | add `conversation_id` thread key, rolling `summary` | hardened thread store |
| `credit_ledger` | **new** — append-only double-entry (debit/credit, reserved/settled) | authoritative billing + reconciliation |

Redis projection (per region, derived, disposable): `registry`, `price_table`, `health_snapshot`, `balance_cache`, `context_cache`.

## 8. Provider taxonomy cleanup

Enablement becomes `providers.tier` + `is_active`, seeded once — not code.

- **Delete as provider adapters (tooling, not inference)**: `helicone`, `notdiamond`, `code_router`, `ai_sdk`, `anthropic_transformer`. Helicone's observability value is already covered by Sentry/Arize/OTel.
- **Drop**: `clarifai` — zero unique models, pure reseller of frontier/open models already reached directly.
- **`tier=core`, active**: `openrouter, openai, anthropic, groq, cerebras, together, fireworks, deepinfra, google-vertex, xai, alibaba-cloud, zai`.
- **`tier=aggregator`, keep ONE active as overflow/failover, rest inactive**: `vercel-ai-gateway, onerouter, aihubmix, anannas`.
- **`tier=niche`, inactive by default, flip on by demand**: `nebius, chutes, featherless, huggingface, near, morpheus, simplismart, sybil, nosana, canopywave, akash, cloudflare-workers-ai`; `alpaca-network` (own network — active).
- **`cohere`**: has unique models (Command/rerank/embed) but routing adapter is unfinished — either complete it or leave inactive; no longer half-wired.

Net: ~31 routable entries → ~13 active real providers; the rest one DB flag away; tooling removed.

## 8.1 Architecture cleanup (bounded)

The cleanup is **enlarged beyond the provider layer to exactly the debt the unification cuts through** — and no further. Scope test: "are we already editing this to deliver the new architecture?"

**In scope (rides along with Phase 0–1; measured against current tree):**
- **Decompose `chat.py` (3,291 lines)** — inference, history injection, web search, and persistence are tangled in one route. The canonical contract + handler split breaks it into focused units (gateway → router → dispatch → context → settle).
- **Thin the fat provider clients** (`google_vertex` 1,784, `simplismart` 699, `cloudflare_workers_ai` 688, `nosana` 676, `alibaba_cloud` 603…) into adapters implementing only `{request, stream, process}`; pricing/health/routing logic moves out.
- **Collapse the 5 rate-limit modules** (`anonymous_rate_limiter`, `auth_rate_limiting`, `endpoint_rate_limiter`, `rate_limiting`, `rate_limiting_fallback`) into one limiter owned by the edge gateway.
- **Finish de-hardcoding** — retire `manual_pricing.json` (referenced across ~11 files) and `DEFAULT_*_MODELS` constants in favor of the DB registry + projection.
- **Purge the zombie trial system** — "removed," yet `trial` is still referenced across ~57 files. Pure dead weight; delete on the Phase 0 low-risk pass.

**Out of scope (the unification does not touch these — leave alone to avoid an infinite refactor):**
- Observability stack internals (the 12 metrics services / 7 health monitors) beyond wiring them as the events sink.
- Pydantic schema redesign beyond de-duplicating obviously overlapping models.
- Any subsystem not on the control/data-plane path.

## 9. Build order — each phase is its own spec → plan → implementation

| Phase | Sub-project | Unlocks | Risk |
|---|---|---|---|
| **0** | **Canonical contract + adapter interface** — one request/response schema; collapse provider clients to thin `{request,stream,process}` adapters; delete tooling-as-providers; **decompose `chat.py`**; **purge zombie trial code** (§8.1) | everything | low (no behavior change) |
| **1** | **Registry & projection** — DB source of truth, retire `ENABLED_PROVIDERS`, seed tiers, Redis projection + sync pipeline; provider cleanup; **collapse 5 rate-limiters → 1**; **finish de-hardcoding** (§8.1) | router, multi-region | med |
| **2** | **Smart Router policy engine** — scored candidate ranking + margin floor + failover chain | profitability, smart routing | med |
| **3** | **Billing ledger + reconciliation** — optimistic reserve → async settle → double-entry ledger | profitable at scale | high (money) |
| **4** | **Context & Memory service** — thread store + user memory + budgeted injection | cross-chat context | med |
| **5** | **Multi-region active-active** — projection replication, geo-DNS, region failover, read replicas | HA + low latency | high |
| **6** *(opt)* | **Edge tier (→C) + RAG memory** — Cloudflare edge gateway; embedding retrieval | best latency, scale | later |

Phases 0–1 are the foundation and also deliver the provider cleanup. 2–4 deliver differentiated features. 5 delivers the HA/latency story. 6 is the upgrade path.

## 10. Success criteria

- **Latency**: hot path adds < ~15 ms overhead over raw provider call (steps 1–4 are cache reads); p95 routing decision sub-millisecond.
- **Profitability**: no request can be routed below the margin floor; ledger reconciliation drift < 0.5% of revenue.
- **Availability**: inference continues serving during a full control-plane outage; single-region loss is transparent to users in other regions.
- **Context**: a conversation continues coherently after switching the serving model/provider mid-thread.
- **Maintainability**: enabling/disabling a provider is one DB flag, no redeploy; adding a provider is one adapter implementing the canonical contract.

## 11. Risks & mitigations

- **Eventual consistency of balances across regions** → optimistic reserve + reserve buffer + exact reconciliation; margin floor prevents loss even under race.
- **Projection staleness** (price/health) → short TTL + push invalidation; router degrades gracefully to last-known-good.
- **Billing ledger correctness** (Phase 3, money) → double-entry + reconciliation job + property tests before cutover.
- **Multi-region cost** → Phase 5 is gated; single-region hardened is a valid pause point if economics don't yet justify it.
- **Provider cleanup regressions** → cleanup happens via DB flags (reversible) in Phase 1, not deletion of working adapters; only tooling-as-providers is deleted from code.
