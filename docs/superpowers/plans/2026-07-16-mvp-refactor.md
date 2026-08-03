# MVP Refactor Implementation Plan (North Star Alignment)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shrink gatewayz-backend from ~161k to ~75k LOC by deleting everything off-track vs `docs/NORTH_STAR.md`, consolidating duplicate engines, and closing the 4 Tier-2 provider gaps — without ever breaking the live data plane.

**Architecture:** Pure-subtraction phases first (hygiene → dead code → feature subsystems → provider purge → observability), then consolidation (one adapter, one router, one sync engine, one error module), then additive Tier-2 clients on the new adapter. Every task ends with the full test suite green and a commit; the app must boot after every task.

**Tech Stack:** FastAPI, Supabase (PostgreSQL), Redis, pytest. Work happens in a git worktree on branch `refactor/mvp-north-star`.

## Global Constraints

- **Worktree only** — never branch in the shared checkout: `git worktree add ../gatewayz-mvp-refactor -b refactor/mvp-north-star` and do ALL work there.
- **Data plane is sacred**: after every task, `POST /v1/chat/completions` tests must pass and `python -c "from src.main import create_app; create_app()"` must succeed.
- **Test command** (local venv lacks CI plugins): `pytest -o addopts="" -q` for full runs; `pytest -o addopts="" tests/<dir> -q` for targeted runs.
- **One commit per task**, message prefix `refactor(mvp):`. End every commit body with the standard co-author trailer.
- **DB migrations that DROP tables are written and committed but applied to STAGING ONLY** (`nzsgoitgrndfxziyrgte`). Production apply is a separate, explicitly human-gated step — never automated in this plan.
- **Deregistration before deletion**: a route file is removed from `src/main.py` route lists in the same commit that deletes it — the app must never import a deleted module.
- **Frontend-contract gate**: before deleting any route file, grep the frontend repo if present (`../gatewayz-frontend` or `../frontend`); if absent, check the last 7 days of access logs/Sentry for traffic on its paths. Record the result in the commit message. If live traffic exists → stop, flag to user.

## Decisions baked in (defaults, amendable via `north-star:` PR)

| # | Decision | Call |
|---|---|---|
| D1 | Plans/subscriptions | **KEEP** (`db/plans.py`, `routes/plans.py` restored to KEEP, `subscription_products.py`) — live prod billing/tier dependency; slimming deferred post-MVP |
| D2 | Prompt/code auto-router cluster (9 files ~3.2k) | **CUT** — second router engine violates §5 "one router"; `smart_router` is canonical |
| D3 | IP/bot fingerprinting | **TRIM** — keep key-auth + rate-limit path in `security_middleware`; delete fingerprinting/bot-tier machinery + `datacenter_ips` + `ip_classification` |
| D4 | Status surface | **KEEP** `status_page.py`, `model_health.py`; **CUT** `health_timeline.py`, `downtime_logs.py`, `detailed_status.py`, `error_monitor.py`, `diagnostics.py`, `optimization_monitor.py` |
| D5 | Non-roster providers | **KEEP** `zai_client.py` (key in hand, GLM demand) + `featherless_client.py` (Tier 4); **CUT** `cloudflare_workers_ai_client.py`, `nebius_client.py` |

---

## Phase 0 — Repo hygiene (zero code risk)

### Task 1: Purge tracked junk + gitignore

