# Monitoring API Reference
**Generated:** 2025-12-28
**For:** Integration with Grafana & other agents

---

## 📋 Provider Names & Slugs

### All Supported Providers (17 total)

| Provider Name | Provider Slug | Status | Streaming | Notes |
|---------------|---------------|--------|-----------|-------|
| OpenRouter | `openrouter` | ✅ Active | ✅ Yes | Multi-provider router |
| Portkey | `portkey` | ✅ Active | ✅ Yes | AI gateway & router |
| Featherless | `featherless` | ✅ Active | ✅ Yes | Featherless AI |
| Chutes | `chutes` | ✅ Active | ✅ Yes | Chutes infrastructure |
| DeepInfra | `deepinfra` | ✅ Active | ✅ Yes | Deep learning infra |
| Fireworks AI | `fireworks` | ✅ Active | ✅ Yes | Fast inference |
| Together AI | `together` | ✅ Active | ✅ Yes | Together platform |
| HuggingFace | `huggingface` | ✅ Active | ✅ Yes | HF inference API |
| XAI | `xai` | ✅ Active | ✅ Yes | Grok provider |
| AIMO | `aimo` | ✅ Active | ✅ Yes | AIMO provider |
| Near AI | `near` | ✅ Active | ✅ Yes | Near infrastructure |
| Fal.ai | `fal` | ✅ Active | ❌ No | Image generation |
| Anannas | `anannas` | ✅ Active | ✅ Yes | Anannas provider |
| Google Vertex AI | `google-vertex` | ✅ Active | ✅ Yes | Google Cloud |
| Modelz | `modelz` | ✅ Active | ✅ Yes | Modelz platform |
| AiHubMix | `aihubmix` | ✅ Active | ✅ Yes | AiHubMix provider |
| Vercel AI Gateway | `vercel-ai-gateway` | ✅ Active | ✅ Yes | Vercel gateway |

---

## 🤖 Model Names by Provider

### Common Models Across Providers

**OpenRouter:**
- `gpt-4o` / `gpt-4o-mini`
- `claude-3-opus` / `claude-3-sonnet` / `claude-3-haiku`
- `gpt-4-turbo`
- `gemini-pro`
- `llama-2-70b`
- `mistral-large`

**Portkey:**
- `gpt-4o`
- `claude-3-opus`
- `mistral-large`
- `gpt-4-turbo`

**Fireworks AI:**
- `gpt-4o`
- `llama-70b`
- `mistral-large`

**Together AI:**
- `llama-70b`
- `mistral-large`
- `gpt-4-turbo`
- `falcon-40b`

**Google Vertex AI:**
- `gemini-pro`
- `gemini-pro-vision`
- `palm-2`

**HuggingFace:**
- `mistral-7b`
- `llama-7b`
- `gpt2`
- `distilbert-base-uncased`

**Fal.ai (Image Generation):**
- `stable-diffusion-3`
- `flux-pro`
- `sdxl-turbo`
- `animate-diff`

---

## 📊 Tier 1 Endpoint Response Examples

### 1. GET `/api/monitoring/health`

**Response:**
```json
[
  {
    "provider": "openrouter",
    "health_score": 98.5,
    "status": "healthy",
    "last_updated": "2025-12-28T23:17:40Z"
  },
  {
    "provider": "portkey",
    "health_score": 96.2,
    "status": "healthy",
    "last_updated": "2025-12-28T23:17:35Z"
  },
  {
    "provider": "together",
    "health_score": 87.3,
    "status": "degraded",
    "last_updated": "2025-12-28T23:15:40Z"
  }
]
```

---

### 2. GET `/api/monitoring/stats/realtime`

**Response:**
```json
{
  "timestamp": "2025-12-28T23:17:40.216653+00:00",
  "providers": {
    "openrouter": {
      "total_requests": 1250,
      "total_cost": 45.67,
      "health_score": 98.5,
      "error_rate": 0.02,
      "avg_latency_ms": 245,
      "hourly_breakdown": {
        "2025-12-28T23:00": {
          "requests": 1250,
          "cost": 45.67,
          "errors": 25,
          "avg_latency_ms": 245
        }
      }
    },
    "portkey": {
      "total_requests": 890,
      "total_cost": 32.45,
      "health_score": 96.2,
      "error_rate": 0.015,
      "avg_latency_ms": 312
    },
    "together": {
      "total_requests": 456,
      "total_cost": 12.34,
      "health_score": 87.3,
      "error_rate": 0.08,
      "avg_latency_ms": 678
    }
  },
  "total_requests": 2596,
  "total_cost": 90.46,
  "avg_health_score": 94.0
}
```

---

### 3. GET `/metrics` (Prometheus Format)

