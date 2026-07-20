# North Star Provider Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the provider code + config follow `docs/NORTH_STAR.md` §3 one-to-one — cut 19 off-roster providers, scaffold 6 roster providers dark, remove frontend catalog drift.

**Architecture:** Live routing is gated by the DB `providers.is_active` flag, auto-synced from the `ENABLED_PROVIDERS` env var at startup (`config.py:598`, `startup.py:206-245`). This work edits the central code registries + deletes client files + sets the `ENABLED_PROVIDERS` default to the roster. Prod flip/deploy is the operator's.

**Tech Stack:** Python 3.10-3.12, FastAPI, pytest, Supabase; frontend Next.js/TS.

## Global Constraints

- Roster (18) = `openai, anthropic, google-vertex, xai, deepseek, alibaba, xiaomi, moonshot, minimax, deepinfra, novita, together, fireworks, groq, cerebras, perplexity, mistral, featherless`.
- `openrouter` is **retained** as fallback-only (§5) — enabled, never deleted, not counted in the 18.
- Cut set (19): `chutes, aimo, near, fal, huggingface, nebius, clarifai, simplismart, cloudflare-workers-ai, modelz, cohere, zai, morpheus, sybil, canopywave, akash, alpaca-network, nosana, code-router`.
- Scaffold-dark (6): `deepseek, moonshot, minimax, xiaomi, perplexity, mistral` — registered but **excluded from `ENABLED_PROVIDERS`** so `is_active=False`.
- **Live roster (12)** = roster minus the dark 6 = `openai, anthropic, google-vertex, xai, alibaba, deepinfra, novita, together, fireworks, groq, cerebras, featherless`.
- `ENABLED_PROVIDERS` default = live roster (12) + `openrouter` = **13**. `_FALLBACK_PROVIDER_SLUGS` = same **13**. Dark 6 excluded from both (they have no client code + no keys yet).
- `PROVIDER_ENV_VAR_MAP` keys = 18 roster + `openrouter` = **19** (dark 6 keep their env-var entries; only their enablement is withheld).
- No hand-written prod DB migration. No prod env flip. Each PR: `pytest` green before hand-off.
- Verification discipline for deletions: `grep` clean + full `pytest` green (the existing suite is the regression test).

---

## PR-1 — Backend cut

### Task 1: Roster constants + config defaults

**Files:**
- Modify: `src/config/config.py:598` (`ENABLED_PROVIDERS` default)
- Modify: `src/db/providers_db.py:22-48` (`_FALLBACK_PROVIDER_SLUGS`)
- Modify: `src/services/provider_model_sync_service.py:35-59` (`PROVIDER_ENV_VAR_MAP`)
- Test: `tests/services/test_provider_roster.py` (new)

**Interfaces:**
- Produces: roster constants used by every later task; `PROVIDER_ENV_VAR_MAP` keys = roster + openrouter + dark 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_provider_roster.py
ROSTER = {  # full North Star §3 roster (18); dark 6 included here
    "openai","anthropic","google-vertex","xai","deepseek","alibaba","xiaomi",
    "moonshot","minimax","deepinfra","novita","together","fireworks","groq",
    "cerebras","perplexity","mistral","featherless",
}
DARK = {"deepseek","moonshot","minimax","xiaomi","perplexity","mistral"}
LIVE_ROSTER = ROSTER - DARK  # 12 providers that have client code + keys today
ENABLED_DEFAULT = LIVE_ROSTER | {"openrouter"}  # 13 — dark 6 withheld until keys land
ENV_MAP_KEYS = ROSTER | {"openrouter"}  # 19 — dark 6 keep their env-var mapping
CUT = {
    "chutes","aimo","near","fal","huggingface","nebius","clarifai","simplismart",
    "cloudflare-workers-ai","modelz","cohere","zai","morpheus","sybil","canopywave",
    "akash","alpaca-network","nosana","code-router",
}

def test_fallback_slugs_are_live_roster_plus_openrouter():
    from src.db.providers_db import _FALLBACK_PROVIDER_SLUGS
    assert set(_FALLBACK_PROVIDER_SLUGS) == ENABLED_DEFAULT

