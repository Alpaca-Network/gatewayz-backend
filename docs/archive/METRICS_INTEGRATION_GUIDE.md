# GatewayZ Metrics Integration Guide

## Quick Start

### 1. Enable Metrics Collection

The metrics system is automatically initialized when the application starts. No additional configuration is required.

### 2. Access Metrics Endpoints

```bash
# Prometheus format (for Prometheus scraping)
curl http://localhost:8000/metrics

# JSON format (for programmatic access)
curl http://localhost:8000/metrics/json

# Health status with metrics
curl http://localhost:8000/metrics/health
```

### 3. Configure Prometheus

Update `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'gatewayz-api'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

### 4. Add Grafana Data Source

1. Open Grafana: http://localhost:3000
2. Configuration → Data Sources → Add
3. Select Prometheus
4. URL: http://prometheus:9090
5. Save & Test

## Instrumentation Patterns

### Pattern 1: Decorator-Based Tracking

```python
from src.services.metrics_instrumentation import track_request

@router.post("/api/chat")
@track_request(endpoint="/api/chat", method="POST", provider="openai")
async def chat(request: ChatRequest):
    # Your implementation
    return response
```

### Pattern 2: Manual Recording

```python
from src.services.metrics_instrumentation import get_metrics_collector
import time

collector = get_metrics_collector()
start = time.time()

try:
    # Your code
    result = await process_request()
    status = 200
except Exception as e:
    status = 500
    raise
finally:
    latency = time.time() - start
    collector.record_request(
        endpoint="/api/custom",
        method="POST",
        latency_seconds=latency,
        status_code=status,
        provider="custom-provider"
    )
```

### Pattern 3: Context Manager

```python
from src.services.metrics_instrumentation import get_metrics_collector
import time

collector = get_metrics_collector()

class MetricsContext:
    def __init__(self, endpoint, method):
        self.endpoint = endpoint
        self.method = method
        self.start = None
        
    async def __aenter__(self):
        self.start = time.time()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        latency = time.time() - self.start
        status = 500 if exc_type else 200
        collector.record_request(
            endpoint=self.endpoint,
            method=self.method,
            latency_seconds=latency,
            status_code=status
        )

# Usage
async with MetricsContext("/api/endpoint", "POST"):
    # Your code
    pass
```

## Metrics by Use Case

### API Latency Monitoring

**Metrics:**
- `http_request_latency_p99_seconds` - 99th percentile latency
- `http_request_latency_p95_seconds` - 95th percentile latency
- `http_request_latency_avg_seconds` - Average latency

**Grafana Query:**
```promql
http_request_latency_p99_seconds{endpoint="/api/chat"}
```

**Alert Rule:**
```yaml
- alert: HighLatency
  expr: http_request_latency_p99_seconds > 1.0
  for: 5m
```

### Error Rate Monitoring

**Metrics:**
- `http_request_errors_total` - Total errors
- `http_requests_total` - Total requests
- `http_response_status_total` - Responses by status

**Grafana Query:**
```promql
rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])
```

**Alert Rule:**
```yaml
- alert: HighErrorRate
  expr: |
    (rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])) > 0.05
  for: 5m
```

### Provider Performance

**Metrics:**
- `provider_requests_total` - Requests per provider
- `provider_errors_total` - Errors per provider
- `provider_error_rate` - Error rate per provider
- `provider_latency_avg_seconds` - Latency per provider

**Grafana Query:**
```promql
provider_error_rate{provider=~"openai|anthropic|google"}
```

**Alert Rule:**
```yaml
- alert: ProviderDegraded
  expr: provider_error_rate > 0.1
  for: 5m
  labels:
    severity: warning
```

### Model Performance

**Metrics:**
- `model_requests_total` - Requests per model
- `model_errors_total` - Errors per model
- `model_error_rate` - Error rate per model
- `model_latency_avg_seconds` - Latency per model

**Grafana Query:**
```promql
model_latency_avg_seconds{model=~"gpt-4|claude-3|gemini"}
```

### Cache Performance

**Metrics:**
- `cache_hits_total` - Cache hits
- `cache_misses_total` - Cache misses
- `cache_hit_rate` - Hit rate

**Grafana Query:**
```promql
cache_hit_rate
```

**Alert Rule:**
```yaml
- alert: LowCacheHitRate
  expr: cache_hit_rate < 0.5
  for: 10m
