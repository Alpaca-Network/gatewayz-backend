# Model Categorization — Design Spec

**Date:** 2026-07-04
**Status:** Draft — awaiting approval
**Goal:** Auto-assign descriptive categories (cheapest, fastest, largest, smartest, …) to every model in the catalog, driven by data we already pull from providers. Categories are multi-label tags on each model and become the candidate-filter for routing in a later phase.

---

## 1. Problem & Scope

We want every model — current and all future ones — to carry a set of derived category tags so that:

1. The catalog/UI can group and filter models ("show me the fastest models").
2. A later routing phase can narrow the candidate pool to models carrying a tag before applying policy scoring (`smart_router` / `routing_policies`).

**Decisions locked with the requester:**

- **Category model:** multi-label **tags on each model** (a model can be `['cheapest','fastest','free']`). Not a routing preference, not a M2M join table.
- **Coverage:** **uniform, all models auto-categorized** — no curated allowlist, no special-casing of the "select few that currently work."
- **Membership rule:** **absolute thresholds**, config-driven (a DB rules table), tunable without code deploy.
- **Tag vocabulary:** core relative tags + capability tags + a composite + coarse tiers (see §3).

**Out of scope (this phase):**
- Changing `smart_router.py` / `model_selector.py` selection logic. This phase *produces* the tags; wiring them into candidate selection is a follow-up.
- Per-user or per-key category preferences.
- Benchmark harness for quality scores (we consume existing `model_quality_scores`).

---

## 2. Existing data we categorize on

No new provider fetching is required — every input already lands in the DB via the sync pipeline:

| Dimension | Source (existing) |
|---|---|
| price | `model_pricing` (prompt/completion $/token), `model_provider_offers.upstream_cost` |
| latency | `models.latency_tier` (1=ultra … 4=slow), `offers.p50_ms`/`p95_ms` |
| context size | `models.context_length`, `models.max_output_tokens` |
| quality | `model_quality_scores` (per `task_type`), `offers.quality_prior` |
| reasoning | `models.is_reasoning` |
| vision | `models.modality`, `models.capabilities` (jsonb) |
| free | `models.is_free` |

---

## 3. Tag vocabulary & default rules

Absolute thresholds, seeded into `category_rules`. All thresholds are tunable in-DB.

| Tag | Kind | Source field | Default rule |
|---|---|---|---|
| `cheapest` | relative→absolute | blended $/1M tokens¹ | ≤ 0.50 |
| `fastest` | relative→absolute | `latency_tier` | ≤ 2 |
| `largest` | relative→absolute | `context_length` | ≥ 200_000 |
| `long-context` | capability | `context_length` | ≥ 128_000 |
| `smartest` | relative→absolute | quality (overall / `unknown` task) | ≥ 85 |
| `coding` | capability | quality (`code_generation`) | ≥ 85 |
| `reasoning` | boolean | `is_reasoning` | = true |
| `vision` | capability | `modality` / `capabilities` | modality contains `image`/`vision` |
| `free` | boolean | `is_free` | = true |
| `balanced` | composite | quality ÷ blended $/1M | ratio ≥ threshold² |
| `flagship` | tier | quality band | quality ≥ 90 |
| `mid` | tier | quality band | 70 ≤ quality < 90 |
| `budget` | tier | quality band | quality < 70 |

¹ **Blended price** = `prompt_price * 0.25 + completion_price * 0.75` normalized to $/1M tokens (completion-weighted, since output dominates cost). Exact weights are a `category_rules` param so they're tunable.
² `balanced` ratio default TBD-at-seed — set after a one-time distribution check over the live catalog so it selects a sensible top slice; recorded in the seed migration.

**Tier tags are mutually exclusive** (exactly one of flagship/mid/budget), enforced in the engine, not the DB. Every categorized model gets exactly one tier even if quality is unknown → defaults to `budget` (documented, fail-safe).

**Missing data:** if a rule's source field is null (e.g. no quality score yet, no pricing row), that specific tag is simply not applied — never guessed. A model with zero known dimensions gets `[]` plus the default `budget` tier. This is logged at debug for observability.

---

## 4. Data model

### 4.1 `models.categories text[]`
```sql
ALTER TABLE public.models ADD COLUMN IF NOT EXISTS categories text[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_models_categories ON public.models USING GIN (categories);
```
- Flat array chosen over a M2M join table: routing filters are `categories @> '{fastest}'` (GIN, fast); no per-assignment metadata is needed; matches the existing flat-column pattern (`is_free`, `latency_tier`).
- `NOT NULL DEFAULT '{}'` → metadata-only add on PG 11+, no table rewrite.