def test_env_var_map_is_roster_plus_openrouter_no_cut():
    from src.services.provider_model_sync_service import PROVIDER_ENV_VAR_MAP
    keys = set(PROVIDER_ENV_VAR_MAP.keys())
    assert CUT.isdisjoint(keys)
    assert keys == ENV_MAP_KEYS

def test_enabled_providers_default_excludes_dark():
    import os, importlib
    os.environ.pop("ENABLED_PROVIDERS", None)
    import src.config.config as c; importlib.reload(c)
    assert c.Config.ENABLED_PROVIDERS == frozenset(ENABLED_DEFAULT)
    assert DARK.isdisjoint(c.Config.ENABLED_PROVIDERS)  # dark 6 not enabled
```

- [ ] **Step 2: Run — expect FAIL** — `pytest tests/services/test_provider_roster.py -o addopts="" -v`
- [ ] **Step 3:** Set `config.py:598` default string to the 13 live-roster+openrouter slugs: `"openai,anthropic,google-vertex,xai,alibaba,deepinfra,novita,together,fireworks,groq,cerebras,featherless,openrouter"`. Set `_FALLBACK_PROVIDER_SLUGS` to the same 13. Set `PROVIDER_ENV_VAR_MAP` to the 19 (full roster + openrouter, incl. dark-6 env vars `DEEPSEEK_API_KEY, MOONSHOT_API_KEY, MINIMAX_API_KEY, XIAOMI_API_KEY, PERPLEXITY_API_KEY, MISTRAL_API_KEY`; drop all CUT entries).
- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `git commit -am "feat(providers): roster constants + ENABLED_PROVIDERS default to North Star §3"`

### Task 2: Fetch map + imports (`model_catalog_sync.py`)

**Files:**
- Modify: `src/services/model_catalog_sync.py:17,23-51` (imports), `:69-99` (`PROVIDER_FETCH_FUNCTIONS`), `:58-66` (`_PROVIDER_TIERS` — drop `cloudflare-workers-ai`)
- Test: extend `tests/services/test_provider_roster.py`

**Interfaces:**
- Consumes: ROSTER/CUT from Task 1 test module.
- Produces: `PROVIDER_FETCH_FUNCTIONS` keys = roster (that have fetch fns) + openrouter; no CUT keys.

- [ ] **Step 1: Add failing test**

```python
def test_fetch_map_has_no_cut_providers():
    from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS
    assert CUT.isdisjoint(PROVIDER_FETCH_FUNCTIONS.keys())
```

- [ ] **Step 2: Run — expect FAIL** — `pytest tests/services/test_provider_roster.py::test_fetch_map_has_no_cut_providers -o addopts="" -v`
- [ ] **Step 3:** Remove the 15 routable-cut entries from `PROVIDER_FETCH_FUNCTIONS` (chutes, aimo, near, fal, huggingface, nebius, clarifai, simplismart, cloudflare-workers-ai, modelz, cohere, zai, morpheus, sybil, canopywave) and their `import` lines (17, 23-24, 26, 28-33, 35, 40-43, 47-49, 51). Remove `cloudflare-workers-ai` from `_PROVIDER_TIERS`. Leave `google-vertex, featherless, openrouter, deepinfra, groq, fireworks, together, alibaba, cerebras, xai, novita, openai, anthropic`.
- [ ] **Step 4: Run — expect PASS**; also `python -c "import src.services.model_catalog_sync"` (import must not error).
- [ ] **Step 5: Commit** — `git commit -am "feat(providers): drop 15 off-roster providers from fetch map + imports"`

### Task 3: Model-id transformations

**Files:**
- Modify: `src/services/model_transformations.py` (remove cut-provider mappings: `nebius/` prefix `:191`, `chutes` `:564`, `z-ai`/`zai` `:615`, and any other cut-provider branches)
- Test: `tests/services/test_provider_roster.py`

- [ ] **Step 1: Add failing test**

```python
def test_transformations_reference_no_cut_provider():
    import src.services.model_transformations as m, inspect
    src = inspect.getsource(m)
    for slug in ("chutes","nebius","zai","clarifai","cohere","modelz","morpheus","sybil","canopywave","simplismart"):
        assert slug not in src, f"{slug} still referenced in model_transformations"
