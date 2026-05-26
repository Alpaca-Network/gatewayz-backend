# Cost Reduction — Design Spec

**Date:** 2026-05-25
**Branch base:** `main`
**Working branch:** `feat/cost-reduction` (in worktree)

## Goal

Cut recurring SaaS + infra cost for the gatewayz-backend without removing observability or breaking endpoints. Target savings: **$250–300/mo** (~$3k/year) with code changes only — no architectural rewrites.

## Scope

In-scope (this spec):
1. Observability consolidation — remove redundant telemetry backends.
2. Catalog/DB query tightening — replace `select("*")` with explicit columns in hot paths; add Redis caching for `/models`.
3. Health-service migration — kill always-on Railway container; replace with cron-triggered job.
4. Log volume reduction — default `LOG_LEVEL=WARNING` in prod; lower hot-path log noise.
5. Rate-limiter consolidation — single Redis-backed limiter, retire in-memory deque path.
6. Vercel cold-start fixes — lazy provider SDK imports; defer non-critical init.
7. Requirements pruning — confirm heavy provider SDKs (`google-cloud-aiplatform`, `clarifai`) are not installed on Vercel.

Out-of-scope:
- Repricing models / changing markups.
- Frontend changes.
- Touching the in-flight `fix/abuse-prevention-cost-tracking` branch.

## Non-goals

- Removing **all** observability. Sentry stays; one tracing path stays.
- Changing the public API surface (routes, payloads).
- Schema migrations beyond what's strictly required.

## Approach

Pure subtractive + caching. No new features. Each change behind a feature flag where removal would be risky, hard-deletion where it isn't.

### Category A — Observability consolidation (HIGH)

Keep: **Sentry** (errors), **PostHog** (product analytics), **Prometheus** (metrics scrape).
Remove or gate-off-by-default: **Pyroscope, Loki, Tempo/OTLP, Arize, Braintrust**.

Files touched:
- `src/services/startup.py` — drop init blocks for pyroscope/tempo/arize/braintrust; keep Sentry.
- `src/services/pyroscope_config.py` — delete.
- `src/services/tempo_otlp.py` — delete.
- `src/services/braintrust_service.py` — delete.
- `src/handlers/braintrust_logging.py` — delete.
- `src/config/arize_config.py` — keep file but make `init_arize_otel()` a no-op when `ARIZE_ENABLED!=true` (already env-gated; set default false in `config.py`).
- `src/routes/chat.py` — remove all `braintrust` imports, decorators, span starts, log calls. Replace `@traced` with no-op.
- `requirements.txt` — remove `braintrust==0.1.0`; keep `sentry-sdk` and `posthog`.
- `pyproject.toml` — sync.

Behavior change: Pyroscope/Loki/Tempo/Arize calls become no-ops or are physically removed. No public endpoint affected.

### Category B — `/models` query tightening + caching (HIGH)

Hot path: `src/db/models_catalog_db.py` makes `select("*, providers!inner(*)")` calls on every catalog request.

Changes:
- Replace `select("*, providers!inner(*)")` with explicit column list in `get_all_models_for_catalog`, `get_models_by_provider`, `get_model_by_id` (4 call sites in `models_catalog_db.py`).
  - Required columns from inspection: `id, model_name, display_name, provider_id, context_length, input_cost, output_cost, modality, is_active, source_gateway`.
  - Provider join: `providers(slug, name, site_url)`.
- Wrap `get_all_models_for_catalog()` with Redis cache: key `catalog:v1:all`, TTL 3600s. Bust on provider/model sync via existing `model_catalog_cache.invalidate()`.
- Confirm the existing `src/services/cache/model_catalog_cache.py` is wired into `routes/catalog.py`; if not, wire it.

Behavior change: smaller payloads on the wire from Supabase → less egress + faster JSON parse. Response shape identical.

### Category C — Health-service: always-on → on-demand (HIGH)

Today: dedicated Railway container with 32GB memory limit polls 10k+ models continuously.

Change:
- Delete `health-service/railway.toml` always-on deploy (keep `health-service/main.py` runnable as a one-shot).
- Convert `health-service/main.py` from FastAPI long-running to a CLI entrypoint runnable via Railway Cron / GitHub Actions on a schedule (every 30 min for critical tier, every 4 h for cold tier).
- Add `.github/workflows/health-monitor.yml` triggered on a `schedule:` cron (`*/30 * * * *`) that runs the tiered check and writes results to Redis.
- Main API reads `simple_health_cache` only — never invokes live polling on request path. (Already true; verify.)

Behavior change: model health staleness window grows from continuous to 30 min for hot tier. Acceptable per `intelligent_health_monitor` docs (which already uses 5min/30min/2-4h tiers).

### Category D — Log volume (MEDIUM)

