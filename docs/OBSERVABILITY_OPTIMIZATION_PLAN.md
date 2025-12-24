# Observability Stack Optimization Plan

**Branch:** `feat/observability-optimization`
**Goal:** Complete Grafana dashboard integration with minimal infrastructure changes
**Estimated Time:** 3-5 days (phased approach)

---

## 📊 Current State Analysis

### ✅ Already Implemented (80% Complete!)

1. **Prometheus Metrics** ✅
   - `/metrics` endpoint working at `https://api.gatewayz.ai/metrics`
   - All core metrics exported (HTTP, model inference, database, cache)
   - Provider health metrics **DEFINED** (but not populated)

2. **Logging** ✅
   - Grafana Loki integration complete
   - Structured JSON logging with trace correlation
   - Custom `LokiLogHandler` in `src/config/logging_config.py`

3. **Tracing** ✅
   - OpenTelemetry configured
   - OTLP exporter to Tempo
   - FastAPI, HTTPX, Requests instrumentation
   - Configuration in `src/config/opentelemetry_config.py`

4. **Error Monitoring** ✅
   - Sentry integration with adaptive sampling
   - Configured in `src/main.py`

5. **Health Monitoring** ✅
   - Model health monitor running (`src/services/model_health_monitor.py`)
   - Background health checks every 5 minutes
   - Data stored in-memory

### ❌ Gaps (20% Remaining)

1. **Provider Metrics NOT Populated**
   - Metrics defined but values are empty/zero
   - Need background task to update from model_health_monitor data

2. **Missing Composite Metrics**
   - `gatewayz_provider_health_score` - needs to be created
   - `gatewayz_circuit_breaker_state` - no circuit breaker implemented
   - `gatewayz_cost_by_provider` - not tracked per-provider

3. **Missing Dashboard Endpoints**
   - `/health/dashboard` - doesn't exist
   - `/models/stats` - doesn't exist

4. **Redis Metrics Not Instrumented**
   - Redis operations not tracked in Prometheus
   - Need to wrap Redis calls

---

## 🎯 Implementation Strategy

### Phase 1: Critical (Get Dashboards Working) - 1-2 Days

**Goal:** Populate provider health metrics so Grafana dashboards show data

**Changes:**
1. Add `gatewayz_provider_health_score` metric to `src/services/prometheus_metrics.py`
2. Create `src/services/provider_health_tracker.py` - background service to update metrics
3. Integrate tracker into `src/services/startup.py` lifespan
4. Test locally that metrics are populated

**Files to Modify:**
- `src/services/prometheus_metrics.py` (add 1 metric)
- `src/services/provider_health_tracker.py` (new file, ~250 lines)
- `src/services/startup.py` (add 3 lines to start tracker)

**Expected Result:**
- `provider_availability{provider="openrouter"}` shows 1 or 0
- `provider_error_rate{provider="openrouter"}` shows actual error rate
- `gatewayz_provider_health_score{provider="openrouter"}` shows 0.0-1.0 score

---

### Phase 2: Important (Fill Gaps) - 2-3 Days

**Goal:** Add missing metrics and endpoints

**Changes:**
1. Implement cost tracking per provider
   - Add `gatewayz_cost_by_provider` metric
   - Update chat route to track costs

2. Create `/health/dashboard` endpoint
   - Returns aggregated provider health
   - Uses data from model_health_monitor

3. Instrument Redis operations
   - Wrap Redis calls with metric tracking
   - Add operation counters and latency

4. Create `/models/stats` endpoint (optional)
   - Aggregate model usage stats

**Files to Modify:**
- `src/services/prometheus_metrics.py` (add cost metric)
- `src/routes/chat.py` (track cost by provider)
- `src/routes/health.py` (add `/health/dashboard` endpoint)
- `src/services/redis_client.py` or create wrapper (Redis instrumentation)

**Expected Result:**
- `/health/dashboard` returns provider health JSON
- `gatewayz_cost_by_provider{provider="openrouter"}` increments with API usage
- Redis metrics show operation counts

---