```

- [ ] **Step 2: Run — expect FAIL** — `pytest tests/services/test_provider_roster.py::test_transformations_reference_no_cut_provider -o addopts="" -v`
- [ ] **Step 3:** Delete the cut-provider prefix/branch entries in `model_transformations.py`. Re-run full grep `grep -nE "chutes|nebius|z-ai|zai|clarifai|cohere|modelz|morpheus|sybil|canopywave|simplismart" src/services/model_transformations.py` → zero hits.
- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `git commit -am "feat(providers): purge cut-provider model transformations"`

### Task 4: Delete client modules + fix dependent routes

**Files:**
- Delete: the 19 client files under `src/services/providers/` (`chutes_client.py, aimo_client.py, near_client.py, fal_image_client.py, nebius_client.py, clarifai_client.py, simplismart_client.py, cloudflare_workers_ai_client.py, modelz_client.py, cohere_client.py, zai_client.py, morpheus_client.py, sybil_client.py, canopywave_client.py, akash_client.py, alpaca_network_client.py, nosana_client.py, code_router_client.py`) + `src/services/huggingface_models.py` (huggingface fetch)
- Modify: `src/routes/*.py` dispatch/catalog endpoints that import deleted clients (esp. `fal` image-gen endpoint, `huggingface` catalog endpoints per gap #5)
- Test: full suite

- [ ] **Step 1:** Delete the client files. Run `python -c "import src.main"` — collect every `ImportError`.
- [ ] **Step 2:** For each import error, remove/repoint the reference. For `fal` image-generation endpoints: remove the route (or return 501 if the router must keep the path); for `huggingface` catalog endpoints: remove the 6 endpoints or repoint to the DB catalog. Do NOT leave dangling imports.
- [ ] **Step 3: Run** — `python -c "import src.main"` clean, then `pytest -o addopts="" -q` — capture failures from deleted-provider tests.
- [ ] **Step 4:** Delete/adjust provider-specific tests for cut providers (`tests/**/*<slug>*`). Re-run `pytest -o addopts="" -q` → green.
- [ ] **Step 5: Commit** — `git commit -am "feat(providers): delete 19 cut client modules + fix dependent routes"`

### Task 5: Grep sweep + PR-1 hand-off

- [ ] **Step 1:** `for s in chutes aimo near nebius clarifai simplismart cloudflare_workers modelz cohere zai morpheus sybil canopywave akash nosana code_router; do echo "== $s =="; grep -rniE "\b$s\b" src/ --include=*.py | grep -viE "fallback|final"; done` — investigate every remaining hit; remove or justify.
- [ ] **Step 2:** `pytest -o addopts="" -q` → green. Push branch, open PR titled `north-star: cut off-roster providers to §3 roster`.

---

## PR-2 — Scaffold-dark (6 providers)

### Task 6: OpenAI-compatible dark clients (deepseek, moonshot, minimax, mistral, perplexity)

**Files:**
- Create: `src/services/providers/{deepseek,moonshot,minimax,mistral,perplexity}_client.py`
- Test: `tests/services/test_dark_providers.py` (new)

**Interfaces:**
- Produces: `fetch_models_from_<slug>()` for each; OpenAI-compatible chat client following the pattern in an existing kept client (e.g. `deepinfra_client.py`).

- [ ] **Step 1: Write failing test**

```python
import importlib, pytest
DARK = ["deepseek","moonshot","minimax","mistral","perplexity"]
@pytest.mark.parametrize("slug", DARK)
def test_dark_client_exposes_fetch(slug):
    mod = importlib.import_module(f"src.services.providers.{slug}_client")
    assert hasattr(mod, f"fetch_models_from_{slug}")
```

- [ ] **Step 2: Run — expect FAIL** — `pytest tests/services/test_dark_providers.py -o addopts="" -v`
- [ ] **Step 3:** Implement each client by copying the kept OpenAI-compatible pattern (base URL + `api_key_env_var`, `fetch_models_from_<slug>()` reading `/models`, chat via the shared OpenAI-compatible dispatch). Base URLs: deepseek `https://api.deepseek.com`, moonshot `https://api.moonshot.ai/v1`, minimax `https://api.minimax.chat/v1`, mistral `https://api.mistral.ai/v1`, perplexity `https://api.perplexity.ai`.
- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `git commit -am "feat(providers): scaffold 5 OpenAI-compatible dark clients"`

