# Grafana Metrics Endpoints

This document describes the metrics endpoints for Grafana integration with Prometheus, Loki, and Tempo.

## Overview

The metrics endpoints provide:
- **Prometheus metrics** for Grafana dashboards
- **Structured JSON logging** for Loki
- **OpenTelemetry traces** for Tempo
- **Synthetic data fallback** when Supabase is unavailable

## Endpoints

### 1. Prometheus Metrics

**GET** `/metrics`

Returns metrics in Prometheus text exposition format for scraping.

**Response:** `text/plain; charset=utf-8`

```
# HELP fastapi_requests_total Total FastAPI requests
# TYPE fastapi_requests_total counter
fastapi_requests_total{app_name="gatewayz",method="POST",path="/v1/chat/completions",status_code="200"} 1234.0
...
```

### 2. Metrics Status

**GET** `/api/metrics/status`

Get metrics service status including mode (live/synthetic).

**Response:**
```json
{
  "status": "healthy",
  "mode": "live",
  "supabase_available": true,
  "app_name": "gatewayz",
  "environment": "production",
  "service_name": "gatewayz-api",
  "timestamp": "2025-12-17T18:00:00.000000",
  "metrics_endpoint": "/metrics",
  "grafana_compatible": true,
  "supported_dashboards": [
    "FastAPI Observability (ID: 16110)",
    "Custom GatewayZ Dashboard"
  ]
}
```

### 3. Metrics Summary

**GET** `/api/metrics/summary`

Get structured metrics summary with registered metrics list.

**Response:**
```json
{
  "status": "healthy",
  "mode": "live",
  "registered_metrics": 45,
  "metrics": [
    {
      "name": "fastapi_requests_total",
      "type": "Counter",
      "description": "Total FastAPI requests"
    }
  ]
}
```

### 4. Test Metrics

**POST** `/api/metrics/test`

Generate test metrics for verification of Prometheus, Loki, and Tempo integration.

**Response:**
```json
{
  "status": "success",
  "message": "Test metrics generated successfully",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "duration_ms": 15,
  "timestamp": "2025-12-17T18:00:00.000000",
  "verification": {
    "prometheus": "Check /metrics endpoint for fastapi_* metrics",
    "loki": "Query: {service=\"gatewayz-api\", test=\"true\"}",
    "tempo": "Search for trace_id: 4bf92f3577b34da6a3ce929d0e0e4736"
  }
}
```

### 5. Grafana Queries

**GET** `/api/metrics/grafana-queries`

Get PromQL queries for Grafana dashboard panels.

**Response:**
```json
{
  "dashboard_id": "16110",
  "dashboard_name": "FastAPI Observability",
  "panels": {
    "total_requests": {
      "title": "Total Requests",
      "query": "sum(rate(fastapi_requests_total[5m]))",
      "description": "Current request rate (requests/second)"
    },
    "requests_per_minute": {
      "title": "Requests Per Minute",
      "query": "rate(fastapi_requests_total[1m]) * 60",
      "description": "Trend line showing RPM over time"
    }
  },
  "loki_queries": {
    "all_logs": "{service=\"gatewayz-api\"}",
    "error_logs": "{service=\"gatewayz-api\", level=\"ERROR\"}"
  },
  "tempo_queries": {
    "service_traces": "service.name=\"gatewayz-api\"",
    "slow_traces": "service.name=\"gatewayz-api\" && duration > 1s"
  }
}
```

### 6. Metrics Health

**GET** `/api/metrics/health`

