# North Star Provider Convergence — Design

**Date:** 2026-07-20
**Status:** Approved (design shape), pending spec review
**Goal:** Make the provider set follow `docs/NORTH_STAR.md` §3 one-to-one — cut off-roster providers, scaffold the missing roster providers dark, and remove frontend catalog drift.

---

## 1. Canonical roster (North Star §3)

18 roster providers + `openrouter` (fallback-only per §5):

| Tier | Slugs |
|------|-------|
| 1 — Frontier closed | `openai`, `anthropic`, `google-vertex` (Gemini), `xai` |
| 2 — Open-weight direct | `deepseek`, `alibaba` (Qwen), `xiaomi`, `moonshot` (Kimi), `minimax` |
| 3 — Serverless GPU hosts | `deepinfra`, `novita`, `together`, `fireworks`, `groq`, `cerebras` |
| 4 — Optional | `perplexity`, `mistral`, `featherless` |
| Fallback (§5) | `openrouter` |

**Already in code (13):** openai, anthropic, google-vertex, xai, alibaba, deepinfra, novita, together, fireworks, groq, cerebras, featherless, openrouter.
**Missing from code (6) → scaffold dark:** deepseek, moonshot, minimax, xiaomi, perplexity, mistral.

## 2. Cut set (19)

**Routable cut (15):** `chutes, aimo, near, fal, huggingface, nebius, clarifai, simplismart, cloudflare-workers-ai, modelz, cohere, zai, morpheus, sybil, canopywave`

**Client-only cut (4):** `akash, alpaca-network, nosana, code-router`

Rationale: none appear in North Star §3; §5 forbids catalog breadth for its own sake; aggregators are fallback-only (openrouter is the sole retained aggregator).

## 3. Architecture / method

The **live routing switch is the DB `providers.is_active` flag**, which is auto-synced from the **`ENABLED_PROVIDERS` env var** on startup (`config.py:598`, `startup.py:206-245`). Therefore:

- **Routing** converges by setting `ENABLED_PROVIDERS` to the roster (deploy-time, reversible). Prod flip is the operator's (done by user, not this work).
- **Code** converges by editing the central registries + deleting client files. Code deletion is hygiene; it does not by itself change routing.

**Central edit points (single source of truth for provider identity):**
1. `src/services/model_catalog_sync.py` — `PROVIDER_FETCH_FUNCTIONS` map, imports, fetch fns, failover-priority dict.
2. `src/db/providers_db.py` — `_FALLBACK_PROVIDER_SLUGS`.
3. `src/services/provider_model_sync_service.py` — `PROVIDER_ENV_VAR_MAP`.
4. `src/config/config.py` — `ENABLED_PROVIDERS` default → roster.
5. `src/services/model_transformations.py` — model-id mappings for cut providers.
6. `src/routes/catalog.py` — `GATEWAY_REGISTRY` entries.
7. Pricing config — cut-provider entries.
8. `src/services/providers/` — delete the 19 client files (+ transformers).

After registry edits, `pytest` surfaces remaining references; fix by deletion/repoint.

**Dependent routes (must handle, not leave dangling):**
- `fal` — image-generation route(s). Remove or disable the endpoints that dispatch to fal.
- `huggingface` — catalog-fetch + 6 catalog endpoints (gap #5). Remove/repoint those endpoints.

## 4. Scaffold-dark (the 6)

Each new provider gets: a client module (OpenAI-compatible base where the API allows — deepseek/moonshot/minimax/mistral/perplexity are; xiaomi/MiMo gets a minimal adapter), a fetch function registered in `PROVIDER_FETCH_FUNCTIONS`, an entry in `PROVIDER_ENV_VAR_MAP` and `GATEWAY_REGISTRY`.

**Left out of `ENABLED_PROVIDERS`** → startup keeps them `is_active=False` → nothing routes until keys are funded and the operator adds the slug to `ENABLED_PROVIDERS`.

## 5. Frontend (gatewayz-frontend)

Delete hardcoded catalog tables (`lib/gateway-registry.ts` 75 providers, `lib/data.ts`, `lib/models-data.ts`, `lib/provider-config.ts`); repoint importers to the DB-driven `catalog-api.ts` (per `FRONTEND_NORTH_STAR_AUDIT.md` §4, Phase 3).

## 6. Non-goals / invariants

- **`openrouter` stays** (fallback-only, §5). Never delete.
- **No prod DB migration hand-written**; convergence rides the existing `ENABLED_PROVIDERS`→startup-sync mechanism.
- **No prod env flip in this work** — operator deploys.
- Scaffolded providers must not route with no key (dark).

## 7. Delivery

Three PRs off worktrees, in order:
- **PR-1** — backend cut (19 providers + dependent routes) + `ENABLED_PROVIDERS` default → roster.
- **PR-2** — backend scaffold-dark (6 providers).
- **PR-3** — frontend drift removal.

Each PR: green `pytest` / build before hand-off. Nothing deployed by this work.

## 8. Success criteria

- Backend registers exactly: 12 live roster + `openrouter` + 6 dark = 19 fetch-mapped; zero off-roster references (grep + pytest clean).
- `ENABLED_PROVIDERS` default = live roster (12, = roster minus dark 6) + openrouter = 13. `PROVIDER_ENV_VAR_MAP` = full 18 roster + openrouter = 19. Dark 6 mapped but not enabled.
- Frontend has no hardcoded provider/model tables; catalog UI reads the DB API.
- With roster `ENABLED_PROVIDERS`, startup sync deactivates every non-roster DB provider (verified on staging).