### Task 7: Xiaomi (MiMo) dark client

**Files:**
- Create: `src/services/providers/xiaomi_client.py`
- Test: `tests/services/test_dark_providers.py`

- [ ] **Step 1: Add failing test** — extend `DARK` param with `"xiaomi"`.
- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3:** Implement `xiaomi_client.py` with `fetch_models_from_xiaomi()`. If MiMo has no public `/models` endpoint, return a static single-model list (documented) — still dark, so never routed.
- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `git commit -am "feat(providers): scaffold xiaomi/MiMo dark client"`

### Task 8: Register the 6 dark providers (registered ≠ enabled)

**Files:**
- Modify: `src/services/model_catalog_sync.py` (imports + `PROVIDER_FETCH_FUNCTIONS`)
- Test: `tests/services/test_dark_providers.py`

**Interfaces:**
- Consumes: `fetch_models_from_<slug>` from Tasks 6-7; `ENABLED_PROVIDERS` from Task 1.

- [ ] **Step 1: Write failing test**

```python
def test_dark_registered_but_not_enabled():
    from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS
    from src.config.config import Config
    for slug in ["deepseek","moonshot","minimax","mistral","perplexity","xiaomi"]:
        assert slug in PROVIDER_FETCH_FUNCTIONS          # registered
        assert slug not in Config.ENABLED_PROVIDERS       # dark
```

- [ ] **Step 2: Run — expect FAIL** — `pytest tests/services/test_dark_providers.py::test_dark_registered_but_not_enabled -o addopts="" -v`
- [ ] **Step 3:** Add the 6 imports + `PROVIDER_FETCH_FUNCTIONS` entries in `model_catalog_sync.py`. Do NOT add them to `ENABLED_PROVIDERS` (Task 1 already excludes them).
- [ ] **Step 4: Run — expect PASS**; `pytest -o addopts="" -q` green.
- [ ] **Step 5: Commit + PR** — `git commit -am "feat(providers): register 6 dark providers (excluded from ENABLED_PROVIDERS)"`; PR `north-star: scaffold Tier-2/optional providers dark`.

---

## PR-3 — Frontend drift (gatewayz-frontend)

### Task 9: Delete hardcoded catalog tables, repoint to DB catalog API

**Files (repo `gatewayz-frontend`):**
- Delete: `lib/gateway-registry.ts`, `lib/data.ts`, `lib/models-data.ts`, `lib/provider-config.ts`
- Modify: their importers → `lib/catalog-api.ts` (DB-driven)
- Test: `pnpm build` + Playwright e2e

- [ ] **Step 1:** In a `gatewayz-frontend` worktree, `grep -rl "gateway-registry\|models-data\|lib/data\|provider-config" app components lib` to list importers.
- [ ] **Step 2:** Repoint each importer to `catalog-api.ts` equivalents; delete the 4 hardcoded modules.
- [ ] **Step 3: Run** — `pnpm build` clean; `pnpm test:e2e` (or Playwright) green.
- [ ] **Step 4: Commit + PR** — `north-star: remove hardcoded provider/model tables; read DB catalog`.

---

## Self-Review

- **Spec coverage:** cut (Tasks 1-5) ✓; scaffold-dark (Tasks 6-8) ✓; frontend (Task 9) ✓; ENABLED_PROVIDERS default (Task 1) ✓; openrouter retained (Global Constraints, never in CUT) ✓; dependent fal/HF routes (Task 4) ✓.
- **Placeholder scan:** base URLs + slug lists are concrete; deletion tasks use grep+pytest gates (appropriate for a removal refactor). No TBD.
- **Type consistency:** `fetch_models_from_<slug>` naming consistent across Tasks 2/6/7/8; ROSTER/CUT/ENABLED_DEFAULT defined once in Task 1 test module, reused by name.
- **Known risk:** xiaomi/MiMo API surface unverified — Task 7 allows a static fallback; stays dark so no routing risk.
