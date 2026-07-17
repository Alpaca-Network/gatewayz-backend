# Gatewayz North Star — Source of Truth

**Status**: Canonical. Every architectural, product, and provider decision must be justifiable against this document. If a change contradicts it, either the change is wrong or this document gets amended first — never silently diverged from.

**What we are building**: A profitable, serverless LLM router. One OpenAI-compatible API in front of many providers. We sell reliability, simplicity, and price — not GPUs, not models.

---

## 1. Core Principle

> **Gross margin = (what we bill) − (provider invoice). Every component exists to widen that spread per token, or it doesn't exist.**

- **Zero GPU ownership, ever.** All inference is passthrough to upstream providers with serverless, token-billed endpoints.
- **Near-zero infra COGS.** Edge/serverless compute, one Postgres (state), Redis (hot path). Infra cost must stay decoupled from request volume (streaming passthrough, no buffering, async writes).
- **We compete with going direct.** Total added latency budget on the hot path: **< 50ms**. If we're slower than the provider's own API by more than that, we lose the customer.

---

## 2. Business Model (revenue stack, in priority order)

| # | Stream | Mechanism | Notes |
|---|--------|-----------|-------|
| 1 | **Top-up fee** | ~5% on credit purchases | Margin on money movement, not tokens. The proven core (OpenRouter's actual model). |
| 2 | **Open-model arbitrage** | Route to cheapest healthy host, bill at reference price | The only true *routing* profit. Requires ≥2 hosts per high-volume open model. |
| 3 | **Negotiated discounts** | Volume deals with hosts (5–20%) not passed to users | Invisible second margin. Unlocked by token volume. |
| 4 | **BYOK fee** | ~5% of would-be cost when users bring their own provider keys | Monetizes users we'd otherwise lose entirely. |
| 5 | **Token markup** | 0–5% on open models | Keep at 0–low while growing; it's a dial, not a foundation. |
| 6 | **Speed/enterprise upsell** | `:fast` variants (Groq/Cerebras) priced higher; SLA + invoicing tier | Speed is a product, price it as one. |

**Loss-leader rule**: Closed frontier models (OpenAI, Anthropic, Google, xAI) have one source → zero arbitrage. They exist to attract dollar-heavy traffic that monetizes via fees (#1, #4). Never expect token margin from them.

**Growth rule**: Token volume is the lever for everything (#2, #3, data flywheel). Subsidize high-volume cheap models; monetize dollar-heavy closed models via fees.

**Break-even math**: at ~5% blended take rate, ~$200k/mo routed volume ≈ $10k/mo gross margin. Infra target: < $1k/mo at millions of requests.

**Billing invariants**:
- **Prepaid credits only. Never postpaid.** Fraud kills routers.
- Per-request: pre-authorize estimated max cost → stream → meter actual tokens → settle → refund delta.
- Double-entry ledger, idempotent writes, all billing writes async (never on the hot path).
- **Nightly reconciliation** of our metered usage vs. provider invoices. Drift = silent margin leak = P0.

---

## 3. Provider Strategy

**Target: ~15 providers. MVP: 8. Hard ceiling: ~20** (past that is maintenance cost with no demand coverage gain).

| Tier | Providers | Why | Margin type |
|------|-----------|-----|-------------|
| **1 — Frontier closed** (4) | OpenAI, Anthropic, Google (Gemini), xAI | Dominate *dollar* spend (~30% of tokens, most of revenue) | Fees only (loss-leader) |
| **2 — Open-weight labs, direct** (5) | DeepSeek, Alibaba/Qwen, Xiaomi, Moonshot/Kimi, MiniMax | ~45% of market token volume; cheap, high-growth | Volume + light markup |
| **3 — Serverless GPU hosts** (6) | DeepInfra, Novita (cheapest); Together, Fireworks (reliability/catalog); Groq, Cerebras (speed tier) | 2–3 hosts per popular open model → arbitrage + failover | **Arbitrage (core profit)** |
| **4 — Optional** | Perplexity (search-grounded), Mistral, Featherless (long-tail) | Catalog breadth, marketing | Marginal |

**Provider admission criteria** (all required):
1. Serverless, token-billed API (no dedicated GPU rental, no capacity commitments).
2. OpenAI-compatible or cheaply adaptable.
3. Published, machine-readable pricing.
4. Either: unique models with real demand, **or** undercuts an existing host on a high-volume model by enough to arbitrage.

**Provider removal criteria** (any one): health score persistently below threshold; pricing opacity; <0.1% of routed volume for 90 days with no unique models.

---

## 4. Architecture: Two Planes

### 4.1 Data plane (hot path — every request)

```
Client
  → Edge auth        (API-key hash lookup, cache-first)
  → Rate limit       (Redis)
  → Credit pre-check (Redis-cached balance, pre-auth estimated max cost)
  → Router           (resolve model → ranked provider chain)
  → Stream           (provider #1; on 5xx/429/timeout → failover #2 → #3)
  → Meter            (count tokens from the stream itself)
  → Respond          (billing settle written async, post-response)
```

**Data-plane invariants**:
- Hot path touches **cache only**. Every DB write is async/post-response.
- Streaming is passthrough — never buffer full responses.
- Failover is mandatory on every request: a chain, not a single dispatch. A model with one healthy provider is served; a model with zero is a 502 *with an honest error*, never a silent hang.
- Provider errors are classified and mapped to correct client-facing status codes (429 ≠ 502 ≠ 402). Never leak raw provider error bodies.

### 4.2 Control plane (slow path — async, cron-driven)

| Job | Cadence | Purpose |
|-----|---------|---------|
| **Catalog sync** | Hourly | Poll every provider's `/models` + pricing → canonical registry |
| **Pricing normalization** | On sync | One canonical price unit. *This is the hardest data-quality problem in the system: a unit error inverts arbitrage and routes traffic to the most expensive host.* Sanity-gate every ingested price against cross-provider medians. |
| **Health scoring** | Continuous | Passive (real-traffic error/latency stats) + active probes → rolling per-(model, provider) score |
| **Stale model deprecation** | Daily | Models absent from provider catalogs get deactivated, then purged |
| **Ledger reconciliation** | Nightly | Our metered usage vs. provider invoices |
| **Rankings/data publication** | Daily | Usage stats → public rankings (SEO/traffic flywheel) |

**Registry invariants**:
- One canonical model ID scheme (`vendor/model`), one price unit, everywhere. No hardcoded model lists or prices in code — **the database is the source of truth for the catalog**.
- Every model row carries: canonical ID, per-provider offers (price, context, caps, modality), health, and quality gate status.

### 4.3 The router (where profit is made)

For each request, build the provider chain ranked by:

1. **Effective cost** = provider price × markup − negotiated discount
2. **Health score** — drop providers below threshold entirely
3. **Latency tier** — `:fast` routes to speed-tier hosts at a higher price; default routes to cheapest

Modifiers: BYOK keys pin to the user's provider (fee applies); explicit `provider` pin honored (no arbitrage, but honor it); free-tier models route only to free/cheapest endpoints with strict rate limits.

---

## 5. Non-Goals (equally binding)

- **No GPU hosting, no dedicated capacity, no model training.** We are a router.
- **No postpaid billing.**
- **No aggregator-of-aggregators as primary supply.** Aggregators (incl. OpenRouter itself) are *fallback only* — reselling a reseller has negative margin and no reliability edge.
- **No per-provider special-case logic in the hot path.** Provider differences are absorbed at the client-adapter layer and the registry, never in routing code.
- **No catalog breadth for its own sake.** 7,000 unpriced/unhealthy models is a liability, not a feature. Every listed model must be priced, health-checked, and routable, or it isn't listed.

---

## 6. KPIs (what "working" means)

| KPI | Target |
|-----|--------|
| Blended take rate | ≥ 5% of routed dollar volume |
| Arbitrage capture | ≥ 60% of open-model tokens routed to a host cheaper than reference price |
| Added latency (p50 / p99) | < 30ms / < 80ms over direct |
| Failover success | > 99% of requests served despite a single-provider failure |
| Reconciliation drift | < 0.5% monthly (metered vs. invoiced) |
| Infra COGS | < 1% of revenue |
| Providers live | 8 (MVP) → 15 (target), each meeting admission criteria |

---

## 7. Amendment Process

This document changes by deliberate amendment (PR titled `north-star:`), not by drift. When reality contradicts it — a revenue stream underperforms, a provider tier shifts, latency budgets prove wrong — amend the document with the evidence, then change the code.

---

## Amendments

### 2026-07-17 — MVP North Star alignment refactor

Decisions baked into `docs/superpowers/plans/2026-07-16-mvp-refactor.md` and executed on branch `refactor/mvp-north-star`:

| # | Decision | Call |
|---|---|---|
| D1 | Plans/subscriptions | **KEEP** — live prod billing/tier dependency; slimming deferred post-MVP |
| D2 | Prompt/code auto-router cluster (9 files, ~3.2k LOC) | **CUT** — second router engine violated §5 "one router"; `smart_router` is canonical |
| D3 | IP/bot fingerprinting | **TRIM** — kept key-auth + rate-limit path in `security_middleware`; deleted fingerprinting/bot-tier machinery + `datacenter_ips` + `ip_classification` |
| D4 | Status surface | **KEEP** `status_page.py`, `model_health.py`; **CUT** `health_timeline.py`, `downtime_logs.py`, `detailed_status.py`, `error_monitor.py`, `diagnostics.py`, `optimization_monitor.py` |
| D5 | Non-roster providers | **KEEP** `zai_client.py` (key in hand, GLM demand — Z.ai admitted to the Tier-3/4 roster) + `featherless_client.py` (Tier 4); **CUT** `cloudflare_workers_ai_client.py`, `nebius_client.py` (rejected) |

**Adapter consolidation — reduced scope from the original plan**: instead of moving all 10 OpenAI-compatible clients onto one shared adapter, the executed scope was **5 consolidated onto `openai_compat.py`** (deepinfra, together, fireworks, groq, zai) **+ 4 kept bespoke** (cerebras, xai, featherless, alibaba — each had provider-specific quirks not worth abstracting for one caller) **+ novita kept fetch-only** (catalog/pricing ingestion only, no consolidated request/stream path). Tier-2 providers added in Task 18 (deepseek, moonshot, minimax, xiaomi) were built directly as `adapter_configs.py` entries on the shared adapter from day one.

**huggingface — OPEN roster decision, not resolved by this refactor**: `huggingface_client.py` was deleted (Task 10) and removed from `FALLBACK_PROVIDER_PRIORITY` (no client to fail over into), but its catalog-fetch entry in `PROVIDER_FETCH_FUNCTIONS` is still alive — models keep appearing in the catalog with no way to actually serve them. This is flagged, not fixed; needs a product call on whether to re-add a client, drop the catalog entry, or keep it as a "coming soon" listing.

**Parked pending user/product decision** (frontend actively uses these paths; not deleted, not confirmed as permanent keeps): `chat_history` / `share` / `feedback` / `user_memory` / `chat_context` routes and DB tables, plus `audio` (Whisper transcription), `tools` (server-side TTS/calculator/code-exec), and `activity` routes. All still boot and serve traffic; none were evaluated against the admission/removal criteria in §3 because they're outside provider strategy — they need an explicit product decision on whether they belong in the MVP surface at all.