Health check for metrics collection subsystem.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-12-17T18:00:00.000000",
  "components": {
    "prometheus": {
      "status": "healthy",
      "registered_metrics": 45
    },
    "supabase": {
      "status": "healthy",
      "mode": "live"
    },
    "opentelemetry": {
      "status": "healthy",
      "available": true,
      "tempo_enabled": true
    },
    "loki": {
      "status": "healthy",
      "enabled": true
    }
  }
}
```

## Prometheus Metrics Exposed

### HTTP Request Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `fastapi_requests_total` | Counter | app_name, method, path, status_code | Total HTTP requests |
| `fastapi_requests_duration_seconds` | Histogram | app_name, method, path | Request duration |
| `fastapi_requests_in_progress` | Gauge | app_name, method, path | Concurrent requests |
| `fastapi_request_size_bytes` | Histogram | app_name, method, path | Request body size |
| `fastapi_response_size_bytes` | Histogram | app_name, method, path | Response body size |
| `fastapi_exceptions_total` | Counter | app_name, exception_type | Total exceptions |

### Model Inference Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `model_inference_requests_total` | Counter | provider, model, status | Inference requests |
| `model_inference_duration_seconds` | Histogram | provider, model | Inference duration |
| `tokens_used_total` | Counter | provider, model, token_type | Token consumption |
| `credits_used_total` | Counter | provider, model | Credit consumption |

### Provider Health Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `provider_availability` | Gauge | provider | Provider status (1=up, 0=down) |
| `provider_error_rate` | Gauge | provider | Error rate (0-1) |
| `provider_response_time_seconds` | Histogram | provider | Response time |

### Cache Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `cache_hits_total` | Counter | cache_name | Cache hits |
| `cache_misses_total` | Counter | cache_name | Cache misses |
| `cache_size_bytes` | Gauge | cache_name | Cache size |

## Grafana Dashboard Queries

### Panel: Total Requests
```promql
sum(rate(fastapi_requests_total[5m]))
```

### Panel: Requests Per Minute
```promql
rate(fastapi_requests_total[1m]) * 60
```

### Panel: Errors Per Second
```promql
rate(fastapi_requests_total{status_code=~"4..|5.."}[1m])
```

### Panel: Average Response Time
```promql
rate(fastapi_requests_duration_seconds_sum[5m]) / rate(fastapi_requests_duration_seconds_count[5m])
```

### Panel: P50 Latency
```promql
histogram_quantile(0.50, rate(fastapi_requests_duration_seconds_bucket[5m]))
```

### Panel: P95 Latency
```promql
histogram_quantile(0.95, rate(fastapi_requests_duration_seconds_bucket[5m]))
```

### Panel: P99 Latency
```promql
histogram_quantile(0.99, rate(fastapi_requests_duration_seconds_bucket[5m]))
```

### Panel: CPU Usage
```promql
rate(process_cpu_seconds_total[5m]) * 100
```

### Panel: Memory Usage (MB)
```promql
process_resident_memory_bytes / 1024 / 1024
```

### Panel: Requests In Progress
```promql
sum(fastapi_requests_in_progress)
```

### Panel: Cache Hit Rate
```promql
sum(rate(cache_hits_total[5m])) / (sum(rate(cache_hits_total[5m])) + sum(rate(cache_misses_total[5m])))
```

## Loki Queries

### All Logs
```logql
{service="gatewayz-api"}
```

### Error Logs
```logql
{service="gatewayz-api", level="ERROR"}
```

### Slow Requests (>1s)
```logql
{service="gatewayz-api"} | json | duration_ms > 1000
```

### By Endpoint
```logql
{service="gatewayz-api"} | json | endpoint="/v1/chat/completions"
```

## Tempo Queries

### Service Traces
```
service.name="gatewayz-api"
```

### Slow Traces
```
service.name="gatewayz-api" && duration > 1s
```

### Error Traces
```
service.name="gatewayz-api" && status.code=error
```

## Synthetic Data Mode

When Supabase is unavailable, the metrics service automatically switches to synthetic data mode:

- Generates realistic mock metrics for all endpoints
- Ensures Grafana dashboards always have data to display
- Useful for development and testing
- Automatically switches back to live mode when Supabase becomes available

Check current mode:
```bash
curl http://localhost:8000/api/metrics/status | jq '.mode'
```

## Testing

### Generate Test Traffic
```bash
# Generate test metrics
curl -X POST http://localhost:8000/api/metrics/test

# Verify Prometheus metrics
curl http://localhost:8000/metrics | grep fastapi_requests_total

# Check metrics status
curl http://localhost:8000/api/metrics/status

# Get Grafana queries
curl http://localhost:8000/api/metrics/grafana-queries
```

### Verify in Grafana

1. Open Grafana at `http://localhost:3000`
2. Add Prometheus data source: `http://prometheus:9090`
3. Import FastAPI Observability Dashboard (ID: 16110)
4. Verify panels show data

## Environment Variables

```bash
# Service identification
APP_NAME=gatewayz
SERVICE_NAME=gatewayz-api
ENVIRONMENT=production

# Prometheus
# (No additional config needed - /metrics endpoint is auto-exposed)

# Loki
LOKI_ENABLED=true
LOKI_PUSH_URL=http://loki:3100/loki/api/v1/push

# Tempo
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo:4318
```

## Data Flow

```
FastAPI Backend
    │
    ├─→ /metrics endpoint
    │       ↓
    │   Prometheus scrapes every 15s
    │       ↓
    │   Grafana queries Prometheus
    │       ↓
    │   Dashboard panels update
    │
    ├─→ STDOUT JSON logs
    │       ↓
    │   Loki ingests logs
    │       ↓
    │   Grafana queries Loki
    │       ↓
    │   Logger Stream panel updates
    │
    └─→ OpenTelemetry gRPC (localhost:4317)
            ↓
        Tempo stores traces
            ↓
        Grafana Tempo dashboard
```

## Related Documentation

- [Instrumentation Endpoints](./INSTRUMENTATION_ENDPOINTS.md)
- [Health Caching Optimization](./HEALTH_CACHING_OPTIMIZATION.md)
- [Grafana Loki Documentation](https://grafana.com/docs/loki/latest/)
- [Grafana Tempo Documentation](https://grafana.com/docs/tempo/latest/)
- [Prometheus Documentation](https://prometheus.io/docs/)
