# GatewayZ Backend Architecture - 3-Layer Model

## Overview

The GatewayZ backend follows a **3-layer architecture** that separates concerns between routing, business logic, and data persistence:

```
┌─────────────────────────────────────────────────────────────────┐
│                        LAYER 1: ROUTES                          │
│                   (FastAPI Endpoints & Handlers)                │
├─────────────────────────────────────────────────────────────────┤
│  /v1/chat/completions  │  /api/health  │  /prometheus/metrics   │
│  /health/providers     │  /health/models  │  /v1/images/...    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                  ┌────────▼────────┐
                  │   Dependency    │
                  │   Injection &   │
                  │  Middleware     │
                  └────────┬────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                       LAYER 2: SERVICES                         │
│           (Business Logic, Orchestration, State)                │
├──────────────────────────────────────────────────────────────────┤
│  Provider Management  │  Model Inference  │  Health Monitoring   │
│  Rate Limiting        │  Authentication   │  Metrics Collection  │
│  Analytics            │  Cost Tracking    │  Cache Management    │
│  Circuit Breakers     │  Token Tracking   │  Anomaly Detection   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                ┌──────────┬──────────┬──────────┐
                │          │          │          │
     ┌──────────▼──┐  ┌───▼──────┐  │  ┌──────▼──┐
     │   In-Memory │  │  Redis   │  │  │Prometheus
     │   Cache     │  │  Cache   │  │  │ Registry
     └─────────────┘  └──────────┘  │  └──────────┘
                                    │
┌───────────────────────────────────▼──────────────────────────────┐
│                      LAYER 3: DBMS                               │
│            (Data Persistence, Queries, State)                    │
├────────────────────────────────────────────────────────────────── ┤
│  Supabase (PostgreSQL)  │  Redis  │  Monitoring Stack             │
│  ├─ Users               │  ├─ Sessions                            │
│  ├─ API Keys            │  ├─ Rate Limits                         │
│  ├─ Models              │  ├─ Cache Data                          │
│  ├─ Provider Config     │  ├─ Health Metrics                      │
│  ├─ Transactions        │  └─ Analytics State                     │
│  ├─ Chat History        │                                         │
│  └─ Audit Logs          │  Prometheus  │  Loki  │  Tempo         │
│                         │  (Metrics)   │ (Logs) │ (Traces)       │
└────────────────────────────────────────────────────────────────── ┘
```

---

## Layer 1: Routes (FastAPI Endpoints)

**Responsibility**: HTTP request handling, input validation, response formatting

### Key Route Files

| File | Purpose | Endpoints |
|------|---------|-----------|
| [src/routes/unified_chat.py](src/routes/unified_chat.py) | Chat completions | `POST /v1/chat/completions` |
| [src/routes/health.py](src/routes/health.py) | System health | `GET /health`, `/health/system`, `/health/providers`, `/health/models` |
| [src/routes/catalog.py](src/routes/catalog.py) | Model catalog | `GET /models`, `GET /v1/models` |
| [src/routes/monitoring.py](src/routes/monitoring.py) | Monitoring API | `GET /api/monitoring/*` |
| [src/routes/grafana_metrics.py](src/routes/grafana_metrics.py) | Grafana integration | `GET /api/metrics/prometheus`, `GET /api/metrics/summary` |
| [src/routes/auth.py](src/routes/auth.py) | Authentication | `POST /auth/login`, `POST /auth/register` |
| [src/routes/analytics.py](src/routes/analytics.py) | Analytics events | `POST /api/analytics/events` |
| [src/routes/system.py](src/routes/system.py) | System management | `GET /api/cache/stats`, `DELETE /api/cache/clear` |

### Route Loading Sequence

Routes are loaded dynamically in `src/main.py` during app startup:

```python
# Routes are loaded in this order (critical routes first):
v1_routes = [
    ("unified_chat", "Chat API"),
    ("images", "Image Generation"),
    ("catalog", "Model Catalog"),
]

non_v1_routes = [
    ("health", "Health Check"),           # ✅ Early load (healthcheck)
    ("availability", "Model Availability"),
    ("monitoring", "Monitoring API"),
    ("grafana_metrics", "Grafana Metrics"),
    ...
]

# Dynamic loading handles import errors gracefully:
# - Logs failures but continues
# - Routes marked as "critical" log extra details
```

