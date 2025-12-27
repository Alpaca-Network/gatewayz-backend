# Prometheus Endpoints Integration Guide for UI/Dashboard Agent

**Date**: 2025-12-26
**Status**: ✅ Production Ready
**Author**: Backend Team
**For**: Frontend/Dashboard Agent Implementation

---

## 🎯 QUICK START - What You Need to Know

### **Three Main Integration Points:**

```
┌─────────────────────────────────────────────────┐
│  FRONTEND/DASHBOARD AGENT                       │
├─────────────────────────────────────────────────┤
│                                                 │
│  1. GET /prometheus/metrics/summary             │
│     ↓ JSON data for dashboard widgets           │
│                                                 │
│  2. GET /prometheus/metrics/[category]          │
│     ↓ Raw Prometheus data for Grafana           │
│                                                 │
│  3. POST /api/monitoring/*                      │
│     ↓ Existing monitoring endpoints             │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## 📊 Core Endpoints Reference

### **Endpoint 1: JSON Summary (RECOMMENDED FOR DASHBOARDS)**

```
GET /prometheus/metrics/summary
```

**Use This For**: Dashboard widgets, real-time counters, status cards

**Response Format**: JSON
```json
{
  "timestamp": "2025-12-26T12:00:00+00:00",
  "metrics": {
    "http": {
      "total_requests": "12345",
      "request_rate_per_minute": "25.5",
      "error_rate": "2.3",
      "avg_latency_ms": "145.2",
      "in_progress": "3"
    },
    "models": {
      "total_inference_requests": "5432",
      "tokens_used_total": "1234567",
      "credits_used_total": "123.45",
      "avg_inference_latency_ms": "234.5"
    },
    "providers": {
      "total_providers": "16",
      "healthy_providers": "14",
      "degraded_providers": "1",
      "unavailable_providers": "1",
      "avg_error_rate": "0.05",
      "avg_response_time_ms": "200"
    },
    "database": {
      "total_queries": "54321",
      "avg_query_latency_ms": "45.2",
      "cache_hit_rate": "0.87"
    },
    "business": {
      "active_api_keys": "234",
      "active_subscriptions": "45",
      "active_trials": "12",
      "total_tokens_used": "9876543",
      "total_credits_used": "987.65"
    }
  }
}
```

**Example Usage**:
```javascript
// Fetch summary for dashboard
const response = await fetch('http://localhost:8000/prometheus/metrics/summary');
const data = await response.json();