```

### Database Performance

**Metrics:**
- `db_queries_total` - Total queries
- `db_query_latency_avg_seconds` - Average query latency

**Grafana Query:**
```promql
rate(db_queries_total[5m])
```

## Dashboard Templates

### Latency Dashboard

Create a new Grafana dashboard with these panels:

1. **P99 Latency by Endpoint** (Graph)
   ```promql
   http_request_latency_p99_seconds
   ```

2. **P95 Latency by Endpoint** (Graph)
   ```promql
   http_request_latency_p95_seconds
   ```

3. **Average Latency Trend** (Graph)
   ```promql
   http_request_latency_avg_seconds
   ```

4. **Request Rate** (Graph)
   ```promql
   rate(http_requests_total[1m])
   ```

### Error Dashboard

1. **Error Rate by Endpoint** (Graph)
   ```promql
   rate(http_request_errors_total[5m]) / rate(http_requests_total[5m])
   ```

2. **Total Errors** (Stat)
   ```promql
   http_request_errors_total
   ```

3. **5xx Errors** (Graph)
   ```promql
   http_response_status_total{status=~"5.."}
   ```

4. **Error Rate Trend** (Graph)
   ```promql
   rate(http_request_errors_total[5m])
   ```

### Provider Dashboard

1. **Provider Error Rates** (Graph)
   ```promql
   provider_error_rate
   ```

2. **Provider Latency** (Graph)
   ```promql
   provider_latency_avg_seconds
   ```

3. **Provider Request Volume** (Graph)
   ```promql
   rate(provider_requests_total[5m])
   ```

4. **Provider Comparison** (Table)
   ```promql
   provider_requests_total
   ```

### Model Dashboard

1. **Model Error Rates** (Graph)
   ```promql
   model_error_rate
   ```

2. **Model Latency** (Graph)
   ```promql
   model_latency_avg_seconds
   ```

3. **Model Request Volume** (Graph)
   ```promql
   rate(model_requests_total[5m])
   ```

4. **Top Models by Requests** (Table)
   ```promql
   model_requests_total
   ```

## Testing the Implementation

### 1. Verify Metrics Collection

```bash
# Check if metrics endpoint is working
curl http://localhost:8000/metrics | head -20

# Check JSON metrics
curl http://localhost:8000/metrics/json | jq '.latency'

# Check health status
curl http://localhost:8000/metrics/health
```

### 2. Generate Test Traffic

```bash
# Simple load test
for i in {1..100}; do
  curl -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' &
done
wait

# Check metrics after traffic
curl http://localhost:8000/metrics/json | jq '.requests'
```

### 3. Verify Prometheus Scraping

1. Open Prometheus: http://localhost:9090
2. Go to Status → Targets
3. Verify `gatewayz-api` target shows "UP"
4. Go to Graph tab
5. Query: `http_requests_total`
6. Should see metrics from your API

### 4. Create Grafana Dashboard

1. Open Grafana: http://localhost:3000
2. Create → Dashboard
3. Add Panel
4. Data Source: Prometheus
5. Query: `http_request_latency_p99_seconds`
6. Save

## Troubleshooting

### Metrics Not Appearing

**Problem:** Prometheus shows "DOWN" for target

**Solution:**
1. Verify API is running: `curl http://localhost:8000/health`
2. Check metrics endpoint: `curl http://localhost:8000/metrics`
3. Check Prometheus logs: `docker logs prometheus`
4. Verify network connectivity between Prometheus and API

**Problem:** No data in Grafana

**Solution:**
1. Wait 15-30 seconds for Prometheus to scrape
2. Check Prometheus Data Source: Configuration → Data Sources
3. Test Data Source connection
4. Verify query syntax in Prometheus UI first

### High Memory Usage

**Problem:** Metrics collector consuming too much memory

**Solution:**
1. Check metric cardinality: `curl http://localhost:8000/metrics/json | jq '.providers | length'`
2. Reduce label cardinality (avoid user IDs, timestamps)
3. Implement metric retention policies
4. Reset metrics periodically: `curl -X POST http://localhost:8000/metrics/reset`

### Missing Metrics

**Problem:** Expected metrics not appearing

**Solution:**
1. Verify decorator is applied: `@track_request(...)`
2. Check endpoint is being called
3. Verify metrics are being recorded: `curl http://localhost:8000/metrics/json`
4. Check for exceptions in application logs

## Performance Impact

- **Memory:** ~1-5MB per 10,000 unique metric combinations
- **CPU:** <1% overhead for metrics collection
- **Latency:** <1ms per request for metrics recording

## Next Steps

1. Integrate metrics into existing endpoints
2. Create Grafana dashboards for monitoring
3. Set up alert rules in Prometheus
4. Monitor metrics in production
5. Adjust retention policies based on traffic

## Related Documentation

- [Prometheus Metrics Implementation](./PROMETHEUS_METRICS_IMPLEMENTATION.md)
- [Prometheus Official Docs](https://prometheus.io/docs/)
- [Grafana Official Docs](https://grafana.com/docs/)