---

## Layer 2: Services (Business Logic)

**Responsibility**: Core business logic, orchestration, state management, metrics

### Service Categories

#### A. **Provider & Model Management**

| Service | Purpose | Key Methods |
|---------|---------|-------------|
| [src/services/provider_registry.py](src/services/provider_registry.py) | Provider lifecycle | `get_provider()`, `list_providers()`, `health_check()` |
| [src/services/model_inference.py](src/services/model_inference.py) | Model API calls | `call_provider()`, `stream_response()` |
| [src/services/canonical_registry.py](src/services/canonical_registry.py) | Model naming | `resolve_model()`, `get_canonical_name()` |

#### B. **Monitoring & Observability** (Most Critical for Your Task)

| Service | Purpose | Key Metrics |
|---------|---------|-------------|
| [src/services/prometheus_metrics.py](src/services/prometheus_metrics.py) | Metric definitions | `fastapi_requests_total`, `model_inference_duration_seconds`, `provider_availability`, `provider_error_rate` |
| [src/services/prometheus_exporter.py](src/services/prometheus_exporter.py) | Metric export (future) | Custom format export |
| [src/services/grafana_metrics_service.py](src/services/grafana_metrics_service.py) | Grafana helpers | `format_metrics()`, `calculate_health_score()` |
| [src/services/provider_health_tracker.py](src/services/provider_health_tracker.py) | Background health monitoring | Background task every 30s |
| [src/services/analytics.py](src/services/analytics.py) | Event tracking | `track_inference()`, `track_cost()` |
| [src/services/model_availability.py](src/services/model_availability.py) | Model uptime tracking | `update_availability()` |

#### C. **Logging & Error Handling**

| Service | Purpose | Key Features |
|---------|---------|-------------|
| [src/config/logging_config.py](src/config/logging_config.py) | Loki logging (ASYNC) | `LokiLogHandler` with background worker thread |
| [src/middleware/auto_sentry_middleware.py](src/middleware/auto_sentry_middleware.py) | Error capture | Automatic Sentry integration |
| [src/utils/sentry_context.py](src/utils/sentry_context.py) | Sentry helper | `capture_error()` context manager |

#### D. **Caching & Performance**

| Service | Purpose | Implementation |
|---------|---------|-----------------|
| [src/services/auth_cache.py](src/services/auth_cache.py) | API key caching | In-memory + Redis fallback |
| [src/services/rate_limit_service.py](src/services/rate_limit_service.py) | Rate limiting | Redis-backed counters |

### Service Initialization Flow

```
app.startup() [lifespan in src/services/startup.py]
│
├─ Initialize Supabase connection
├─ Load provider registry
├─ Initialize metrics (Prometheus)
├─ Start background tasks:
│  ├─ Provider health tracker (every 30s)
│  ├─ Analytics aggregation (every minute)
│  └─ Anomaly detection (every 5 minutes)
├─ Initialize Loki async handler (non-blocking queue)
└─ Server ready to accept requests

app.shutdown()
│
└─ Flush Loki queue (graceful shutdown)
```

---

## Layer 3: DBMS (Data Persistence)

**Responsibility**: Data storage, retrieval, transactions

### Primary Database: Supabase (PostgreSQL)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `users` | User accounts | `id`, `email`, `created_at`, `credits` |
| `api_keys` | Authentication | `id`, `user_id`, `key_hash`, `active`, `rate_limit` |
| `models` | Model catalog | `id`, `name`, `provider`, `pricing`, `active` |
| `providers` | Provider config | `id`, `name`, `endpoint`, `credentials`, `enabled` |
| `provider_health` | Health tracking | `provider_id`, `status`, `last_check`, `error_count` |
| `model_health` | Model tracking | `model_id`, `uptime_24h`, `error_rate`, `avg_latency` |
| `user_transactions` | Billing | `user_id`, `credits_used`, `cost`, `model_id`, `timestamp` |
| `audit_logs` | Compliance | `user_id`, `action`, `resource`, `timestamp` |
| `chat_history` | Conversation history | `user_id`, `conversation_id`, `messages`, `tokens_used` |

