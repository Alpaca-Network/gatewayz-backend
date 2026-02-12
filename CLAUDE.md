# Gatewayz Universal Inference API - Context

## Overview

**Gatewayz v2.0.4** - Enterprise FastAPI gateway providing unified access to 100+ AI models from 30+ providers (OpenRouter, Portkey, Featherless, Chutes, DeepInfra, Fireworks, Together, HuggingFace, Google Vertex, Groq, Cerebras, Cloudflare Workers, etc).

**Core Features**: OpenAI/Anthropic API compatibility, multi-provider routing, credit-based billing, encrypted API keys, IP allowlists, audit logging, chat history, image generation, trials, subscriptions, referrals, Prometheus/Grafana/Sentry/Arize observability, OpenTelemetry tracing, rate limiting, health monitoring, provider failover.

**Stack**: FastAPI 0.104.1, Python 3.10-3.12, Supabase (PostgreSQL), Redis, Stripe, Resend, OpenTelemetry, Prometheus.

---

## Architecture

**Layered Design**: Middleware → Routes (43) → Services (95) → Database (24) → Supabase/Redis/External Providers

**Flow**: Request → Auth/Rate Limit Middleware → Route Handler → Service (business logic, provider routing, pricing) → DB Layer (Supabase) → Response

**Key Principles**:
- Modularity (strict layer separation)
- Async/await throughout
- Provider abstraction (30 client modules)
- Security (Fernet encryption, HMAC, RBAC)
- Scalability (Redis caching, connection pooling)

---

## Directory Structure

```
src/                           # 85,080 LOC Python
├── main.py                   # FastAPI app factory
├── config/                   # 8 files: config, db, redis, supabase, arize, logging, opentelemetry
├── middleware/               # 6 files: sentry, observability, timeout, security, gzip, trace
├── db/                       # 24 modules: users, api_keys, chat_history, payments, plans, trials,
│                             # coupons, referral, activity, rate_limits, roles, ranking, credits, etc
├── routes/                   # 43 endpoints: chat, messages, images, catalog, health, ping, auth,
│                             # users, api_keys, admin, payments, plans, analytics, monitoring, etc
├── services/                 # 95 modules organized by function:
│   ├── *_client.py          # 30 provider clients (openrouter, featherless, chutes, etc)
│   ├── models.py, providers.py, pricing.py, rate_limiting.py  # Core services
│   ├── *_monitor.py         # 7 health monitoring services
│   ├── *_cache.py           # 6 caching services
│   ├── prometheus_*, grafana_*, metrics_*  # 12 observability services
│   └── trial_*, referral.py, payments.py, notification.py  # 23 feature/utility services
├── schemas/                  # 15 Pydantic models
├── security/                 # security.py (encryption/HMAC), deps.py (auth dependencies)
├── models/                   # health_models.py, image_models.py
└── utils/                    # 15 utilities: validators, auto_sentry, crypto, retry, etc

tests/                        # 228 tests in 13 directories (unit, integration, e2e, health, smoke, etc)
docs/                         # 121 files (architecture, api, setup, deployment, integrations)
supabase/migrations/          # 36 SQL migrations
scripts/                      # checks, database, integration-tests, utilities
api/index.py                  # Vercel serverless entry
.github/workflows/            # 9 CI/CD workflows
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

**Monitoring**: `routes/{health.py, system.py, metrics.py, grafana_metrics.py, model_health.py}`,
`services/{intelligent_health_monitor.py, autonomous_monitor.py}`

---

## Key Tables (20+)

users, api_keys, payments, plans, chat_history, coupons, referrals, trials, credit_transactions, rate_limits, roles, activity, ranking, gateway_analytics, ping, feedback, model_health, models_catalog, providers, subscription_products, webhook_events, failover

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

**Performance**: Redis multi-layer caching, connection pooling, request prioritization, selective GZip, async I/O, multi-provider load balancing, Prometheus metrics, OpenTelemetry tracing, query timeouts

**Security**: Fernet (AES-128) encryption, HMAC-SHA256 hashing, API key auth, RBAC, audit logging, IP allowlists, domain restrictions, per-user/key/system rate limits

---

## Recent Updates (2025-02-11)

- Three-layer rate limiting architecture (#1091)
- Security middleware with velocity mode
- IP rate limits increased (60→300 RPM, 10→60 RPM)
- Velocity thresholds adjusted (10%→25%, 10min→3min)
- Authenticated user exemption from IP limits
- Improved error classification (exclude 4xx)
- Comprehensive rate limit headers
- Enhanced Prometheus metrics
- 30 provider integrations (up from 17)

---

## Quick Reference

| Component | Location | Count |
|-----------|----------|-------|
| Routes | `src/routes/` | 43 |
| Services | `src/services/` | 95 |
| DB Modules | `src/db/` | 24 |
| Schemas | `src/schemas/` | 15 |
| Config | `src/config/` | 8 |
| Middleware | `src/middleware/` | 6 |
| Utils | `src/utils/` | 15 |
| Tests | `tests/` | 228 |
| Migrations | `supabase/migrations/` | 36 |
| **Total Code** | `src/` | **85,080 LOC** |

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
7. **Multi-Provider**: Consider impact across all 30 providers
8. **Rate Limiting**: Account for Redis availability + fallback
9. **Performance**: Use async/await; leverage caching; monitor pools
10. **Observability**: Add Prometheus metrics, OpenTelemetry traces, Sentry tracking
11. **Health**: Consider health check impacts for new providers/services
12. **Docs**: Update docs for major features

**Key Docs**: `docs/{architecture.md, api.md, setup.md, DEPLOYMENT.md, RATE_LIMITING.md}`

**Health Check**: `curl https://api.gatewayz.ai/health`

---

**Version**: 2.0.4 | **Updated**: 2025-02-11