### Phase 3: Nice-to-Have (Advanced Features) - 1 Week

**Goal:** Circuit breaker and advanced monitoring

**Changes:**
1. Implement circuit breaker pattern
   - Add circuit breaker to provider calls
   - Track state in `gatewayz_circuit_breaker_state` metric

2. Enhanced dashboard endpoints
   - `/models/stats` with comprehensive model analytics

3. Alert rules (optional)
   - Prometheus alerting rules
   - Low health score alerts

**Files to Create:**
- `src/services/circuit_breaker.py` (new circuit breaker implementation)
- `src/routes/models_stats.py` (model statistics endpoint)

---

## 📁 File Structure (What We're Adding)

```
src/
├── services/
│   ├── prometheus_metrics.py          # MODIFY: Add gatewayz_provider_health_score, gatewayz_cost_by_provider
│   ├── provider_health_tracker.py     # NEW: Background service to populate metrics
│   ├── circuit_breaker.py             # NEW (Phase 3): Circuit breaker implementation
│   └── startup.py                     # MODIFY: Start provider_health_tracker
│
├── routes/
│   ├── health.py                      # MODIFY: Add /health/dashboard endpoint
│   ├── chat.py                        # MODIFY: Track cost by provider
│   └── models_stats.py                # NEW (Phase 2): Model statistics endpoint
│
└── config/
    └── (no changes needed)
```

---

## 🔧 Implementation Details

### Phase 1: Provider Health Tracker

**New Metric Definition** (`src/services/prometheus_metrics.py`):
```python
gatewayz_provider_health_score = get_or_create_metric(
    Gauge,
    "gatewayz_provider_health_score",
    "Composite provider health score (0-1)",
    ["provider"]
)
```

**Background Service** (`src/services/provider_health_tracker.py`):
```python
class ProviderHealthTracker:
    """Updates provider metrics every 30s using model_health_monitor data"""

    async def _update_provider_metrics(self):
        # 1. Read from model_health_monitor.health_data
        # 2. Aggregate by provider
        # 3. Calculate:
        #    - availability (1 if any healthy models, else 0)
        #    - error_rate (errors / total_requests)
        #    - health_score (composite 0-1 score)
        # 4. Update Prometheus metrics
```

**Integration** (`src/services/startup.py`):
```python
from src.services.provider_health_tracker import provider_health_tracker

async def lifespan(app: FastAPI):
    # ... existing startup code ...
    await provider_health_tracker.start()  # ADD THIS

    yield

    await provider_health_tracker.stop()  # ADD THIS
```

---

### Phase 2: Dashboard Endpoint

**New Endpoint** (`src/routes/health.py`):
```python
@router.get("/health/dashboard")
async def health_dashboard():
    """Returns aggregated provider health for Grafana dashboard"""
    from src.services.model_health_monitor import health_monitor

    # Aggregate provider health from health_monitor
    providers = aggregate_provider_health(health_monitor.health_data)

    return {
        "timestamp": datetime.now().isoformat(),
        "providers": providers,
        "summary": calculate_summary(providers)
    }
```

---

## ✅ Testing Strategy

### Local Testing (Before Deploy)

1. **Start the API locally:**
   ```bash
   python src/main.py
   ```

2. **Check metrics endpoint:**
   ```bash
   curl http://localhost:8000/metrics | grep provider
   ```

   **Expected output:**
   ```
   provider_availability{provider="openrouter"} 1.0
   provider_error_rate{provider="openrouter"} 0.05
   gatewayz_provider_health_score{provider="openrouter"} 0.85
   ```

3. **Check health dashboard:**
   ```bash
   curl http://localhost:8000/health/dashboard
   ```

   **Expected output:**
   ```json
   {
     "timestamp": "2025-12-23T10:30:00Z",
     "providers": [
       {
         "name": "openrouter",
         "status": "healthy",
         "availability": 1.0,
         "error_rate": 0.05,
         "health_score": 0.85
       }
     ]
   }
   ```

4. **Wait 30-60 seconds** and check metrics again to see updates

