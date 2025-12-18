# GatewayZ Metrics Quick Reference

## Endpoints

| Endpoint | Format | Purpose |
|----------|--------|---------|
| `/metrics` | Prometheus | Prometheus scraping endpoint |
| `/metrics/json` | JSON | Programmatic access to metrics |
| `/metrics/health` | JSON | Health status with key metrics |
| `/metrics/reset` | JSON | Reset all metrics (admin) |

## Core Metrics

### Latency (Gauges)
```
http_request_latency_avg_seconds{endpoint="..."}
http_request_latency_p50_seconds{endpoint="..."}
http_request_latency_p95_seconds{endpoint="..."}
http_request_latency_p99_seconds{endpoint="..."}
```

### Requests (Counters)
```
http_requests_total{endpoint="...",method="..."}
http_request_errors_total{endpoint="...",method="..."}
http_response_status_total{endpoint="...",status="..."}
```

### Providers (Gauges & Counters)
```
provider_requests_total{provider="..."}
provider_errors_total{provider="..."}
provider_error_rate{provider="..."}
provider_latency_avg_seconds{provider="..."}
provider_latency_min_seconds{provider="..."}
provider_latency_max_seconds{provider="..."}
```

### Models (Gauges & Counters)
```
model_requests_total{model="..."}
model_errors_total{model="..."}
model_error_rate{model="..."}
model_latency_avg_seconds{model="..."}
```

### Cache (Counters & Gauges)
```
cache_hits_total
cache_misses_total
cache_hit_rate
```

### Database (Counters & Gauges)
```
db_queries_total
db_query_latency_avg_seconds
```

### External APIs (Counters)
```
external_api_calls_total{service="..."}
external_api_errors_total{service="..."}
```

### Application (Gauge)
```
gatewayz_uptime_seconds
```

## Common Queries

### Latency
```promql
# P99 latency
http_request_latency_p99_seconds

# Latency trend
rate(http_request_latency_avg_seconds[5m])

# Latency by endpoint
http_request_latency_p95_seconds{endpoint=~"/api/.*"}
```

### Errors
```promql
# Error rate
rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])

# Errors by status
http_response_status_total{status=~"5.."}

# Provider errors
provider_error_rate{provider="openai"}
```

### Requests
```promql
# Request rate
rate(http_requests_total[1m])

# Requests by method
http_requests_total{method="POST"}

# Provider requests
rate(provider_requests_total[5m])
```

### Performance
```promql
# Cache hit rate
cache_hit_rate

# DB query rate
rate(db_queries_total[5m])

# Uptime
gatewayz_uptime_seconds
```

## Alert Rules

```yaml
# High latency
- alert: HighLatency
  expr: http_request_latency_p99_seconds > 1.0
  for: 5m

# High error rate
- alert: HighErrorRate
  expr: (rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])) > 0.05
  for: 5m

# Provider down
- alert: ProviderDown
  expr: provider_error_rate > 0.2
  for: 5m

# Low cache hit rate
- alert: LowCacheHitRate
  expr: cache_hit_rate < 0.5
  for: 10m
```

## Instrumentation

### Decorator
```python
from src.services.metrics_instrumentation import track_request

@track_request(
    endpoint="/api/chat",
    method="POST",
    provider="openai",
    model="gpt-4"
)
async def chat(request):
    pass
```

### Manual Recording
```python
from src.services.metrics_instrumentation import get_metrics_collector

collector = get_metrics_collector()
collector.record_request(
    endpoint="/api/chat",
    method="POST",
    latency_seconds=0.245,
    status_code=200,
    provider="openai",
    model="gpt-4"
)
```

### Cache Operations
```python
collector.record_cache_hit()
collector.record_cache_miss()
```

### Database Operations
```python
collector.record_db_query(latency_seconds=0.0234)
```

### External API Calls
```python
collector.record_external_api_call(service="stripe", error=False)
```

## Testing

```bash
# Get metrics
curl http://localhost:8000/metrics

# Get JSON metrics
curl http://localhost:8000/metrics/json

# Get health
curl http://localhost:8000/metrics/health

# Reset metrics
curl -X POST http://localhost:8000/metrics/reset
```

## Files

| File | Purpose |
|------|---------|
| `src/services/metrics_instrumentation.py` | Core metrics collection |
| `src/services/prometheus_exporter.py` | Prometheus format export |
| `src/routes/metrics.py` | Metrics endpoints |
| `src/services/metrics_parser.py` | Parse Prometheus metrics |
| `docs/PROMETHEUS_METRICS_IMPLEMENTATION.md` | Detailed implementation guide |
| `docs/METRICS_INTEGRATION_GUIDE.md` | Integration patterns |
| `docs/METRICS_QUICK_REFERENCE.md` | This file |

## Key Concepts

**Gauge**: Value that can go up or down (latency, error rate)
**Counter**: Monotonically increasing value (total requests, errors)
**Histogram**: Distribution of values (latencies in buckets)
**Label**: Tag for grouping metrics (endpoint, method, provider)

## Performance

- Memory: ~1-5MB per 10,000 metric combinations
- CPU: <1% overhead
- Latency: <1ms per request

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No metrics | Verify `/metrics` endpoint returns data |
| Prometheus DOWN | Check API is running and accessible |
| Missing data | Verify decorator is applied to endpoints |
| High memory | Reduce label cardinality or reset metrics |