**Files:**
- Delete: `scratchpad_server.log`, `scratchpad_resync.log`, `scratchpad_resync2.log`, `scratchpad_stripe_listen.log`, `scratchpad_trigger.log`, `test_output.log`, `node_modules/`, `tmp/`, `gatewayz/` (empty dir), `Dockerfile.apidog`, `.github-issue-*.md` (4 files), `scripts/create-user-package.tar.gz`, all `* 2.sh` / `* 2.md` Finder-duplicate files, committed `__pycache__/` dirs, `scripts/benchmarks/benchmark_results/`
- Modify: `.gitignore` (append `*.log`, `node_modules/`, `tmp/`, `__pycache__/`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`, `.idea/`, `scratchpad_*`)
- Decide `package.json`/`package-lock.json`: keep ONLY if a deploy step runs `npm` (check `vercel.json`, `railway.*`, `nixpacks.toml`, `start.sh`); otherwise delete both.

- [ ] **Step 1:** `git rm -r --cached node_modules tmp 2>/dev/null; git rm scratchpad_*.log test_output.log Dockerfile.apidog .github-issue-*.md` + remaining list above; `find . -name "* 2.*" -not -path "./node_modules/*" -exec git rm {} +`
- [ ] **Step 2:** Append the `.gitignore` lines above.
- [ ] **Step 3:** `grep -rn "apidog\|package.json" vercel.json railway.json railway.toml nixpacks.toml start.sh .github/workflows/` → delete `package*.json` + `docs/APIDOG_RUNNER_*` if unreferenced.
- [ ] **Step 4:** `pytest -o addopts="" -q` → all pass (nothing imported changed). Expected: same pass count as baseline (record baseline first: `pytest -o addopts="" -q | tail -1`).
- [ ] **Step 5:** Commit `refactor(mvp): purge tracked logs, node_modules, and root junk`.

### Task 2: Archive stale root/docs reports + workflow cleanup

**Files:**
- Move to `docs/archive/`: root `AUDIT_SUMMARY.txt`, `CACHE_FLOW_AUDIT_SUMMARY.md`, `CACHE_FLOW_DIAGRAM.md`, `EXECUTIVE_BRIEFING.md`, `PAGINATION_AUDIT_SUMMARY.md`, `PAGINATION_QUICK_REF.md`, `HARDCODED_MODELS_AUDIT.md`, `HARDCODED_LOCATIONS_REFERENCE.md`, `SEPARATE_CONNECTION_POOLS.md`, `TRANSIENT_FAILURE_IMPROVEMENTS.md`; `docs/fixes/` (all 27); docs root dated audits (`PRICING_PRODUCTION_AUDIT_2026-01-22.md`, `PRICING_TEST_REPORT_2026-01-26.md`, `QA_COMPREHENSIVE_AUDIT_REPORT.md`, `CM_UNIT_TEST_COVERAGE_REPORT.md`, `COHORT_ANALYSIS_SUMMARY.md`, `v1_models_audit_report.md`, `PHASE_*_COMPLETION.md`, `*_SUMMARY.md`, `*_PROGRESS.md`)
- Delete: `.github/workflows/*.disabled` (3 deploy variants + `model-sync.yml.disabled` if `scheduled-sync.yml` covers it — verify by diffing their cron/job content), consolidate loose root postman files into `postman/`.

- [ ] **Step 1:** `mkdir -p docs/archive && git mv <each file> docs/archive/`
- [ ] **Step 2:** `git rm .github/workflows/deploy.yml.disabled .github/workflows/deploy-manual.yml.disabled .github/workflows/deploy-railway-cli.yml.disabled` (+ model-sync if superseded); `git mv Gatewayz_*.postman_*.json postman/`
- [ ] **Step 3:** `pytest -o addopts="" -q` → green. Commit `refactor(mvp): archive stale reports, drop disabled workflows`.

---

## Phase 1 — Dead code (zero-reference, verified deletes)

### Task 3: Delete 15 verified orphans

**Files (Delete):**
- `src/utils/provider_safety.py`, `src/utils/auto_sentry.py`, `src/utils/model_suggestions.py`, `src/utils/safe_logger.py`, `src/utils/release_tracking.py`, `src/utils/reset_welcome_emails.py`, `src/utils/profiling.py`
- `src/schemas/code_routing.py`
- `src/routes/ops_dashboard.py` (not registered in main.py)
- `src/db/referral.py` (dead SQLAlchemy module)
- `src/services/professional_email_templates.py`, `src/services/bug_fix_generator.py`, `src/services/bug_fix_generator_old.py`, `src/services/canonical_registry.py`, `src/services/model_catalog_validator.py`, `src/services/google_oauth2_jwt.py`, `src/services/notification_triggers.py`
- Their dedicated test files (find via `grep -rl "<module_name>" tests/`)
- `src/utils/profiling.py` call sites: replace `@profile`/profiling imports at its ~10 call sites with plain code (grep `from src.utils.profiling` / `import profiling`).

**Interfaces:** Produces nothing; consumers must remain zero.

- [ ] **Step 1:** For EACH file, re-verify orphanhood now: `grep -rn "<module_name>" src/ --include="*.py" | grep -v "src/<its own path>"` → must return only profiling call sites (handled) or nothing. Any hit = leave that file, note it, continue.
- [ ] **Step 2:** Remove profiling decorators/imports at call sites (mechanical edit, keep function bodies).
- [ ] **Step 3:** `git rm` the verified list + their tests.
- [ ] **Step 4:** `python -c "from src.main import create_app; create_app()"` then `pytest -o addopts="" -q` → green.
- [ ] **Step 5:** Commit `refactor(mvp): delete 15 zero-reference orphan modules (~5.5k LOC)`.

---

## Phase 2 — Feature subsystem cuts (one task per subsystem; identical step template)

**Step template for Tasks 4–9** (referenced as "CUT-TEMPLATE"):
1. Frontend-contract gate (Global Constraints) for each route path.
2. Remove route entries from `src/main.py` (`v1_routes_to_load` / `non_v1_routes_to_load`).
3. `git rm` listed files + their tests.
4. `grep -rn "<deleted module names>" src/` → fix any lingering importers by deleting the importing dead branch (typical: `credit_handler` trial hook, `context_assembly_bridge` in chat path — remove the call site, keep the caller).
5. Write staging-only migration `supabase/migrations/<ts>_drop_<feature>.sql` with `DROP TABLE IF EXISTS <tables> CASCADE;` — commit, do NOT apply to prod.
6. Boot check + `pytest -o addopts="" -q` → green. Commit.

### Task 4: Cut trials (incl. partner trials)
**Delete:** `src/routes/trial_analytics.py`, `src/routes/partner_trials.py`, `src/db/trials.py`, `src/schemas/trials.py`, `src/schemas/trial_analytics.py`; trial hooks in `src/handlers/credit_handler.py` and `src/routes/admin.py` (grep `trial`); tests `tests/schemas/test_trials.py`, `tests/conceptual_model/test_cm07_plans_trials.py` (trial halves only — keep plan tests per D1), `docs/TRIAL_ANALYTICS_HANDOFF.md`.
**Tables:** `trial_config`, `trial_grants`, `trial_conversion_metrics`, `partner_trials`, `partner_trial_analytics`.
- [ ] Apply CUT-TEMPLATE. Commit `refactor(mvp): remove trials subsystem`.

### Task 5: Cut coupons + referrals
**Delete:** `src/routes/coupons.py`, `src/routes/referral.py`, `src/db/coupons.py`, `src/services/referral.py`, `src/schemas/coupons.py` (restore KEEP only if payments imports it — grep first; if `routes/payments.py` imports coupon schemas, delete the coupon-redemption endpoints there too), referral hooks in `routes/auth.py` + `routes/payments.py` webhook (grep `referral`); tests: 9 referral + 4 coupon files listed in audit; `docs/features/REFERRAL_SYSTEM.md`.
**Tables:** `coupons`, `coupon_redemptions`, `referrals`.
- [ ] Apply CUT-TEMPLATE. Commit `refactor(mvp): remove coupons and referrals`.

### Task 6: Cut chat-app statefulness (history/share/feedback/memory/context)
**Delete:** `src/routes/chat_history.py`, `src/routes/share.py`, `src/routes/user_memory.py`, `src/routes/chat_context.py`, `src/db/chat_history.py`, `src/db/shared_chats.py`, `src/db/feedback.py`, `src/db/user_memory.py`, `src/services/context_assembly.py`, `src/services/context_assembly_bridge.py`, `src/services/memory_extraction.py`, `src/schemas/share.py`; hot-path hook: remove context-assembly call from `src/routes/chat.py`/`chat_request.py` (grep `context_assembly`); 6+ chat-history test files, `tests/db/test_chat_history.py` etc.
**Tables:** `chat_messages`, `chat_sessions`, `shared_chats`, `message_feedback`, `user_memory`.
**⚠ Highest frontend risk of the plan — the chat UI may persist history. The frontend gate is mandatory; if traffic exists, park this task and continue with Task 7.**
- [ ] Apply CUT-TEMPLATE. Commit `refactor(mvp): remove chat-app state (history/share/feedback/memory)`.

### Task 7: Cut wrong-modality endpoints (audio/images/TTS/tools) + GPU marketplace
**Delete:** `src/routes/audio.py`, `src/routes/images.py`, `src/routes/tools.py`, `src/routes/nosana.py`, `src/services/providers/fal_image_client.py`, `src/services/providers/image_generation_client.py`, `src/services/providers/chatterbox_tts_client.py`, `src/services/providers/nosana_client.py`, `src/models/image_models.py`; registry entries for these in `src/handlers/provider_registry.py` + `PROVIDER_FETCH_FUNCTIONS` in `src/services/model_catalog_sync.py`; matching tests + `scripts/integration-tests/*nosana*`.
- [ ] Apply CUT-TEMPLATE (no tables). Commit `refactor(mvp): remove audio/image/TTS/tools and Nosana GPU marketplace`.

### Task 8: Cut misc off-track routes
**Delete:** `src/routes/notifications.py`, `src/routes/activity.py` (route only — KEEP `db/activity.py` metering), `src/routes/analytics.py` (Statsig events), `src/routes/ping.py` + `src/db/ping.py` + `src/services/ping.py` (vanity counter; verify no uptime monitor pings it — check access logs), `src/models/notification_models.py`, `src/schemas/notification.py`, `src/services/notification_service.py`; per D4: `src/routes/health_timeline.py`, `src/routes/downtime_logs.py`, `src/routes/detailed_status.py`, `src/routes/error_monitor.py`, `src/routes/diagnostics.py`, `src/routes/optimization_monitor.py`, `src/schemas/health_timeline.py`; matching tests.
**Tables:** `ping_stats`, `notifications`, `notification_preferences`, `admin_notifications` (staging-only migration).
- [ ] Apply CUT-TEMPLATE. Commit `refactor(mvp): remove notifications, vanity ping, redundant status routes`.

### Task 9: Cut remaining off-track services
**Delete:** `src/services/query_classifier.py`, `src/services/region_service.py`, `src/services/region_router.py` (staged/not-wired), `src/middleware/region_middleware.py` ONLY if it imports region_service (grep; else keep the 43-line header shim), `src/services/failover_service.py` (unreachable once near_client dies in Task 10), `src/services/huggingface_hub_service.py`, `src/services/huggingface_models.py`, `src/services/endpoint_rate_limiter.py`; matching tests.
- [ ] Apply CUT-TEMPLATE (no tables). Commit `refactor(mvp): remove staged/redundant services`.

---

## Phase 3 — Provider client purge

### Task 10: Delete 16 non-roster provider clients

**Files (Delete):** in `src/services/providers/`: `aimo_client.py`, `near_client.py`, `morpheus_client.py`, `chutes_client.py`, `akash_client.py`, `sybil_client.py`, `canopywave_client.py`, `simplismart_client.py`, `clarifai_client.py`, `huggingface_client.py`, `cohere_client.py`, `code_router_client.py`, `alpaca_network_client.py`, `modelz_client.py`, `cloudflare_workers_ai_client.py` (D5), `nebius_client.py` (D5). (fal/image/tts/nosana already gone in Task 7.)
**Modify:** `src/handlers/provider_registry.py` (`PROVIDER_ROUTING`, `PROVIDER_FUNCTIONS`), `src/services/model_catalog_sync.py` (`PROVIDER_FETCH_FUNCTIONS` line ~69), `src/services/providers.py` + `providers/__init__.py` re-exports, `PROVIDER_ENV_VAR_MAP` (grep location), `anthropic_transformer.py` (drop alpaca_network import if present).
**Data:** staging-only SQL: `UPDATE providers SET is_active=false WHERE slug IN (<16 slugs>);` — models deactivate via existing stale-deprecation cron.
**Tests:** delete matching client tests + `scripts/integration-tests/test_{near,canopywave}_*`, `scripts/validation/*openrouter_auto*` (aggregator-primary scripts).

- [ ] **Step 1:** For each client, grep slug across `src/` and delete every registry/map entry in the same pass.
- [ ] **Step 2:** `git rm` files + tests + scripts.
- [ ] **Step 3:** Boot check; `pytest -o addopts="" -q` → green; run provider-registry tests explicitly: `pytest -o addopts="" tests/services -q`.
- [ ] **Step 4:** Commit `refactor(mvp): purge 16 non-roster provider clients (~5.5k LOC)`.

---

## Phase 4 — Observability teardown (keep: basic Prometheus metrics + basic Sentry)

### Task 11: Delete OTel/Traceloop/Arize/Loki/Grafana/Statsig/PostHog

**Files (Delete):**
- Config: `src/config/opentelemetry_config.py`, `src/config/traceloop_config.py`, `src/config/arize_config.py`
- Utils: `src/utils/ai_tracing.py`, `src/utils/bot_filter_span_processor.py`, `src/utils/resilient_span_processor.py`, `src/utils/sentry_insights.py`, `src/utils/step_logger.py`, `src/utils/db_instrumentation.py`
- Middleware: `src/middleware/trace_context_middleware.py`, `src/middleware/observability_middleware.py` (keep `auto_sentry_middleware.py` as the ONE error-capture mechanism)
- Services: `src/services/provider_span_enricher.py`, `src/services/downtime_log_capture.py`, `src/services/statsig_service.py`, `src/services/posthog_service.py`
- Routes: `src/routes/grafana_metrics.py`, `src/routes/prometheus_grafana.py`, `src/routes/prometheus_data.py`, `src/routes/instrumentation.py` (KEEP `src/routes/metrics.py` basic Prometheus if it exists — verify)
- Root: `dashboards/`, `prometheus/`, `prometheus.yml`, `prometheus-alerts.yml`, `tempo/`, `redis-exporter/`, `docker-compose.prometheus.yml`; docs `METRICS_*`, `GRAFANA_*`, `PROMETHEUS_*`, `TEMPO_*` → `docs/archive/`
**Modify:** `src/config/logging_config.py` (remove OTel span-id injection), `src/services/startup.py` (remove arize/traceloop/otel/statsig/posthog init blocks), `src/main.py` (middleware stack — remove the two deleted middlewares), `requirements.txt` (remove `opentelemetry-*`, `traceloop-sdk`, `arize-*`, `statsig`, `posthog` — grep exact names first).

- [ ] **Step 1:** Delete files; fix importers (`grep -rn "opentelemetry_config\|traceloop\|arize\|statsig\|posthog\|span_enricher\|downtime_log_capture" src/`).
- [ ] **Step 2:** Trim `startup.py`, `logging_config.py`, `main.py`, `requirements.txt`.
- [ ] **Step 3:** Boot check + full suite green; verify Sentry still initializes (`grep -n sentry_sdk src/main.py` and boot log line).
- [ ] **Step 4:** Commit `refactor(mvp): tear down heavy observability, keep Prometheus basics + Sentry (~5k LOC)`.

---

## Phase 5 — Consolidations

### Task 12: Config-driven OpenAI-compatible provider adapter

**Files:**
- Create: `src/services/providers/openai_compat.py` (adapter factory on `base.py`'s `ProviderAdapter` protocol)
- Create: `src/services/providers/adapter_configs.py` (config table)
- Test: `tests/services/providers/test_openai_compat.py`

**Interfaces:**
- Produces: `make_adapter(cfg: ProviderConfig) -> ProviderAdapter` where `ProviderConfig(slug: str, base_url: str, api_key_env: str, model_prefix: str | None = None, extra_headers: dict | None = None, quirks: Quirks | None = None)`; `ADAPTERS: dict[str, ProviderAdapter]` keyed by slug, each exposing `request(payload) -> httpx.Request`, `process(response) -> dict`, `stream(response) -> AsyncIterator[bytes]` (match the existing trio signatures in `provider_registry.PROVIDER_ROUTING` — read one client, e.g. `deepinfra_client.py`, and mirror exactly).
- Consumes: `src/services/providers/base.py` protocol.

- [ ] **Step 1:** Write failing test: build adapter for a fake config, assert `request()` sets base URL/auth header/model prefix, `process()` normalizes an OpenAI-shape response, `stream()` passes SSE chunks through untouched. Use respx/httpx mocking consistent with existing provider tests.
- [ ] **Step 2:** Run: `pytest -o addopts="" tests/services/providers/test_openai_compat.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement `openai_compat.py` (~150 LOC) by extracting the common trio from `deepinfra_client.py`; add `adapter_configs.py` entries for: deepinfra, novita, together, fireworks, groq, cerebras, xai, alibaba, zai, featherless.
- [ ] **Step 4:** Test passes. **Migrate one provider end-to-end first (deepinfra):** point its `PROVIDER_ROUTING`/`PROVIDER_FETCH_FUNCTIONS` entries at the adapter, run that provider's existing tests → green → `git rm src/services/providers/deepinfra_client.py`.
- [ ] **Step 5:** Repeat per provider (one commit each or one batch commit after all 10 green): novita, together, fireworks, groq, cerebras, xai, alibaba, zai, featherless. Keep custom: `anthropic_client.py` (+transformer), `google_vertex_*`, `openai_client.py` (native), `openrouter_client.py` (fallback multiplexer).
- [ ] **Step 6:** Full suite + boot. Commit `refactor(mvp): consolidate 10 OpenAI-compatible clients onto one adapter (~3.2k LOC)`.

### Task 13: Delete the second router (prompt/code auto-routing cluster, D2)

**Files (Delete):** `src/services/prompt_router.py`, `src/services/model_selector.py`, `src/services/code_router.py`, `src/services/prompt_classifier_rules.py`, `src/services/code_classifier.py`, `src/services/general_router.py`, `src/services/fallback_chain.py`, `src/services/capability_gating.py`, `src/services/general_router_fallback.py`, `src/routes/code_router.py`, `src/routes/general_router.py`, `src/schemas/general_router.py`; matching tests; `docs` references → archive.
**Modify:** `src/main.py` (deregister 2 routes); grep `prompt_router\|model_selector\|general_router\|fallback_chain\|capability_gating` in `chat_*.py` — if the chat path calls any (e.g. an `:auto` model alias), replace with a direct `smart_router_bridge` call preserving the public `model="auto"` behavior OR return 400 for `auto` (check whether any prod traffic uses `auto` first — access-log gate).

- [ ] **Step 1:** Traffic gate on `auto`/`:auto` model IDs.
- [ ] **Step 2:** Delete files, deregister, fix chat-path call sites.
- [ ] **Step 3:** Full suite + boot + `pytest -o addopts="" tests/routes -q`. Commit `refactor(mvp): remove duplicate prompt-router engine (~3.2k LOC)`.

### Task 14: One catalog-sync engine

**Files:** KEEP `src/services/model_catalog_sync.py` (canonical) + `src/services/scheduled_sync.py` (cron wrapper) + `src/services/price_refresh.py` (cheap hourly price cron — §4.2 pricing cadence). **Delete:** `src/services/incremental_sync.py`, `src/services/parallel_catalog_fetch.py`, `src/services/provider_model_sync_service.py`; matching tests.
**Modify:** `scheduled_sync.py` — if it imports a deleted engine, re-point to `model_catalog_sync`; `src/routes/model_sync.py` same.

- [ ] **Step 1:** `grep -rn "incremental_sync\|parallel_catalog_fetch\|provider_model_sync_service" src/` → re-point every caller to `model_catalog_sync` equivalents (functions: check names in both before deleting; the canonical entry is the sync function `scheduled_sync` already calls in prod — verify via `src/routes/model_sync.py` and cron workflow).
- [ ] **Step 2:** Delete, full suite, boot. Run a live staging sync smoke: `python -m scripts.database.<sync smoke script>` or hit `POST /admin/model-sync` locally against staging env.
- [ ] **Step 3:** Commit `refactor(mvp): single catalog-sync engine (~1.3k LOC)`.

### Task 15: Consolidate error cluster (2.8k → ~800)

**Files:**
- Create: `src/utils/errors.py` — merges: exception classes (from `exceptions.py`), provider-error → client-status mapping incl. 429≠502≠402 + no-raw-provider-body rule (from `error_factory.py`/`error_codes.py`), user-facing message table (trimmed, from `error_messages.py`).
- Keep: `src/utils/error_handlers.py` (FastAPI handler registration) — re-point imports to `errors.py`.
- Delete after migration: `src/utils/error_factory.py`, `src/utils/error_messages.py`, `src/utils/exceptions.py`, `src/utils/error_codes.py`.
- Test: keep ALL existing error-classification tests (they encode PRs #2144–#2147, #2160 regressions — do not weaken); re-point imports.

**Interfaces:** Produces `classify_provider_error(status:int, body:str, provider:str) -> GatewayError` and the existing exception class names re-exported verbatim (`grep -rn "from src.utils.exceptions import\|from src.utils.error_factory import" src/ | cut -d: -f3 | sort -u` = the required export list).

- [ ] **Step 1:** Build the export list (command above). Write `errors.py` re-exporting every name, implemented by moving code, not rewriting logic.
- [ ] **Step 2:** Repoint all importers via grep; delete the 4 old modules.
- [ ] **Step 3:** `pytest -o addopts="" tests/ -q -k "error"` then full suite → green. Commit `refactor(mvp): consolidate error handling into one module`.

### Task 16: Trim hot-path security (D3)

**Files:**
- Modify: `src/middleware/security_middleware.py` — keep: API-key presence fast-fail, Redis rate-limit call, velocity-mode hook (fraud guardrail per §2); delete: IP fingerprinting, bot tiering, datacenter classification branches.
- Delete: `src/config/datacenter_ips.py`, `src/services/ip_classification.py`; matching tests.
- Keep: `src/services/velocity_mode_events` wiring (db) — billing guardrail.
- Modify: `src/utils/security_validators.py` — delete validators with zero call sites (grep each public function name).

- [ ] **Step 1:** Map every function in `security_middleware.py` to keep/delete; delete + fix.
- [ ] **Step 2:** Delete the 2 support modules; prune dead validators.
- [ ] **Step 3:** Full suite + boot; run `pytest -o addopts="" tests/security -q`. Commit `refactor(mvp): slim hot-path security to auth+rate-limit+velocity (~1.8k LOC)`.

### Task 17: Merge db metering extension + misc dedup

**Files:** Merge `src/db/chat_completion_requests_enhanced.py` (346) into `src/db/chat_completion_requests.py` (move functions, repoint importers, delete). Delete `src/services/pricing_normalization.py` (3-line shim) → repoint to `src/utils/pricing_normalization.py`. Resolve `src/config/db_config.py` vs `supabase_config.py`: grep importers of each; if `db_config` importers ≤2, fold into `supabase_config.py` and delete.
- [ ] Steps: grep → move → repoint → delete → full suite → commit `refactor(mvp): merge db metering extension, drop shims`.

---

## Phase 6 — Close Tier-2 roster gaps (additive)

### Task 18: DeepSeek, Moonshot/Kimi, MiniMax, Xiaomi adapters

**Files:**
- Modify: `src/services/providers/adapter_configs.py` (4 new `ProviderConfig` entries), `src/handlers/provider_registry.py`, `src/services/model_catalog_sync.py` (`PROVIDER_FETCH_FUNCTIONS`), provider env map, `src/config/config.py` (env vars `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `MINIMAX_API_KEY`, `XIAOMI_API_KEY`)
- Test: `tests/services/providers/test_tier2_adapters.py`

Config values (verify each base URL against provider docs at implementation time — they change):
```python
ProviderConfig(slug="deepseek", base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY"),
ProviderConfig(slug="moonshot", base_url="https://api.moonshot.ai/v1", api_key_env="MOONSHOT_API_KEY"),
ProviderConfig(slug="minimax", base_url="https://api.minimax.io/v1", api_key_env="MINIMAX_API_KEY"),
ProviderConfig(slug="xiaomi", base_url="<verify: Xiaomi MiMo open platform>", api_key_env="XIAOMI_API_KEY"),
```
- [ ] **Step 1:** Failing test: each slug resolves an adapter; `request()` targets the right base URL + auth header.
- [ ] **Step 2:** Add configs + registry entries; test green. If Xiaomi has no public serverless endpoint yet, add the other 3 and record Xiaomi as a gap in the commit message — do not fake it.
- [ ] **Step 3:** DB: insert provider rows on STAGING (`providers` table: slug, name, failover_priority) — reuse the pattern from `scripts/database/` provider-seed scripts. Run staging catalog sync; verify models appear priced (staging pricing gap is a known issue — record counts, don't block).
- [ ] **Step 4:** Live smoke per provider (needs API keys in `.env` — keys exist for none of these yet; if absent, mark task done-code-only and list keys needed). Commit `feat(providers): Tier-2 adapters — DeepSeek, Kimi, MiniMax(, Xiaomi)`.

---

## Phase 7 — Verification + docs truth

### Task 19: Full verification sweep

- [ ] **Step 1:** `pytest -o addopts="" -q` full suite → record pass/fail vs baseline; zero new failures.
- [ ] **Step 2:** Boot server against staging env; run e2e smoke: free-model chat completion (streaming + non-streaming), a BYOK call, a failover simulation (disable top provider in staging `providers` table, confirm chain advances), Stripe webhook test event.
- [ ] **Step 3:** `git grep -l "trial\|coupon\|referral" src/ | grep -v test` → expect empty (or justified hits like "trial" in unrelated words); same for deleted provider slugs.
- [ ] **Step 4:** LOC report: `find src -name '*.py' | xargs wc -l | tail -1` → target ≤ ~85k. Record in commit.
- [ ] **Step 5:** Commit `refactor(mvp): verification sweep`.

### Task 20: Update CLAUDE.md + North Star amendment

- [ ] **Step 1:** Update `CLAUDE.md` Quick Reference counts (routes/services/db/utils), remove references to deleted subsystems (trials, coupons, referrals, image gen, Arize, Grafana services), update provider count.
- [ ] **Step 2:** Append to `docs/NORTH_STAR.md` §3 an amendment note: Z.ai admitted (key + GLM demand), Cloudflare/Nebius rejected, D1–D5 decisions recorded with date.
- [ ] **Step 3:** Commit `north-star: record D1-D5 decisions; docs: refresh CLAUDE.md to post-refactor reality`.
- [ ] **Step 4:** Push branch, open PR titled `refactor: MVP North Star alignment (−~50% LOC)` with per-phase summary and the staging-only migration warning called out.

---

## Execution order & risk notes

- Tasks 1–3 are risk-free; 4–11 are independent of each other (any can be parked if a frontend gate fails); 12 must precede 18; 13–17 independent.
- **Prod DB migrations are NEVER auto-applied.** The drop-table migrations ride the PR; applying to prod is a human decision after the code deploy has soaked (deleted code stops writing to those tables immediately; tables can be dropped weeks later).
- Rollback story: every task is one commit; `git revert <sha>` restores any subsystem cleanly because deletion commits are self-contained (files + registration + tests together).
- Estimated result: src ~161k → ~78–85k LOC; providers 40 clients → 14 (4 custom + 10 adapter configs) + 3–4 Tier-2 configs; middleware 11 → 7; sync engines 5 → 1; routers 2 → 1; error modules 5 → 2.

## Deferred (explicitly post-MVP, not in this plan)

- Splitting oversized KEEP files (`catalog.py` 3.3k, `admin.py` 2.7k, `system.py` 2.3k, `health.py` 2.1k, `auth.py` 1.9k) — refactor, not deletion; separate plan.
- Full `scripts/` triage (122 loose root scripts) beyond the artifacts removed in Tasks 1–2.
- Slimming plans/subscriptions (D1) and the sentry-context/perf-tracker utils MAYBE cluster.
- Applying drop-table migrations to production (human-gated, weeks after deploy soak).

## Outcome

- LOC: `src/` ~161k → 123.8k (Task 19 measurement); adapter consolidation reduced scope to 5 consolidated + 4 bespoke clients + novita fetch-only (not the full 10→ adapter sweep originally scoped).
- Parked, not deleted (frontend-active, needs product decision): chat_history/share/feedback/user_memory/chat_context, audio, tools, activity routes; huggingface left as an open roster decision (client gone, catalog-fetch still live).
- Key deviations: T15 kept `DetailedErrorFactory` byte-identical rather than inventing a new `classify_provider_error` interface; drop/seed/deactivate migrations shipped to `supabase/staged-migrations/` (human-gated) instead of `supabase/migrations/` to avoid CI auto-push to production.
- Verification: ~2190 tests pass / 0 unexplained failures across the chunked suite; e2e smoke clean; app boots with 311 routes.
- Ops follow-ups outstanding: fund DeepSeek/Moonshot/MiniMax provider accounts, provision `XIAOMI_API_KEY`, add tier-2 slugs to `ENABLED_PROVIDERS`, and manually apply `supabase/staged-migrations/*.sql` per its README once each deploy has soaked.
