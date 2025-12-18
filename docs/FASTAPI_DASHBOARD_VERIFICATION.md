# FastAPI Dashboard Verification Checklist

This document provides step-by-step verification procedures for the FastAPI observability stack including Prometheus, Loki, and Tempo integration.

## Quick Reference - All Endpoints

### Metrics Endpoints (Grafana Integration)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/metrics` | GET | Prometheus metrics (text format) |
| `/api/metrics/status` | GET | Metrics service status |
| `/api/metrics/summary` | GET | Registered metrics summary |
| `/api/metrics/test` | POST | Generate test metrics |
| `/api/metrics/grafana-queries` | GET | PromQL queries for dashboards |
| `/api/metrics/health` | GET | Metrics subsystem health |

### Instrumentation Endpoints (Loki/Tempo)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/instrumentation/health` | GET | No | Instrumentation health status |
| `/api/instrumentation/trace-context` | GET | No | Current trace/span IDs |
| `/api/instrumentation/loki/status` | GET | Admin | Loki configuration |
| `/api/instrumentation/tempo/status` | GET | Admin | Tempo configuration |
| `/api/instrumentation/config` | GET | Admin | Full instrumentation config |
| `/api/instrumentation/test-trace` | POST | Admin | Generate test trace |
| `/api/instrumentation/test-log` | POST | Admin | Generate test log |
| `/api/instrumentation/environment-variables` | GET | Admin | Env vars (masked) |

---

## Pre-Verification Checklist

### Environment Variables Required

```bash
# Service Identification
APP_NAME=gatewayz
SERVICE_NAME=gatewayz-api
ENVIRONMENT=production

# Loki Configuration
LOKI_ENABLED=true
LOKI_PUSH_URL=http://loki:3100/loki/api/v1/push
LOKI_QUERY_URL=http://loki:3100/loki/api/v1/query_range

# Tempo Configuration
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo:4318
TEMPO_OTLP_GRPC_ENDPOINT=http://tempo:4317

# OpenTelemetry
OTEL_SERVICE_NAME=gatewayz-api
```

---

## Step 1: Verify Prometheus Metrics

### 1.1 Check /metrics Endpoint

```bash
curl -s http://localhost:8000/metrics | head -50
```

**Expected Output:**
```
# HELP fastapi_app_info FastAPI application information
# TYPE fastapi_app_info gauge
fastapi_app_info{app_name="gatewayz"} 1.0
# HELP fastapi_requests_total Total FastAPI requests
# TYPE fastapi_requests_total counter
fastapi_requests_total{app_name="gatewayz",method="GET",path="/health",status_code="200"} 10.0
...
```

### 1.2 Verify Specific Metrics Exist

```bash
# Check request counter
curl -s http://localhost:8000/metrics | grep "fastapi_requests_total"

# Check duration histogram
curl -s http://localhost:8000/metrics | grep "fastapi_requests_duration_seconds"

# Check in-progress gauge
curl -s http://localhost:8000/metrics | grep "fastapi_requests_in_progress"

# Check model inference metrics
curl -s http://localhost:8000/metrics | grep "model_inference"

# Check token metrics
curl -s http://localhost:8000/metrics | grep "tokens_used_total"
```

### 1.3 Check Metrics Status

```bash
curl -s http://localhost:8000/api/metrics/status | jq
```

**Expected Output:**
```json
{
  "status": "healthy",
  "mode": "live",
  "supabase_available": true,
  "app_name": "gatewayz",
  "environment": "production",
  "service_name": "gatewayz-api",
  "metrics_endpoint": "/metrics",
  "grafana_compatible": true
}
```

### 1.4 Check Metrics Health

```bash
curl -s http://localhost:8000/api/metrics/health | jq
```

**Expected Output:**
```json
{
  "status": "healthy",
  "components": {
    "prometheus": {"status": "healthy", "registered_metrics": 45},
    "supabase": {"status": "healthy", "mode": "live"},
    "opentelemetry": {"status": "healthy", "tempo_enabled": true},
    "loki": {"status": "healthy", "enabled": true}
  }
}
```

---

## Step 2: Verify Loki Logging

### 2.1 Check Loki Status

```bash
curl -s http://localhost:8000/api/instrumentation/loki/status \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" | jq
```

**Expected Output:**
```json
{
  "enabled": true,
  "push_url": "http://loki:3100/loki/api/v1/push",
  "query_url": "http://loki:3100/loki/api/v1/query_range",
  "service_name": "gatewayz-api",
  "environment": "production"
}
```

### 2.2 Generate Test Log

```bash
curl -s -X POST http://localhost:8000/api/instrumentation/test-log \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" | jq
```

**Expected Output:**
```json
{
  "status": "success",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "message": "Test log generated successfully. Check Loki for log details."
}
```

### 2.3 Verify Logs in Loki (if Loki is running)

```bash
curl -s 'http://localhost:3100/loki/api/v1/query?query={service="gatewayz-api"}' | jq
```

---

## Step 3: Verify Tempo Tracing

### 3.1 Check Tempo Status

```bash
curl -s http://localhost:8000/api/instrumentation/tempo/status \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" | jq
```

**Expected Output:**
```json
{
  "enabled": true,
  "otlp_http_endpoint": "http://tempo:4318",
  "otlp_grpc_endpoint": "http://tempo:4317",
  "service_name": "gatewayz-api",
  "environment": "production"
}
```

### 3.2 Generate Test Trace

```bash
curl -s -X POST http://localhost:8000/api/instrumentation/test-trace \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" | jq
```

**Expected Output:**
```json
{
  "status": "success",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "message": "Test trace generated successfully. Check Tempo for trace details."
}
```

### 3.3 Get Current Trace Context