### Secondary Cache: Redis

```
Key Patterns:
├─ sessions:{session_id} → Session data
├─ rate_limit:{user_id}:{endpoint} → Request counts
├─ cache:{key} → Application cache
├─ provider_health:{provider} → Last health check
├─ model_availability:{model} → Availability status
└─ metrics:{metric_name} → Pre-aggregated metrics
```

### Observability Stack

```
Prometheus (Metrics)
├─ Scrapes: GET http://localhost:8000/metrics
├─ Retention: 15 days
└─ Storage: Time-series data for 100+ metrics

Loki (Logs)
├─ Pushed by: src/config/logging_config.py (async handler)
├─ Labels: {job, service, level, trace_id}
├─ Retention: 30 days
└─ Integration: Non-blocking queue (async, batched)

Tempo (Traces)
├─ OpenTelemetry integration (future)
├─ Distributed tracing
└─ Correlation with logs via trace_id
```

---

## Data Flow Examples

### Example 1: Chat Completion Request

```
Client Request
    │
    ▼
[LAYER 1] POST /v1/chat/completions → unified_chat.py
    │
    ├─ Validate input (pydantic models)
    ├─ Extract API key dependency
    └─ Format request
    │
    ▼
[LAYER 2] Model Inference Service
    │
    ├─ Call canonical_registry → resolve model name
    ├─ Look up provider_registry → get provider config
    ├─ Call provider API with retry logic
    ├─ Record metrics:
    │  ├─ model_inference_requests_total{model, provider} + 1
    │  ├─ model_inference_duration_seconds{model, provider} += latency
    │  ├─ tokens_used_total{model, provider} += tokens
    │  └─ credits_used_total{model, provider} += cost
    ├─ Update analytics
    └─ Stream response
    │
    ▼
[LAYER 3] Supabase + Cache
    │
    ├─ Async: Store in chat_history
    ├─ Async: Update user transaction
    ├─ Async: Update model_health metrics
    └─ Async: Update auth_cache
    │
    ▼
Client Response (HTTP 200 + streamed content)
```

### Example 2: Health Check Request

```
Railway Healthcheck (GET /health)
    │
    ▼
[LAYER 1] Fallback endpoint in main.py (ALWAYS defined)
    │
    ├─ Check app is running (instant)
    └─ Return HTTP 200 ✅
    │
    ▼
[Optional] Full health route (if loaded successfully)
    │
    ├─ Check database initialization status
    ├─ Check provider connectivity
    └─ Return detailed health data
    │
    ▼
[LAYER 3] Supabase status
    │
    └─ get_initialization_status() (cached)
```

### Example 3: Metrics Collection

```
Background Task (Provider Health Tracker - every 30s)
    │
    ▼
[LAYER 2] provider_health_tracker.py
    │
    ├─ For each provider:
    │  ├─ Check provider availability
    │  ├─ Calculate error rate
    │  ├─ Measure response time
    │  └─ Update metrics:
    │     ├─ provider_availability{provider} = 1/0
    │     ├─ provider_error_rate{provider} = 0.05
    │     └─ provider_response_time_seconds{provider} += latency
    │
    └─ Calculate provider_health_score
       └─ (availability * 0.4) + ((1 - error_rate) * 0.3) + (latency_score * 0.3)
    │
    ▼
[LAYER 1] GET /prometheus/metrics
    │
    └─ prometheus_client.REGISTRY.generate_latest()
    │
    ▼
[LAYER 3] Prometheus scrapes metrics every 15s
    │
    └─ Stores in time-series database
```

---

## Logging Architecture (Loki Integration)

### Current Implementation (Non-Blocking)

**File**: `src/config/logging_config.py` (Lines 25-150)

```
Main Thread                    Background Worker Thread
─────────────────────────────────────────────────────
Application Log Event
    │
    ├─ Format as JSON
    │
    ├─ Add trace context
    │
    └─ Emit to logger
        │
        └─ LokiLogHandler.emit()
            │
            ├─ Create log record (instant)
            │
            └─ Queue to _queue.put() (NON-BLOCKING)
                │                        │
                │                        └─> Background Thread
                │                            │
                │                            ├─ Waits on queue
                │                            │
                ├─ Return to app (fast!)    ├─ Batches logs
                │                            │
                └─ Continue processing      ├─ HTTP POST to Loki
                                            │
                                            └─ Retry with exponential backoff
```

