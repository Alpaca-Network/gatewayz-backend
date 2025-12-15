# Prometheus Metrics Implementation for GatewayZ API

## Overview

This document describes the comprehensive Prometheus metrics implementation for the GatewayZ API, enabling full observability through Prometheus and Grafana integration.

## Architecture

### Components

1. **MetricsCollector** (`src/services/metrics_instrumentation.py`)
   - Core metrics collection engine
   - Tracks latency, request counts, errors, and provider/model-specific metrics
   - Calculates percentiles and aggregations in real-time

2. **PrometheusExporter** (`src/services/prometheus_exporter.py`)
   - Converts collected metrics to Prometheus exposition format
   - Exports in TYPE 0.0.4 text format compatible with Prometheus scraping

3. **Metrics Routes** (`src/routes/metrics.py`)
   - `/metrics` - Prometheus scrape endpoint (exposition format)
   - `/metrics/json` - JSON formatted metrics for programmatic access
   - `/metrics/health` - Health status with key metrics
   - `/metrics/reset` - Admin endpoint to reset metrics

## Metrics Collected

### Request Latency Metrics

Tracks HTTP request latency with percentile calculations:

- `http_request_latency_avg_seconds` - Average latency per endpoint
- `http_request_latency_p50_seconds` - 50th percentile (median) latency
- `http_request_latency_p95_seconds` - 95th percentile latency
- `http_request_latency_p99_seconds` - 99th percentile latency

**Labels:**
- `endpoint` - API endpoint path (e.g., `/api/chat`, `/api/models`)

**Example:**
```
http_request_latency_avg_seconds{endpoint="/api/chat"} 0.245
http_request_latency_p95_seconds{endpoint="/api/chat"} 0.512
http_request_latency_p99_seconds{endpoint="/api/chat"} 0.891
```

### Request Count Metrics

Tracks total requests per endpoint and HTTP method:

- `http_requests_total` - Total HTTP requests (counter)

**Labels:**
- `endpoint` - API endpoint path
- `method` - HTTP method (GET, POST, PUT, DELETE, etc.)

**Example:**
```
http_requests_total{endpoint="/api/chat",method="POST"} 15234
http_requests_total{endpoint="/api/models",method="GET"} 8901
```

### Error Metrics

Tracks errors per endpoint and method:

- `http_request_errors_total` - Total request errors (counter)

**Labels:**
- `endpoint` - API endpoint path
- `method` - HTTP method

**Example:**
```
http_request_errors_total{endpoint="/api/chat",method="POST"} 234
http_request_errors_total{endpoint="/api/models",method="GET"} 12
```

### HTTP Status Code Metrics

Tracks responses by status code:

- `http_response_status_total` - Total responses by status code (counter)

**Labels:**
- `endpoint` - API endpoint path
- `status` - HTTP status code (200, 400, 500, etc.)

**Example:**
```
http_response_status_total{endpoint="/api/chat",status="200"} 15000
http_response_status_total{endpoint="/api/chat",status="500"} 234
```

### Provider Metrics

Tracks performance per AI provider:

- `provider_requests_total` - Total requests to provider (counter)
- `provider_errors_total` - Total errors from provider (counter)
- `provider_error_rate` - Error rate for provider (gauge, 0.0-1.0)
- `provider_latency_avg_seconds` - Average latency (gauge)
- `provider_latency_min_seconds` - Minimum latency (gauge)
- `provider_latency_max_seconds` - Maximum latency (gauge)

**Labels:**
- `provider` - Provider name (openai, anthropic, google, etc.)

**Example:**
```
provider_requests_total{provider="openai"} 5234
provider_errors_total{provider="openai"} 45
provider_error_rate{provider="openai"} 0.0086
provider_latency_avg_seconds{provider="openai"} 0.342
provider_latency_p95_seconds{provider="openai"} 0.756
```

### Model Metrics

Tracks performance per AI model:

- `model_requests_total` - Total requests for model (counter)
- `model_errors_total` - Total errors for model (counter)
- `model_error_rate` - Error rate for model (gauge)
- `model_latency_avg_seconds` - Average latency (gauge)

**Labels:**
- `model` - Model identifier (gpt-4, claude-3, etc.)

**Example:**
```
model_requests_total{model="gpt-4"} 3421
model_errors_total{model="gpt-4"} 23
model_error_rate{model="gpt-4"} 0.0067
model_latency_avg_seconds{model="gpt-4"} 0.456
```

### Cache Metrics

Tracks cache performance:

- `cache_hits_total` - Total cache hits (counter)
- `cache_misses_total` - Total cache misses (counter)
- `cache_hit_rate` - Cache hit rate (gauge, 0.0-1.0)

**Example:**
```
cache_hits_total 12345
cache_misses_total 3456
cache_hit_rate 0.7809
```

### Database Metrics

Tracks database query performance:

- `db_queries_total` - Total database queries (counter)
- `db_query_latency_avg_seconds` - Average query latency (gauge)

**Example:**
```
db_queries_total 45678
db_query_latency_avg_seconds 0.0234
```

### External API Metrics

Tracks calls to external services:

- `external_api_calls_total` - Total calls to external API (counter)
- `external_api_errors_total` - Total errors from external API (counter)

**Labels:**
- `service` - External service name (stripe, sendgrid, etc.)

**Example:**
```
external_api_calls_total{service="stripe"} 234
external_api_errors_total{service="stripe"} 2
```

### Application Metrics