**Response (text/plain):**
```prometheus
# HELP fastapi_app_info_info FastAPI application information
# TYPE fastapi_app_info_info gauge
fastapi_app_info_info{app_name="gatewayz"} 1.0

# HELP fastapi_requests_total Total FastAPI requests
# TYPE fastapi_requests_total counter
fastapi_requests_total{app_name="gatewayz",method="GET",path="/api/monitoring/health",status_code="200"} 1523.0
fastapi_requests_total{app_name="gatewayz",method="POST",path="/v1/chat/completions",status_code="200"} 45678.0
fastapi_requests_total{app_name="gatewayz",method="POST",path="/v1/chat/completions",status_code="429"} 234.0

# HELP fastapi_requests_duration_seconds FastAPI request duration in seconds
# TYPE fastapi_requests_duration_seconds histogram
fastapi_requests_duration_seconds_bucket{app_name="gatewayz",le="0.01",method="GET",path="/api/monitoring/health"} 1450.0
fastapi_requests_duration_seconds_bucket{app_name="gatewayz",le="0.1",method="GET",path="/api/monitoring/health"} 1500.0
fastapi_requests_duration_seconds_bucket{app_name="gatewayz",le="+Inf",method="GET",path="/api/monitoring/health"} 1523.0
fastapi_requests_duration_seconds_sum{app_name="gatewayz",method="GET",path="/api/monitoring/health"} 156.234
fastapi_requests_duration_seconds_count{app_name="gatewayz",method="GET",path="/api/monitoring/health"} 1523.0

# HELP provider_health_score Provider health score (0-100)
# TYPE provider_health_score gauge
provider_health_score{provider="openrouter"} 98.5
provider_health_score{provider="portkey"} 96.2
provider_health_score{provider="together"} 87.3

# HELP provider_request_count Request count by provider
# TYPE provider_request_count counter
provider_request_count{provider="openrouter"} 12450
provider_request_count{provider="portkey"} 8890
provider_request_count{provider="together"} 4560

# HELP model_inference_requests Inference requests by model
# TYPE model_inference_requests counter
model_inference_requests{model="gpt-4o",provider="openrouter"} 5670
model_inference_requests{model="claude-3-opus",provider="portkey"} 3245
model_inference_requests{model="gpt-4-turbo",provider="together"} 2134
```

---

### 4. GET `/api/monitoring/latency/{provider}/{model}`

**Example: `/api/monitoring/latency/openrouter/gpt-4o`**

**Response:**
```json
{
  "provider": "openrouter",
  "model": "gpt-4o",
  "count": 245,
  "avg": 342.5,
  "p50": 278,
  "p95": 567,
  "p99": 892,
  "min": 145,
  "max": 2345,
  "stddev": 125.3
}
```

**Alternative latencies by percentile:** `/api/monitoring/latency/together/llama-70b?percentiles=50,75,90,95,99`

---

## 🚨 Anomaly Detection Configuration

### Detection Conditions & Thresholds

#### 1. **Cost Spike Anomaly**
```
Condition: Total cost > 200% of 24-hour average
Severity: WARNING
Example: If avg_cost = $50, triggers when cost > $100
Formula: current_cost > (avg_cost × 2)
```

#### 2. **Latency Spike Anomaly**
```
Condition: Average latency > 200% of 24-hour average
Severity: WARNING
Example: If avg_latency = 300ms, triggers when latency > 600ms
Formula: current_latency > (avg_latency × 2)
```

#### 3. **Error Rate Anomaly**
```
Condition: Error rate > 10% (HIGH) or > 25% (CRITICAL)
Normal Baseline: < 5%
Severity: WARNING (10-25%), CRITICAL (>25%)
Example:
  - 5% error rate = NORMAL
  - 15% error rate = WARNING
  - 30% error rate = CRITICAL
```

---

### Alert Severity Levels

| Severity | Condition | Action Required | SLA |
|----------|-----------|-----------------|-----|
| **CRITICAL** | Error rate > 25% | Immediate investigation | < 5 min |
| **WARNING** | Cost spike > 200% OR Latency > 200% OR Error rate 10-25% | Investigate within 30 min | < 30 min |
| **INFO** | Other metrics | Monitor | None |

---

### Anomaly Detection Window

- **Time Window:** Last 24 hours
- **Granularity:** Hourly aggregates
- **Data Source:** `metrics_hourly_aggregates` table
- **Calculation:** Per provider

---

## 📈 Tier 2 & 3 Endpoint Examples

### GET `/api/monitoring/error-rates`