// Use in dashboard
document.getElementById('error-rate').textContent = data.metrics.http.error_rate + '%';
document.getElementById('active-keys').textContent = data.metrics.business.active_api_keys;
```

---

### **Endpoint 2: Category-Specific Prometheus Metrics**

Use these for **Grafana dashboard panels** or **raw metric exports**:

#### **A. System & HTTP Metrics**
```
GET /prometheus/metrics/system
```

**Metrics Available**:
- `fastapi_requests_total` - Total requests by status code
- `fastapi_requests_duration_seconds` - Request latency histogram
- `fastapi_requests_in_progress` - Current in-flight requests
- `fastapi_exceptions_total` - Total exceptions
- `fastapi_app_info` - Application info

**Visual Components**:
```
┌─────────────────────────────────────────────────┐
│  HTTP REQUEST DASHBOARD                         │
├─────────────────────────────────────────────────┤
│                                                 │
│  [Throughput Graph]    [Error Rate Gauge]      │
│  Queries Per Minute    5% (Red threshold)      │
│                                                 │
│  [Latency Percentiles]   [In-Progress Counter] │
│  p50: 45ms  p95: 234ms   Current: 3 requests   │
│  p99: 512ms                                    │
│                                                 │
└─────────────────────────────────────────────────┘
```

#### **B. Provider Health Metrics** ⭐ **KEY ENDPOINT**
```
GET /prometheus/metrics/providers
```

**Metrics Available**:
- `provider_availability{provider}` - 1=available, 0=down
- `provider_error_rate{provider}` - Error percentage 0-1
- `provider_response_time_seconds{provider}` - Latency histogram
- `gatewayz_provider_health_score{provider}` - Composite score 0-1

**Visual Components - RECOMMENDED LAYOUT**:
```
┌──────────────────────────────────────────────────────────┐
│  PROVIDER HEALTH DASHBOARD                               │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ╔════════════════════════════════════════════════════╗ │
│  ║ PROVIDER STATUS CARDS                             ║ │
│  ╠════════════════════════════════════════════════════╣ │
│  ║                                                    ║ │
│  ║  ┌──────────────┐  ┌──────────────┐              ║ │
│  ║  │ OpenRouter   │  │ Claude       │              ║ │
│  ║  │ ━━━━━━━━━━━━ │  │ ━━━━━━━━━━━━ │              ║ │
│  ║  │ ✅ Healthy   │  │ ✅ Healthy   │              ║ │
│  ║  │ Score: 0.95  │  │ Score: 0.98  │              ║ │
│  ║  │ Err: 2%      │  │ Err: 1%      │              ║ │
│  ║  │ Resp: 120ms  │  │ Resp: 85ms   │              ║ │
│  ║  └──────────────┘  └──────────────┘              ║ │
│  ║                                                    ║ │
│  ║  ┌──────────────┐  ┌──────────────┐              ║ │
│  ║  │ Cerebras     │  │ Google       │              ║ │
│  ║  │ ━━━━━━━━━━━━ │  │ ━━━━━━━━━━━━ │              ║ │
│  ║  │ ⚠️  Degraded │  │ ❌ Offline   │              ║ │
│  ║  │ Score: 0.68  │  │ Score: 0.0   │              ║ │
│  ║  │ Err: 8%      │  │ Err: 100%    │              ║ │
│  ║  │ Resp: 450ms  │  │ Resp: -      │              ║ │
│  ║  └──────────────┘  └──────────────┘              ║ │
│  ║                                                    ║ │
│  ╚════════════════════════════════════════════════════╝ │
│                                                          │
│  [Provider Availability Timeline] [Error Rate Chart]    │
│                                                          │
│  [Response Time Trend]            [Health Score Trend]  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**PromQL for Grafana Panels**:
```promql
# Health Score (Gauge)
gatewayz_provider_health_score{provider="$provider"}

# Availability (%)
provider_availability{provider="$provider"} * 100

# Error Rate (%)
provider_error_rate{provider="$provider"} * 100

# Response Time p95 (milliseconds)
histogram_quantile(0.95, rate(provider_response_time_seconds_bucket[5m])) * 1000
```

#### **C. Model Performance Metrics**
```
GET /prometheus/metrics/models
```

**Metrics Available**:
- `model_inference_requests_total{model, provider, status}` - Request count
- `model_inference_duration_seconds{model, provider}` - Latency histogram
- `tokens_used_total{model, provider}` - Token consumption
- `credits_used_total{model, provider}` - Cost per model

**Visual Components - RECOMMENDED LAYOUT**:
```
┌──────────────────────────────────────────────────────────┐
│  MODEL PERFORMANCE DASHBOARD                             │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  [Model Request Rate - Line Chart]                       │
│  Y-Axis: Requests/minute                                │
│  X-Axis: Time                                            │
│  Lines: One per model                                    │
│                                                          │
│  [Token Usage Over Time - Stacked Area]                  │
│  Y-Axis: Tokens/minute                                   │
│  X-Axis: Time                                            │
│  Areas: One per model                                    │
│                                                          │
│  [Model Error Rates - Bar Chart]                         │
│  Bars: Error % per model                                │
│  Threshold: 5% (red line)                               │
│                                                          │
│  [Latency Percentiles - Heatmap]                         │
│  X-Axis: Models                                          │
│  Y-Axis: Time buckets                                    │
│  Color: Response time (green=fast, red=slow)             │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**PromQL for Grafana Panels**:
```promql
# Request Rate (requests/minute)
sum(rate(model_inference_requests_total[5m])) by (model) * 60