- `gatewayz_uptime_seconds` - Application uptime in seconds (gauge)

## Integration Points

### Using the Metrics Decorator

Track metrics on any async or sync function:

```python
from src.services.metrics_instrumentation import track_request

@track_request(
    endpoint="/api/chat",
    method="POST",
    provider="openai",
    model="gpt-4"
)
async def handle_chat_request(request):
    # Your code here
    pass
```

### Manual Metric Recording

Record metrics programmatically:

```python
from src.services.metrics_instrumentation import get_metrics_collector

collector = get_metrics_collector()

# Record a request
collector.record_request(
    endpoint="/api/chat",
    method="POST",
    latency_seconds=0.245,
    status_code=200,
    provider="openai",
    model="gpt-4"
)

# Record cache operations
collector.record_cache_hit()
collector.record_cache_miss()

# Record database queries
collector.record_db_query(latency_seconds=0.0234)

# Record external API calls
collector.record_external_api_call(service="stripe", error=False)
```

## Prometheus Configuration

Add to `prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'gatewayz-api'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
    scrape_timeout: 10s
```

## Grafana Dashboard Queries

### Latency Dashboard

**P99 Latency by Endpoint:**
```promql
http_request_latency_p99_seconds
```

**Average Latency Trend:**
```promql
rate(http_request_latency_avg_seconds[5m])
```

**Request Rate:**
```promql
rate(http_requests_total[1m])
```

### Error Dashboard

**Error Rate by Endpoint:**
```promql
rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])
```

**Total Errors:**
```promql
http_request_errors_total
```

**Errors by Status Code:**
```promql
http_response_status_total{status=~"5.."}
```

### Provider Dashboard

**Provider Error Rates:**
```promql
provider_error_rate
```

**Provider Latency Comparison:**
```promql
provider_latency_avg_seconds
```

**Provider Request Volume:**
```promql
rate(provider_requests_total[5m])
```

### Model Dashboard

**Model Error Rates:**
```promql
model_error_rate
```

**Model Latency:**
```promql
model_latency_avg_seconds
```

**Model Request Volume:**
```promql
rate(model_requests_total[5m])
```

### Cache Dashboard

**Cache Hit Rate:**
```promql
cache_hit_rate
```

**Cache Operations:**
```promql
rate(cache_hits_total[5m])
rate(cache_misses_total[5m])
```

### Database Dashboard

**Database Query Rate:**
```promql
rate(db_queries_total[5m])
```

**Database Latency:**
```promql
db_query_latency_avg_seconds
```

## Alert Rules

Example alert rules for `prometheus-alerts.yml`:

```yaml
groups:
  - name: gatewayz_alerts
    interval: 30s
    rules:
      - alert: HighErrorRate
        expr: |
          (rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"

      - alert: HighLatency
        expr: http_request_latency_p99_seconds > 1.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P99 latency exceeds 1 second"

      - alert: ProviderDown
        expr: rate(provider_errors_total[5m]) / rate(provider_requests_total[5m]) > 0.2
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Provider error rate exceeds 20%"

      - alert: CacheMissRate
        expr: (1 - cache_hit_rate) > 0.5
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "Cache miss rate exceeds 50%"
```

## Testing Metrics

### Verify Metrics Endpoint

```bash
# Get Prometheus format metrics
curl http://localhost:8000/metrics

# Get JSON format metrics
curl http://localhost:8000/metrics/json

# Get health status
curl http://localhost:8000/metrics/health
```

### Generate Test Traffic

```bash
# Generate requests to populate metrics
for i in {1..100}; do
  curl -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' &
done
wait
```

## Performance Considerations

1. **Memory Usage**: Metrics are stored in memory. For high-traffic systems, consider implementing metric retention policies.

2. **Percentile Calculation**: Percentiles are calculated from all recorded latencies. For very high request volumes, consider using approximate percentile algorithms.

3. **Scrape Interval**: Default 15s scrape interval balances freshness and overhead. Adjust based on your needs.

4. **Cardinality**: Be careful with high-cardinality labels (e.g., user IDs). Limit labels to low-cardinality values.

## Integration with Existing Code

The metrics system is designed to be non-intrusive:

1. Add `@track_request()` decorator to endpoints
2. Call `get_metrics_collector()` for manual recording
3. Metrics are collected automatically without affecting request handling
4. Graceful degradation if metrics collection fails

## Future Enhancements

1. **Histogram Buckets**: Implement proper Prometheus histogram buckets for latency
2. **Custom Metrics**: Add business-specific metrics (tokens used, cost, etc.)
3. **Metric Retention**: Implement time-window based metric retention
4. **Distributed Tracing**: Integrate with OpenTelemetry for distributed tracing
5. **Custom Exporters**: Add exporters for other monitoring systems (Datadog, New Relic, etc.)

## Troubleshooting

### Metrics Not Appearing in Prometheus

1. Check Prometheus scrape logs: `curl http://prometheus:9090/api/v1/targets`
2. Verify endpoint is accessible: `curl http://localhost:8000/metrics`
3. Check Prometheus configuration for correct job name and path

### High Memory Usage

1. Check metric cardinality: `curl http://localhost:8000/metrics/json | jq '.providers | length'`
2. Implement metric retention policies
3. Consider metric sampling for high-volume endpoints

### Missing Metrics

1. Ensure `@track_request()` decorator is applied to endpoints
2. Check that `record_request()` is called with correct parameters
3. Verify metrics are being collected: `curl http://localhost:8000/metrics/json`
