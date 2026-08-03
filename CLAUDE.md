# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Gatewayz Universal Inference API - Context

## Overview

**Gatewayz v2.0.4** - Enterprise FastAPI gateway providing unified access to 100+ AI models from 17 providers (OpenAI, Anthropic, Google Vertex, xAI, DeepSeek, Alibaba/Qwen, Moonshot/Kimi, MiniMax, Xiaomi, Z.ai, DeepInfra, Novita, Together, Fireworks, Groq, Cerebras, Featherless), with OpenRouter kept as an aggregator-of-last-resort fallback, never primary supply.

**Core Features**: OpenAI/Anthropic API compatibility, multi-provider routing, credit-based billing, encrypted API keys, IP allowlists, audit logging, chat history, subscriptions, Prometheus/Sentry observability, rate limiting, health monitoring, provider failover.

**Stack**: FastAPI 0.104.1, Python 3.10-3.12, Supabase (PostgreSQL), Redis, Stripe, Resend, Prometheus.

---

## Architecture

**Layered Design**: Middleware → Routes (43) → Services (42 + providers/) → Database (30) → Supabase/Redis/External Providers

**Flow**: Request → Auth/Rate Limit Middleware → Route Handler → Service (business logic, provider routing, pricing) → DB Layer (Supabase) → Response

**Key Principles**:
- Modularity (strict layer separation)
- Async/await throughout
- Provider abstraction (bespoke clients + shared OpenAI-compatible adapter)
- Security (Fernet encryption, HMAC, RBAC)
- Scalability (Redis caching, connection pooling)

---

## Directory Structure

```
src/                           # Python source
├── main.py                   # FastAPI app factory
├── config/                   # 5 files: config, redis, supabase, logging, usage_limits
├── middleware/               # 8 files: auto_sentry, concurrency, observability (Prometheus
│                             # request metrics), request_id, timeout, security, gzip, staging
├── db/                       # 30 modules: users, api_keys, chat_history, payments, plans,
│                             # activity, rate_limits, roles, ranking, credits, etc
├── routes/                   # 43 endpoints: chat, messages, catalog, health, auth, users,
│                             # api_keys, admin, payments, plans, monitoring, etc
├── services/                 # 42 top-level modules + services/providers/ (client adapters):
│   ├── providers/            # bespoke clients (openai, anthropic, google_vertex, xai,
│   │                         # openrouter, cerebras, featherless, novita, alibaba) +
│   │                         # openai_compat.py adapter with adapter_configs.py driving
│   │                         # deepinfra/together/fireworks/groq/zai/deepseek/moonshot/
│   │                         # minimax/xiaomi off one shared trio
│   ├── models.py, providers.py, pricing.py, rate_limiting.py  # Core services
│   ├── *_monitor.py         # health monitoring services
│   ├── *_cache.py           # caching services
│   └── payments.py, model_catalog_sync.py, scheduled_sync.py, price_refresh.py, etc
├── schemas/                  # 14 Pydantic models
├── security/                 # security.py (encryption/HMAC), deps.py (auth dependencies)
├── models/                   # health_models.py
└── utils/                    # 25 utilities: validators, crypto, retry, errors, etc

tests/                        # 187+ test files across unit, integration, e2e, health, smoke, etc
├── conceptual_model/         # tests verifying code matches Conceptual Model spec (see README.md inside)
docs/                         # architecture, api, setup, deployment, integrations
supabase/migrations/          # SQL migrations (+ supabase/staged-migrations/ for human-gated drops/seeds)
scripts/                      # checks, database, integration-tests, utilities
api/index.py                  # Vercel serverless entry
.github/workflows/            # CI/CD workflows
```

---

## Critical Modules by Function

**Auth & Security**: `security/{security.py, deps.py}`, `db/api_keys.py`, `routes/auth.py`

**Model Routing**: `services/{models.py, model_transformations.py, model_availability.py}`, `routes/catalog.py`

**Chat/Inference**: `routes/{chat.py, messages.py}`, `services/openrouter_client.py`, `services/provider_failover.py`

**Credits**: `db/credit_transactions.py`, `services/{pricing.py, pricing_lookup.py}`, `routes/users.py`

**Rate Limiting (3 layers)**:
- Layer 1: `middleware/security_middleware.py` (IP + behavioral + velocity mode)
- Layer 2: `services/rate_limiting.py` (API key, Redis-based)
- Layer 3: `services/anonymous_rate_limiter.py` (anonymous users)
- Fallback: `services/rate_limiting_fallback.py`, Config: `db/rate_limits.py`

**Database**: `config/supabase_config.py`, `config/config.py` (30+ env vars), `config/redis_config.py`

**Monitoring**: `routes/{health.py, system.py, status_page.py, model_health.py, monitoring.py}`,
`services/{intelligent_health_monitor.py, autonomous_monitor.py}`

---

## Key Tables (20+)