```json
{
  "timestamp": "2025-12-28T23:17:40Z",
  "error_rates": {
    "openrouter": {
      "gpt-4o": {
        "total_errors": 25,
        "total_requests": 1250,
        "error_rate": 0.02,
        "trend": "stable"
      },
      "gpt-4-turbo": {
        "total_errors": 45,
        "total_requests": 890,
        "error_rate": 0.0505,
        "trend": "increasing"
      }
    },
    "together": {
      "llama-70b": {
        "total_errors": 120,
        "total_requests": 1500,
        "error_rate": 0.08,
        "trend": "critical"
      }
    }
  }
}
```

### GET `/api/monitoring/cost-analysis`

```json
{
  "timestamp": "2025-12-28T23:17:40Z",
  "period": "last_7_days",
  "total_cost": 1245.67,
  "by_provider": {
    "openrouter": {
      "total_cost": 567.89,
      "percentage": 45.6,
      "requests": 12450,
      "cost_per_request": 0.0456
    },
    "portkey": {
      "total_cost": 345.67,
      "percentage": 27.8,
      "requests": 8890,
      "cost_per_request": 0.0389
    },
    "together": {
      "total_cost": 332.11,
      "percentage": 26.6,
      "requests": 4560,
      "cost_per_request": 0.0728
    }
  },
  "most_expensive_model": {
    "model": "gpt-4o",
    "provider": "openrouter",
    "cost": 345.23,
    "requests": 5670
  }
}
```

### GET `/api/monitoring/anomalies`

```json
{
  "timestamp": "2025-12-28T23:17:40Z",
  "anomalies": [
    {
      "type": "cost_spike",
      "provider": "together",
      "hour": "2025-12-28T20:00:00Z",
      "value": 245.67,
      "expected": 98.34,
      "severity": "warning"
    },
    {
      "type": "latency_spike",
      "provider": "together",
      "hour": "2025-12-28T19:00:00Z",
      "value": 1245,
      "expected": 456,
      "severity": "warning"
    },
    {
      "type": "high_error_rate",
      "provider": "together",
      "hour": "2025-12-28T18:00:00Z",
      "value": 0.32,
      "expected": 0.05,
      "severity": "critical"
    }
  ],
  "total_count": 3,
  "critical_count": 1,
  "warning_count": 2
}
```

---

## 🔑 Query Parameters

### `/api/monitoring/stats/realtime`
- `hours` (int, 1-24, default: 1) - Last N hours

### `/api/monitoring/stats/hourly/{provider}`
- `hours` (int, 1-168, default: 24) - Last N hours

### `/api/monitoring/errors/{provider}`
- `limit` (int, 1-1000, default: 100) - Number of errors to return

### `/api/monitoring/latency/{provider}/{model}`
- `percentiles` (str, default: "50,95,99") - Comma-separated percentiles

### `/api/monitoring/latency-trends/{provider}`
- `hours` (int, 1-168, default: 24) - Last N hours

### `/api/monitoring/error-rates`
- `hours` (int, 1-168, default: 24) - Last N hours

### `/api/monitoring/cost-analysis`
- `days` (int, 1-90, default: 7) - Last N days

---

## 🔐 Authentication

All monitoring endpoints support **optional authentication:**
- **No API Key:** Public access (rate limited)
- **With API Key:** Authenticated access (higher rate limits, private data if applicable)

**Header Format:**
```
Authorization: Bearer YOUR_API_KEY_HERE
```

---

## 📍 Integration Notes for Other Agents

### For Grafana Dashboard Agent:
- Use **Tier 1 endpoints** for core dashboard
- Poll `/api/monitoring/health` every 60s for status
- Poll `/api/monitoring/stats/realtime` every 30s for live data
- Use `/metrics` endpoint with native Prometheus scraping

### For Alerting Agent:
- Monitor `/api/monitoring/anomalies` every 5 minutes
- Trigger alerts based on severity levels (WARNING, CRITICAL)
- Use thresholds:
  - **Error Rate > 10%:** WARNING
  - **Error Rate > 25%:** CRITICAL
  - **Cost Spike > 200%:** WARNING
  - **Latency Spike > 200%:** WARNING

### For Analytics Agent:
- Use `/api/monitoring/cost-analysis` for financial reporting
- Use `/api/monitoring/error-rates` for quality metrics
- Use `/api/monitoring/token-efficiency/{provider}/{model}` for optimization

---

## ✅ Validation Checklist

- [x] All 17 providers listed with slugs
- [x] Example models provided
- [x] Tier 1 endpoint responses documented
- [x] Prometheus format example provided
- [x] Anomaly detection thresholds defined
- [x] Alert severity levels specified
- [x] Query parameters documented
- [x] Authentication explained
- [x] Integration notes for other agents

**Status:** Ready for external agent integration ✅