```bash
curl -s http://localhost:8000/api/instrumentation/trace-context | jq
```

**Expected Output:**
```json
{
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "timestamp": "2025-12-17T18:00:00.000000"
}
```

---

## Step 4: Verify Instrumentation Health

### 4.1 Check Overall Instrumentation Health

```bash
curl -s http://localhost:8000/api/instrumentation/health | jq
```

**Expected Output:**
```json
{
  "status": "healthy",
  "loki": {
    "enabled": true,
    "url": "http://loki:3100/loki/api/v1/push",
    "service_name": "gatewayz-api"
  },
  "tempo": {
    "enabled": true,
    "endpoint": "http://tempo:4318",
    "service_name": "gatewayz-api"
  }
}
```

### 4.2 Check Full Configuration

```bash
curl -s http://localhost:8000/api/instrumentation/config \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" | jq
```

---

## Step 5: Generate Test Traffic

### 5.1 Generate Metrics Test Data

```bash
curl -s -X POST http://localhost:8000/api/metrics/test | jq
```

### 5.2 Generate Multiple Requests

```bash
# Generate 10 requests to create metrics data
for i in {1..10}; do
  curl -s http://localhost:8000/health > /dev/null
  curl -s http://localhost:8000/api/metrics/status > /dev/null
  echo "Request $i completed"
  sleep 0.5
done
```

### 5.3 Verify Metrics Updated

```bash
curl -s http://localhost:8000/metrics | grep "fastapi_requests_total" | head -5
```

---

## Step 6: Verify Grafana Dashboard Queries

### 6.1 Get All PromQL Queries

```bash
curl -s http://localhost:8000/api/metrics/grafana-queries | jq '.panels'
```

### 6.2 Test Individual Queries in Prometheus

```bash
# Total Requests Rate
curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(fastapi_requests_total[5m]))' | jq

# Average Response Time
curl -s 'http://localhost:9090/api/v1/query?query=rate(fastapi_requests_duration_seconds_sum[5m])/rate(fastapi_requests_duration_seconds_count[5m])' | jq

# P95 Latency
curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,rate(fastapi_requests_duration_seconds_bucket[5m]))' | jq
```

---

## Step 7: Synthetic Data Fallback Verification

### 7.1 Check Current Mode

```bash
curl -s http://localhost:8000/api/metrics/status | jq '.mode'
```

**Expected:** `"live"` (with Supabase) or `"synthetic"` (without Supabase)

### 7.2 Verify Synthetic Data Generation

If Supabase is unavailable, the service automatically generates synthetic data:

```bash
# Check metrics are still available
curl -s http://localhost:8000/metrics | grep "fastapi_requests_total"

# Verify status shows synthetic mode
curl -s http://localhost:8000/api/metrics/status | jq
```

---

## Troubleshooting

### Issue: No metrics appearing

1. Check if the application is running:
   ```bash
   curl http://localhost:8000/health
   ```

2. Check metrics endpoint directly:
   ```bash
   curl http://localhost:8000/metrics
   ```

3. Verify Prometheus registry:
   ```bash
   curl http://localhost:8000/api/metrics/summary | jq '.registered_metrics'
   ```

### Issue: Loki not receiving logs

1. Check Loki is enabled:
   ```bash
   curl http://localhost:8000/api/instrumentation/health | jq '.loki.enabled'
   ```

2. Verify Loki endpoint is reachable:
   ```bash
   curl http://loki:3100/ready
   ```

3. Check application logs for Loki errors

### Issue: Tempo not receiving traces

1. Check Tempo is enabled:
   ```bash
   curl http://localhost:8000/api/instrumentation/health | jq '.tempo.enabled'
   ```

2. Verify Tempo endpoint is reachable:
   ```bash
   curl http://tempo:3200/ready
   ```

3. Generate a test trace and check for errors:
   ```bash
   curl -X POST http://localhost:8000/api/instrumentation/test-trace \
     -H "Authorization: Bearer YOUR_ADMIN_KEY"
   ```

### Issue: Synthetic mode when Supabase should be available

1. Check Supabase connection:
   ```bash
   curl http://localhost:8000/api/metrics/health | jq '.components.supabase'
   ```

2. Verify SUPABASE_URL and SUPABASE_KEY environment variables

---

## Success Criteria

✅ `/metrics` returns Prometheus format metrics
✅ `/api/metrics/status` shows `"status": "healthy"`
✅ `/api/metrics/health` shows all components healthy
✅ `/api/instrumentation/health` shows Loki and Tempo enabled
✅ Test trace generates valid trace_id
✅ Test log generates valid trace_id for correlation
✅ Grafana queries endpoint returns valid PromQL

---

## Next Steps After Verification

1. **Configure Prometheus Scraping**
   ```yaml
   # prometheus.yml
   scrape_configs:
     - job_name: 'gatewayz-api'
       static_configs:
         - targets: ['api:8000']
       metrics_path: '/metrics'
       scrape_interval: 15s
   ```

2. **Import Grafana Dashboard**
   - Dashboard ID: 16110 (FastAPI Observability)
   - Or use custom queries from `/api/metrics/grafana-queries`

3. **Configure Loki Data Source**
   - URL: `http://loki:3100`
   - Query: `{service="gatewayz-api"}`

4. **Configure Tempo Data Source**
   - URL: `http://tempo:3200`
   - Enable trace-to-logs correlation

---

## Related Documentation

- [Grafana Metrics Endpoints](./GRAFANA_METRICS_ENDPOINTS.md)
- [Instrumentation Endpoints](./INSTRUMENTATION_ENDPOINTS.md)
- [Health Caching Optimization](./HEALTH_CACHING_OPTIMIZATION.md)