users, api_keys, payments, plans, chat_history, shared_chats, user_memory, credit_transactions, rate_limits, roles, activity, ranking, gateway_analytics, feedback, model_health, models_catalog, providers, subscription_products, webhook_events, failover, velocity_mode_events

---

## Entry Points

**Dev**: `src/main.py` → `create_app()` → `python src/main.py` or `uvicorn src.main:app --reload` (port 8000)

**Vercel**: `api/index.py` (serverless)

**Railway/Docker**: `start.sh` (container)

---

## Common Tasks

**Start Dev Server**:
```bash
python src/main.py  # or uvicorn src.main:app --reload
```

**Add Route**: Create in `src/routes/`, define schemas in `src/schemas/`, import in `src/main.py`

**Add Provider**:
1. Create `src/services/new_provider_client.py`
2. Register in `src/services/providers.py`
3. Add pricing to pricing config
4. Add mappings to `src/services/model_transformations.py`

**DB Changes**: Create migration in `supabase/migrations/`, apply via CLI, update `src/db/` module

**Tests**: `pytest` (all), `pytest --cov=src` (coverage), `pytest tests/integration/` (specific)

**Conceptual Model Tests**: `pytest tests/conceptual_model/ -v` (186 tests verifying code matches the spec).
See `tests/conceptual_model/README.md` for details. Uses `@pytest.mark.cm_verified` (should pass) and `@pytest.mark.cm_gap` (expected to fail — documents delta). Runs automatically on PRs via `conceptual-model-tests.yml` workflow.

---

## Key Design Patterns

1. **Dependency Injection** (FastAPI auth/logging)
2. **Async/Await** (all I/O)
3. **Service Layer** (isolated business logic)
4. **Factory Pattern** (`create_app()`)
5. **Encryption at Rest** (Fernet for API keys)
6. **Rate Limiting** (Redis + fallback)
7. **Multi-Provider** (abstract interfaces)
8. **Middleware Pipeline** (cross-cutting concerns)
9. **Registry Pattern** (canonical model registry)
10. **Health Checks** (intelligent + passive + autonomous)

---

## Performance & Security

**Performance**: Redis multi-layer caching, connection pooling, request prioritization, selective GZip, async I/O, multi-provider load balancing, Prometheus metrics, query timeouts

**Security**: Fernet (AES-128) encryption, HMAC-SHA256 hashing, API key auth, RBAC, audit logging, IP allowlists, domain restrictions, per-user/key/system rate limits

---

## Quick Reference

| Component | Location | Count |
|-----------|----------|-------|
| Routes | `src/routes/` | 43 |
| Services | `src/services/` (top-level) | 42 |
| Provider clients/adapters | `src/services/providers/` | 26 |
| DB Modules | `src/db/` | 30 |
| Schemas | `src/schemas/` | 14 |
| Config | `src/config/` | 5 |
| Middleware | `src/middleware/` | 8 |
| Utils | `src/utils/` | 25 |
| Test Files | `tests/` | 187 |
| Migrations | `supabase/migrations/` | 145 |

---

## Adding a New Gateway

1. Add to `GATEWAY_REGISTRY` in `src/routes/catalog.py`:
```python
"new-gateway": {
    "name": "New Gateway",
    "color": "bg-purple-500",
    "priority": "slow",
    "site_url": "https://newgateway.com",
},
```

2. Ensure models include `source_gateway` and `provider_slug` fields:
```python
{
    "id": "provider/model-name",
    "name": "Model Display Name",
    "source_gateway": "new-gateway",
    "provider_slug": "new-gateway",
    "context_length": 8192,
}
```

Frontend auto-discovers from `GET /gateways` endpoint.

---

## Notes for Claude

**When working on this codebase**:

1. **Flow**: middleware → routes → services → database
2. **Patterns**: Follow existing patterns (provider clients, service layers)
3. **Security**: Encrypt sensitive data; add audit logs
4. **DB**: Schema changes require migrations in `supabase/migrations/`
5. **Tests**: Add tests (follow existing structure by test type)
6. **Config**: Use env vars via `src/config/config.py`
7. **Multi-Provider**: Consider impact across all 17 providers
8. **Rate Limiting**: Account for Redis availability + fallback
9. **Performance**: Use async/await; leverage caching; monitor pools
10. **Observability**: Add Prometheus metrics, Sentry tracking
11. **Health**: Consider health check impacts for new providers/services
12. **Docs**: Update docs for major features

**Key Docs**: `docs/{architecture.md, api.md, setup.md, DEPLOYMENT.md, RATE_LIMITING.md}`

**Health Check**: `curl https://api.gatewayz.ai/health`

---

**Version**: 2.0.4

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **gatewayz-backend** (27323 symbols, 58322 relationships, 212 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/gatewayz-backend/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/gatewayz-backend/context` | Codebase overview, check index freshness |
| `gitnexus://repo/gatewayz-backend/clusters` | All functional areas |
| `gitnexus://repo/gatewayz-backend/processes` | All execution flows |
| `gitnexus://repo/gatewayz-backend/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