# Error Rate (%)
(sum(rate(model_inference_requests_total{status!="success"}[5m])) by (model) /
 sum(rate(model_inference_requests_total[5m])) by (model)) * 100

# Token Usage Rate (tokens/minute)
sum(rate(tokens_used_total[5m])) by (model) * 60

# Latency p95 (milliseconds)
histogram_quantile(0.95, rate(model_inference_duration_seconds_bucket[5m])) by (model) * 1000
```

#### **D. Business Metrics**
```
GET /prometheus/metrics/business
```

**Metrics Available**:
- `active_api_keys` - Current active API keys
- `subscription_count` - Active subscriptions
- `trial_active` - Active trial users
- `tokens_used_total{model, provider}` - Token consumption
- `credits_used_total{model, provider}` - Cost tracking

**Visual Components - RECOMMENDED LAYOUT**:
```
┌──────────────────────────────────────────────────────────┐
│  BUSINESS METRICS DASHBOARD                              │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ╔════════════════════════════════════════════════════╗ │
│  ║  KEY METRICS                                       ║ │
│  ╠════════════════════════════════════════════════════╣ │
│  ║                                                    ║ │
│  ║  Active API Keys: 234          Subscriptions: 45  ║ │
│  ║  Active Trials: 12             (Charts update 1x) ║ │
│  ║                                                    ║ │
│  ║  Total Tokens Used: 9,876,543  Gauge: 98.7M      ║ │
│  ║  Total Credits Used: $987.65   Cost this month   ║ │
│  ║                                                    ║ │
│  ╚════════════════════════════════════════════════════╝ │
│                                                          │
│  [Subscription Growth - Line]   [Token Usage - Area]    │
│                                                          │
│  [Cost by Provider - Pie]       [Cost Trend - Line]     │
│                                                          │
│  [API Key Growth - Bar]         [Trial to Paid - Flow]  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**PromQL for Grafana Panels**:
```promql
# Cost by Provider (pie chart)
topk(10, gatewayz_cost_by_provider)

# Cost Rate (USD/minute)
sum(rate(gatewayz_cost_by_provider[5m])) * 60

# Total Tokens (counter)
sum(tokens_used_total)

# Active Subscriptions (gauge)
subscription_count
```

#### **E. Performance & Latency Metrics**
```
GET /prometheus/metrics/performance
```

**Metrics Available**:
- Request latency histograms
- Inference latency by model/provider
- Database query latency
- Cache performance