### 4.2 `category_rules` (config, tunable without deploy)
```sql
CREATE TABLE IF NOT EXISTS public.category_rules (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category     text NOT NULL,                 -- e.g. 'cheapest'
    dimension    text NOT NULL,                 -- 'blended_price' | 'latency_tier' | 'context_length' | 'quality' | 'quality_code' | 'is_reasoning' | 'modality' | 'is_free' | 'value_ratio' | 'quality_band'
    operator     text NOT NULL,                 -- 'lte' | 'gte' | 'eq' | 'contains' | 'band'
    threshold    numeric,                       -- null for boolean/contains rules
    threshold2   numeric,                       -- upper bound for 'band' (e.g. mid tier)
    params       jsonb NOT NULL DEFAULT '{}',   -- e.g. blended-price weights
    enabled      boolean NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT category_rules_unique UNIQUE (category)
);
```
- RLS enabled, no permissive policy → backend `service_role` only (matches repo posture).
- Seeded with the §3 defaults in the same migration.

---

## 5. Categorizer engine — `src/services/model_categorizer.py`

Single pure function + a thin rules loader.

```python
def compute_categories(
    model: dict,                    # a models row (context_length, latency_tier, is_reasoning, is_free, modality, capabilities)
    pricing: dict | None,           # model_pricing row (prompt_price, completion_price) or None
    quality: dict[str, float],      # {task_type: score}, may be empty
    rules: list[CategoryRule],      # loaded from category_rules (cached)
) -> list[str]:
    """Return the sorted list of category tags for one model. Pure, deterministic, no I/O."""
```

- **Pure & deterministic** → fully unit-testable with table-driven cases; no DB in the hot path.
- Rules are loaded once and cached (mirror `model_capabilities_cache`, 15-min refresh) so a rules edit propagates on the next sync without deploy.
- Tier resolution guarantees exactly one of flagship/mid/budget.
- Emits a structured debug log of `{model_id, assigned, skipped_for_null_dimension}` for observability.

---

## 6. Wiring

### 6.1 Sync hook (all future models, automatic)
`src/db/models_catalog_db.py::bulk_upsert_models` already post-processes upserts (`_sync_pricing_to_model_pricing` at :896). Add a sibling step `_sync_categories(supabase, upserted_models)` that:
1. loads `category_rules` (cached),
2. for each upserted model, fetches its pricing + quality (batched), runs `compute_categories`,
3. writes `categories` back in a single batched update.

This makes categorization an intrinsic part of every sync → every future model is auto-tagged with zero extra wiring.

### 6.2 Backfill (existing catalog)
`scripts/backfill_model_categories.py` — one-shot: page through all live (`deprecated_at IS NULL`) models, run the engine, batch-update. Idempotent (recomputes from source each run). Safe to re-run after any threshold change.

### 6.3 Exposure
- Include `categories` in catalog model serialization (`src/routes/catalog.py`).
- Add optional `?category=<tag>` filter to the catalog list endpoint → `categories @> ARRAY[tag]`. Multiple tags = AND.

### 6.4 Routing (follow-up, not this phase)
The tags are the candidate-filter primitive: a later change lets the router restrict the offer pool to `categories @> '{tag}'` before `smart_router` scores by `routing_policies` weights. Called out so the data model is routing-ready; no selection-logic change ships here.

---

## 7. Testing

- **Unit (`tests/services/test_model_categorizer.py`):** table-driven over `compute_categories` — each tag's boundary (just-in / just-out), null-dimension skips, exactly-one-tier invariant, empty-input → `['budget']`.
- **Rules loader:** cache hit/miss, disabled rule excluded, unknown dimension ignored (fail-safe).
- **Integration:** `bulk_upsert_models` writes expected `categories` for a seeded fixture set; `?category=` filter returns the right models.
- **Migration:** column + index + rules table apply idempotently; seed rows present.

---

## 8. Rollout

1. Migration: `categories` column + GIN index + `category_rules` table + seed rules.
2. Ship `model_categorizer.py` + unit tests (no wiring yet).
3. Wire `_sync_categories` into `bulk_upsert_models`.
4. Run backfill script against the live catalog; spot-check distributions (how many models per tag) and tune `balanced` ratio + any obviously-off thresholds in `category_rules`.
5. Expose `categories` + `?category=` filter in catalog API.
6. (Follow-up) Router candidate-filter integration.

---

## 9. Open items to confirm at implementation

- `balanced` ratio threshold: set from the live distribution during rollout step 4.
- Blended-price weights (0.25/0.75) — confirm against how billing weights prompt vs completion elsewhere.
- Whether `largest` and `long-context` both earning at 200k is desired (largest ⊂ long-context) — intentional (largest is the elite subset).