**Key Features**:
- ✅ **Non-blocking**: Main thread never waits for HTTP request
- ✅ **Batched**: Reduces HTTP calls (queue size: 10,000 logs)
- ✅ **Graceful shutdown**: Flushes remaining logs on exit
- ✅ **Fault-tolerant**: Silently drops logs if queue is full
- ✅ **Connection pooling**: Max 10 connections, 5 keepalive

### PR #681 Fix

**What was fixed**: Loki handler was making **blocking** HTTP requests for every log message
**Symptoms**: 7+ minute startup time (thousands of logs during init)
**Solution**: Moved to async queue with background worker thread
**Status**: ✅ Already implemented in commit 7bc4d82e

---

## Critical Paths & Bottlenecks

### Startup Sequence (Target: < 30s)

```
0-5s:   Load FastAPI + Middleware
5-10s:  Initialize Supabase connection (with retries)
10-15s: Load provider registry (16 providers)
15-20s: Start background health tracker
20-25s: Register routes dynamically
25-30s: Start Loki handler (async, non-blocking)
30s+:   Ready to accept requests ✅
```

**Bottlenecks**:
1. ⏱️ Supabase connection: ~3-5s (retries: 2)
2. ⏱️ Provider loading: ~2-3s (imports for each)
3. ⏱️ Route loading: ~1-2s (dynamic imports)

### Request Latency (Target: < 500ms p95)

```
Authentication (API key lookup)
├─ Auth cache hit: ~1ms ✅
└─ Auth cache miss: ~20ms (Redis lookup)

Model inference
├─ Provider call: 100-5000ms (varies)
├─ Metric recording: ~1ms
├─ Response formatting: ~5ms
└─ Rate limit check: ~2ms

Total:  100-5050ms (depends on provider)
```

### Memory Usage (Target: < 512MB)

```
Base FastAPI:        ~50MB
Supabase client:     ~20MB
Provider clients:    ~100MB (OpenAI, Claude, etc.)
Cache (in-memory):   ~50MB
Prometheus metrics:  ~20MB
Loki async queue:    ~10MB (max 10k items)
─────────────────────────────
Total:              ~250MB (headroom for spikes)
```

---

## Monitoring & Observability

### Key Metrics to Monitor

**System Health**:
- `fastapi_requests_total` - Request volume
- `fastapi_requests_duration_seconds` - Latency distribution
- `fastapi_exceptions_total` - Error rate

**Provider Health**:
- `provider_availability{provider}` - 1/0 status
- `provider_error_rate{provider}` - % of failed requests
- `gatewayz_provider_health_score{provider}` - Composite score (0-1)

**Business Metrics**:
- `tokens_used_total{model}` - Token consumption
- `credits_used_total{model}` - Cost tracking
- `user_subscription_count` - Active subscriptions

### Prometheus Endpoints

**Standard Prometheus**:
```
GET /metrics
```

**New Structured Endpoints** (to implement):
```
GET /prometheus/metrics/system      → System metrics only
GET /prometheus/metrics/providers   → Provider health only
GET /prometheus/metrics/models      → Model metrics only
GET /prometheus/metrics/business    → Business metrics only
GET /prometheus/metrics/performance → Latency/throughput only
```

---

## Design Principles

1. **Layered Separation**: Routes handle HTTP, Services handle logic, DBMS handles data
2. **Async-First**: All I/O is non-blocking (Loki, Redis, Supabase)
3. **Graceful Degradation**: App works in degraded mode if DB is unavailable
4. **Observable**: Every critical operation is instrumented with metrics
5. **Fault-Tolerant**: Retries, circuit breakers, fallbacks at each layer
6. **Secure**: Authentication at route layer, validation at service layer

---

## Next Steps

1. ✅ Implement `/prometheus/metrics/...` structured endpoints
2. ✅ Add missing provider health metrics
3. ✅ Add circuit breaker metrics
4. ✅ Add cost tracking and token efficiency metrics
5. ✅ Create comprehensive test suite for Loki non-blocking behavior
6. ✅ Update Grafana dashboards with new metrics