---

## 📈 Expected Metrics After Implementation

### Phase 1 Complete:
```promql
# Provider availability
provider_availability{provider="openrouter"} 1.0
provider_availability{provider="cerebras"} 1.0

# Provider error rates
provider_error_rate{provider="openrouter"} 0.02
provider_error_rate{provider="cerebras"} 0.15

# Provider health scores (NEW)
gatewayz_provider_health_score{provider="openrouter"} 0.95
gatewayz_provider_health_score{provider="cerebras"} 0.70
```

### Phase 2 Complete:
```promql
# Cost tracking (NEW)
gatewayz_cost_by_provider{provider="openrouter"} 125.50
gatewayz_cost_by_provider{provider="cerebras"} 45.20

# Redis operations (NEW)
redis_operations_total{operation="get",status="success"} 1523
redis_operations_total{operation="set",status="success"} 234
redis_operation_duration_seconds{operation="get"} 0.002
```

### Phase 3 Complete:
```promql
# Circuit breaker state (NEW)
gatewayz_circuit_breaker_state{provider="openrouter",state="closed"} 1
gatewayz_circuit_breaker_state{provider="openrouter",state="open"} 0
gatewayz_circuit_breaker_state{provider="cerebras",state="open"} 1
```

---

## 🚀 Deployment Checklist

### Before Merging to Main:

- [ ] All tests pass locally
- [ ] Metrics endpoint shows populated data
- [ ] Health dashboard returns valid JSON
- [ ] No errors in logs during 5-minute run
- [ ] Background tasks start and stop cleanly
- [ ] Git commit with clear message

### After Merging:

- [ ] Verify `/metrics` on production
- [ ] Check Grafana dashboards populate with data
- [ ] Monitor Sentry for any new errors
- [ ] Verify health dashboard endpoint works

---

## 🎯 Success Criteria

### Phase 1:
- ✅ Provider health metrics show non-zero values
- ✅ Grafana "GatewayZ Application Health" dashboard shows provider status
- ✅ Background tracker runs without errors

### Phase 2:
- ✅ `/health/dashboard` endpoint returns provider data
- ✅ Cost tracking shows per-provider spending
- ✅ Redis metrics appear in Grafana

### Phase 3:
- ✅ Circuit breaker prevents cascading failures
- ✅ `/models/stats` provides model analytics
- ✅ Alerting rules trigger on low health scores

---

## 📝 Notes

### Why This Approach Works:

1. **Leverages Existing Infrastructure**
   - Uses model_health_monitor data (already collecting health)
   - Uses Prometheus metrics (already exported)
   - No new database tables needed
   - No new external services

2. **Minimal Code Changes**
   - Phase 1: ~300 lines total
   - Most logic is aggregation, not new data collection
   - Background task pattern already used (model_health_monitor)

3. **Progressive Enhancement**
   - Phase 1 gets dashboards working immediately
   - Phases 2-3 add nice-to-haves without blocking

4. **Testable Locally**
   - No Railway/Grafana needed for development
   - Can verify metrics endpoint locally
   - Can test background tasks with asyncio

### Risks & Mitigations:

| Risk | Mitigation |
|------|------------|
| Background task crashes | Wrap in try/except, log errors, continue loop |
| Memory leak from metrics | Use existing metrics registry, no duplication |
| Performance impact | Update every 30s (not per-request), use existing data |
| Circular imports | Import model_health_monitor inside functions |

---

## 🔗 Related Documentation

- [BACKEND_METRICS_REQUIREMENTS.md](../monitoring/BACKEND_METRICS_REQUIREMENTS.md) - Original plan
- [PROMETHEUS_SETUP.md](monitoring/PROMETHEUS_SETUP.md) - Prometheus configuration
- [MODEL_HEALTH_SENTRY_INTEGRATION.md](monitoring/MODEL_HEALTH_SENTRY_INTEGRATION.md) - Health monitoring

---

**Last Updated:** 2025-12-23
**Author:** Claude (Observability Optimization)
**Status:** Ready for Implementation
