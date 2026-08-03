# Task-Based Router — Rebuild Plan

**Status:** Design / plan. The prompt-router engine was deleted in the MVP refactor
(commit `e94e095c`, ~3.2k LOC). Its `/routers` + `/gateways` advertising was removed
in #2179. This plan rebuilds it as *real* task-based routing.

**North Star note:** §4.3 currently specifies only provider-selection + latency routing
for an **explicit** model. Task→model auto-selection is a new product capability and
needs a `north-star:` amendment before shipping. The profit invariant is preserved:
task routing sits *above* the provider router; provider cost/health/latency arbitrage
is unchanged.

---

## 1. Goal

One router API. A caller sends `model: "router:<mode>"` (or `router:code:<mode>`) and the
system: (1) classifies the task, (2) selects the best model for `(task, mode)` from the
live roster, (3) hands the resolved model to the **existing** provider failover chain
(cost → health → latency) which streams + bills. Everything downstream is reused.

Response echoes the routed model id (`"model": "openai/gpt-4o-mini"`) for transparency.

## 2. Architecture — 3 stages

```
request (messages, model="router:general:balanced")
  → [1] classify task      (heuristics; optional small-model classifier for ambiguous)
  → [2] select model       (task + mode → category tags + benchmarks + health → model id)
  → [3] provider dispatch   (EXISTING build_provider_failover_chain → stream → meter → settle)
```

**Stage 1 — Task classification.** Infer task type + requirements from the messages:
`general chat | code | reasoning | long-context | vision`. Signals: prompt length,
fenced code / language keywords, presence of tools/images, explicit hints. MUST be fast
(North Star <50ms hot-path budget) → heuristics first; an optional cheap-model classifier
only for genuinely ambiguous inputs, behind a flag.

**Stage 2 — Model selection.** For `(task, mode)`, rank the live roster:
- `balanced` — weighted quality/cost/latency
- `quality` — best benchmark/quality for the task
- `cost` — cheapest capable model
- `latency` — fastest (speed-tier hosts, `:fast`)
- code modes: `price | quality | agentic` (agentic = premium multi-step models)
Inputs: model **category tags** (cheapest/fastest/largest/best-for-code), benchmark
scores (code), context length, modality, current health. Selection is pure + testable
(given a catalog snapshot → deterministic model id).

**Stage 3 — Dispatch.** Resolve selected model → `build_provider_failover_chain` (already
live) → stream/meter/settle. No new billing or provider code.

## 3. Building blocks

| Block | State | Action |
|---|---|---|
| Model **category tags** (cheapest/fastest/largest/best-for-code) | built on branch `feat/model-categorization`, **unmerged** | rebase onto current main + merge (Phase 0) |
| Provider failover chain (stage 3) | **live** | reuse as-is |
| Code benchmark data (old code-router was benchmark-based) | removed with the engine | re-source or start with a curated table |
| `/routers` + `/gateways` advertising | emptied (#2179) | re-populate once functional (Phase 4) |

## 4. Data model

- **Model metadata** (catalog `models.metadata`): `category_tags[]`, `benchmark_scores{}`,
  `context_length`, `modality`, `latency_tier`. Tags come from the categorization branch.
- **Router policy** (config or small `router_policies` table): `mode → {weights, candidate
  filter}`. Keep it data-driven so tuning a mode is not a code change.

## 5. API surface (one router API)

- `POST /v1/chat/completions` with `model: "router:general:<mode>"` or `"router:code:<mode>"`
  (bare `router:general` = default mode). Keep the `gatewayz-*` aliases for back-compat.
- A read endpoint (`GET /routers`) re-advertises modes **only once they resolve**.
- Reject unknown modes with a clear 400 (not the current silent "no pricing" failure).

## 6. Phases

- **Phase 0 — tags.** Rebase + merge `feat/model-categorization`; verify tags populate on sync.
- **Phase 1 — general router.** Heuristic classifier + rule-based selection for
  `router:general:<mode>` over the live roster. Pure selection unit-tested against catalog
  fixtures. Wire into `resolve_model_routing` (currently a rejecting stub).
- **Phase 2 — code router.** `router:code:<mode>` with a curated benchmark table +
  `agentic` premium tier.
- **Phase 3 — smarter classification (optional).** Cheap-model classifier for ambiguous
  inputs, behind a flag + latency guard; learn from usage stats.
- **Phase 4 — re-advertise.** Re-populate `/routers` + `/gateways`; frontend wires the modes.

## 7. Invariants & risks

- **Latency:** default path must classify + select without an LLM call (heuristics) to stay
  in the <50ms hot-path budget. LLM classification is opt-in only.
- **Safety:** misclassification → wrong model. Mitigate: default to `balanced`/cheapest-capable,
  always honor an explicit model id (bypass router), echo the routed model back.
- **Transparency:** always return the concrete model used + let users pin.
- **Profit:** provider arbitrage (stage 3) is untouched; task routing only chooses the model.
- **North Star:** ship the `north-star:` §4.3 amendment (task→model selection) before Phase 1
  merges to prod.

## 8. Success criteria

- `router:general:cost` picks the cheapest capable model; `:quality` the best; `:latency` a
  speed-tier host — verified on catalog fixtures + live smoke tests.
- Routed model id is returned; explicit models still bypass the router.
- p50 added latency for the router path < 50ms over a direct explicit-model call.
- `/routers` advertises only modes that actually resolve.