Files touched:
- `src/config/logging_config.py:384,395,422` — change default root level from `INFO` to `WARNING` when `ENVIRONMENT=production`, keep `INFO` for dev.
- Add env var `LOG_LEVEL` override.
- `src/services/startup.py` — gate Loki handler init behind `LOKI_ENABLED=true` (already gated; just flip default to false).

Behavior change: ~80% drop in log line volume in prod. Errors still flow through Sentry.

### Category E — Rate limiter consolidation (MEDIUM)

Three limiters today: `services/rate_limiting.py` (Redis sliding window), `services/auth_rate_limiting.py` (in-memory deque, O(n) per IP), `services/endpoint_rate_limiter.py`.

Change (minimal-risk):
- Keep `services/rate_limiting.py` as the canonical limiter.
- Refactor `services/auth_rate_limiting.py` to use Redis under the hood (delegate to `rate_limiting.py`). Drop in-memory deque.
- Leave `endpoint_rate_limiter.py` untouched in this PR (separate concern).

Behavior change: identical rate-limit behavior; memory footprint drops; no GC pressure from per-IP deques.

### Category F — Vercel cold start (MEDIUM)

Files touched:
- `src/main.py` — move Sentry init behind `if not os.getenv("VERCEL")` OR keep it but switch to `traces_sample_rate=0.0` on Vercel (errors only).
- Provider client imports — already lazy in most clients; spot-check `src/services/openrouter_client.py`, `featherless_client.py`, `chutes_client.py` and any that import SDKs at module top.
- `api/index.py` — verify it doesn't trigger eager catalog sync.

Behavior change: cold start time drops; warm requests unaffected.

### Category G — Requirements pruning (LOW)

- Move `google-cloud-aiplatform`, `clarifai`, `huggingface-hub`, `novita-client` from base `requirements.txt` into `pyproject.toml` `[providers]` extras (already present per CLAUDE.md). Verify base install on Vercel does not pull them.
- Remove `braintrust==0.1.0`, `python-snappy` (loki dep) if Loki gone.

Behavior change: smaller Vercel function bundle.

## Components / file impact map

| Category | Files | Net LOC |
|----------|-------|---------|
| A. Observability cut | startup.py, chat.py, 4 deletes, requirements.txt | -800 to -1200 |
| B. /models caching | models_catalog_db.py, catalog.py, model_catalog_cache.py | ~+50, ~-30 |
| C. Health cron | health-service/main.py, .github/workflows/health-monitor.yml, railway.toml | ~+80, ~-50 |
| D. Log volume | logging_config.py, startup.py | ~+15 |
| E. Rate limiter | auth_rate_limiting.py | ~+20, ~-60 |
| F. Cold start | main.py, openrouter_client.py, api/index.py | ~+10 |
| G. Reqs prune | requirements.txt, pyproject.toml | ~-10 |

## Testing strategy

Each category is independently verifiable:

- **A**: `pytest tests/` must pass. `grep -r "braintrust\|pyroscope" src/` returns 0 hits (except deleted-file allowlist).
- **B**: existing catalog tests pass; manual `curl /models` returns same JSON shape; second call < 50ms (cache hit).
- **C**: `python health-service/main.py --once` exits cleanly; cron file lints.
- **D**: `LOG_LEVEL=WARNING ENVIRONMENT=production python -c "from src.config.logging_config import configure_logging; configure_logging()"` → root logger at WARNING.
- **E**: `pytest tests/services/test_auth_rate_limiting.py` passes; no `deque` import remains in `auth_rate_limiting.py`.
- **F**: app boots locally; `time python -c "from src.main import create_app; create_app()"` baseline measured.
- **G**: `pip install -r requirements.txt` size compared before/after.

Existing test suites:
- `pytest` (all)
- `pytest tests/conceptual_model/` (spec compliance)

## Error handling

- All deleted services replaced with no-op shims where called externally to avoid `ImportError` in production until the deploy completes. Specifically: keep `braintrust_service.py` as a stub returning `False` from `is_available()` for one release cycle, then delete. **Decision: delete immediately**; CI catches any remaining import.
- Redis cache misses fall through to Supabase (existing behavior).

## Rollout

Single PR, single deploy. All changes are subtractive or behind existing env flags. Rollback = revert PR.

## Risks

| Risk | Mitigation |
|------|-----------|
| Braintrust span hides a real dependency in chat.py | grep for `braintrust_logger`, `span.log`; ensure no business logic lives inside `with span:` blocks |
| Health monitor cron doesn't fire on Railway | Also wire GitHub Actions `schedule:` as backup |
| `select("*")` removal misses a needed column | Run conceptual_model tests + manual `/models` smoke |
| Vercel still bundles heavy SDKs from `[providers]` extra | Inspect `vercel.json` and deploy logs after first deploy |

## Deliverables

1. Single PR titled `feat: cost reduction — observability, caching, lean health`.
2. Updated `CLAUDE.md` "Critical Modules" section if files deleted.
3. Short cost-impact note in PR description: estimated $/mo saved per category.