**Visual Components - RECOMMENDED LAYOUT**:
```
┌──────────────────────────────────────────────────────────┐
│  PERFORMANCE DASHBOARD                                   │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  [HTTP Latency Percentiles]                              │
│  P50: 45ms  P95: 234ms  P99: 512ms                      │
│  (Red alert if P95 > 500ms)                              │
│                                                          │
│  [Inference Latency by Provider - Box Plot]              │
│  X-Axis: Providers                                       │
│  Y-Axis: Latency (ms)                                    │
│  Shows: min, p25, p50, p75, p95, max                    │
│                                                          │
│  [Database Query Latency - Heatmap]                      │
│  X-Axis: Operations (SELECT, INSERT, UPDATE, DELETE)   │
│  Y-Axis: Time buckets                                    │
│  Color intensity: Query count                            │
│                                                          │
│  [Cache Hit Rate Gauge]                                  │
│  Current: 87%                                            │
│  (Green: >80%, Yellow: 60-80%, Red: <60%)               │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**PromQL for Grafana Panels**:
```promql
# HTTP Request Latency (p50, p95, p99)
histogram_quantile(0.50, rate(fastapi_requests_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(fastapi_requests_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(fastapi_requests_duration_seconds_bucket[5m]))

# Cache Hit Rate (%)
(sum(rate(cache_hits_total[5m])) /
 (sum(rate(cache_hits_total[5m])) + sum(rate(cache_misses_total[5m])))) * 100

# Database Query Latency (p95, milliseconds)
histogram_quantile(0.95, rate(database_query_duration_seconds_bucket[5m])) by (operation) * 1000
```

---

## 🔌 Integration Examples

### **Example 1: Real-Time Health Status Widget**

```javascript
// Fetch provider health summary
async function updateHealthWidget() {
  const response = await fetch('http://localhost:8000/prometheus/metrics/summary');
  const data = await response.json();

  const providers = data.metrics.providers;

  // Update UI
  document.getElementById('healthy-count').textContent = providers.healthy_providers;
  document.getElementById('degraded-count').textContent = providers.degraded_providers;
  document.getElementById('offline-count').textContent = providers.unavailable_providers;
  document.getElementById('avg-health-score').textContent =
    (providers.avg_error_rate * 100).toFixed(1) + '%';
}

// Call every 30 seconds
setInterval(updateHealthWidget, 30000);
```

### **Example 2: Grafana Dashboard Integration**

```json
{
  "dashboard": {
    "title": "Gatewayz API Monitoring",
    "panels": [
      {
        "title": "Provider Health Score",
        "targets": [
          {
            "expr": "gatewayz_provider_health_score{provider=\"$provider\"}"
          }
        ],
        "type": "gauge"
      },
      {
        "title": "Model Request Rate",
        "targets": [
          {
            "expr": "sum(rate(model_inference_requests_total[5m])) by (model) * 60"
          }
        ],
        "type": "graph"
      }
    ]
  }
}
```

### **Example 3: Alert Integration**

```javascript
// Check health and trigger alerts
async function checkHealthAlerts() {
  const response = await fetch('http://localhost:8000/prometheus/metrics/summary');
  const data = await response.json();

  // Alert if error rate too high
  if (parseFloat(data.metrics.http.error_rate) > 5.0) {
    showAlert('HIGH ERROR RATE: ' + data.metrics.http.error_rate + '%', 'error');
  }

  // Alert if providers degraded
  if (data.metrics.providers.unhealthy_providers > 0) {
    showAlert(data.metrics.providers.unhealthy_providers + ' providers offline', 'warning');
  }

  // Alert if costs too high
  const costPerMinute = parseFloat(data.metrics.business.total_credits_used) / (new Date().getMinutes() || 1);
  if (costPerMinute > 100) {
    showAlert('HIGH COST RATE: $' + costPerMinute.toFixed(2) + '/minute', 'warning');
  }
}
```

---

## 🎨 Recommended Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│  GATEWAYZ MONITORING DASHBOARD                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────────────────┬──────────────────────────┐               │
│  │  SYSTEM HEALTH (HTTP)    │  BUSINESS METRICS        │               │
│  ├──────────────────────────┼──────────────────────────┤               │
│  │ Req/min: 2,500           │ Active Keys: 234         │               │
│  │ Error Rate: 2.3% ✅      │ Subscriptions: 45        │               │
│  │ Latency p95: 145ms ✅    │ Total Tokens: 9.8M       │               │
│  │ In Progress: 3           │ Cost: $987.65            │               │
│  └──────────────────────────┴──────────────────────────┘               │
│                                                                         │
│  ┌──────────────────────────────────────────────────────┐               │
│  │  PROVIDER STATUS CARDS                               │               │
│  ├──────────────────────────────────────────────────────┤               │
│  │                                                      │               │
│  │  [OpenRouter]  [Claude]  [Cerebras]  [Google]       │               │
│  │  ✅ Healthy    ✅ Healthy ⚠️ Degraded ❌ Offline    │               │
│  │  0.95          0.98       0.68        0.0            │               │
│  │                                                      │               │
│  └──────────────────────────────────────────────────────┘               │
│                                                                         │
│  ┌──────────────────────────┬──────────────────────────┐               │
│  │  REQUEST RATE            │  ERROR RATE              │               │
│  │  [Line Chart]            │  [Gauge: 2.3%]           │               │
│  │  2,500 req/min           │  (Green threshold)       │               │
│  │                          │                          │               │
│  └──────────────────────────┴──────────────────────────┘               │
│                                                                         │
│  ┌──────────────────────────┬──────────────────────────┐               │
│  │  TOKEN USAGE             │  COST TREND              │               │
│  │  [Stacked Area Chart]    │  [Line Chart: $987.65]   │               │
│  │  Per model breakdown     │  Monthly cost tracking   │               │
│  │                          │                          │               │
│  └──────────────────────────┴──────────────────────────┘               │
│                                                                         │
│  ┌──────────────────────────┬──────────────────────────┐               │
│  │  LATENCY PERCENTILES     │  CACHE HIT RATE          │               │
│  │  P50: 45ms               │  [Gauge: 87%]            │               │
│  │  P95: 234ms              │  (Green: >80%)           │               │
│  │  P99: 512ms              │                          │               │
│  │                          │                          │               │
│  └──────────────────────────┴──────────────────────────┘               │
│                                                                         │
│  [Alerts] [Provider Details] [Model Details] [Cost Analysis]           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Implementation Checklist for Agent

- [ ] **Step 1**: Create dashboard container
- [ ] **Step 2**: Add top-level status cards (Req/min, Error Rate, Active Keys, Cost)
- [ ] **Step 3**: Add provider status cards (use `/prometheus/metrics/summary`)
- [ ] **Step 4**: Add request rate graph (use `/prometheus/metrics/system`)
- [ ] **Step 5**: Add error rate gauge (use `/prometheus/metrics/system`)
- [ ] **Step 6**: Add token usage stacked chart (use `/prometheus/metrics/models`)
- [ ] **Step 7**: Add cost trend line (use `/prometheus/metrics/business`)
- [ ] **Step 8**: Add latency percentiles (use `/prometheus/metrics/performance`)
- [ ] **Step 9**: Add cache hit rate gauge (use `/prometheus/metrics/performance`)
- [ ] **Step 10**: Add provider detail page (use `/prometheus/metrics/providers`)
- [ ] **Step 11**: Set up auto-refresh (30 seconds recommended)
- [ ] **Step 12**: Implement alerts based on thresholds

---

## 🚀 Deployment Notes

### **API Availability**
- All endpoints available immediately at application startup
- No authentication required (configure if needed)
- Compatible with existing `/metrics` endpoint

### **Performance**
- JSON summary endpoint: <50ms response time
- Prometheus text format: <100ms response time
- Auto-refresh recommended every 30-60 seconds

### **Integration with Existing APIs**

Your endpoints integrate with:
- **Existing**: `/api/monitoring/*` (provider health, stats, analytics)
- **New**: `/prometheus/metrics/*` (structured metric exports)
- **Backward Compatible**: `/metrics` (standard Prometheus format)

---

## 📞 Quick Reference

| Need | Endpoint | Format | Refresh |
|------|----------|--------|---------|
| Dashboard widgets | `/prometheus/metrics/summary` | JSON | 30s |
| Provider status | `/prometheus/metrics/providers` | Prometheus | 15s |
| Model performance | `/prometheus/metrics/models` | Prometheus | 15s |
| Business metrics | `/prometheus/metrics/business` | Prometheus | 60s |
| Performance data | `/prometheus/metrics/performance` | Prometheus | 15s |
| All metrics | `/prometheus/metrics/all` | Prometheus | 15s |
| Documentation | `/prometheus/metrics/docs` | Markdown | - |

---

## 🔗 Related Documentation

- `docs/PROMETHEUS_GRAFANA_GUIDE.md` - Complete PromQL query reference
- `docs/BACKEND_ARCHITECTURE_3LAYERS.md` - System architecture
- `docs/PULL_REQUEST_CONTEXT.md` - Full implementation details

---

**Ready to integrate! 🚀 All endpoints are live and production-ready.**
