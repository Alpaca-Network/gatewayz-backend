# Prometheus & Grafana Integration Guide

**Status**: ✅ Complete Implementation
**Branch**: `fix/fix-prometheus-endpoints`
**Last Updated**: 2025-12-26

This guide covers:
1. Structured Prometheus endpoints
2. Missing metrics implementation
3. PromQL queries for Grafana dashboards
4. Alert rules configuration
5. Dashboard setup

---

## Prometheus Endpoints

### Structured Endpoints

All endpoints return Prometheus text format. Use these in Grafana datasource queries.

| Endpoint | Purpose | Metrics |
|----------|---------|---------|
| `GET /metrics` | All metrics (default) | Every metric tracked |
| `GET /prometheus/metrics/all` | Same as /metrics | Every metric tracked |
| `GET /prometheus/metrics/system` | System/HTTP metrics | `fastapi_requests_*`, `fastapi_exceptions_total`, `fastapi_app_info` |
| `GET /prometheus/metrics/providers` | Provider health | `provider_availability`, `provider_error_rate`, `provider_response_time_seconds`, `gatewayz_provider_health_score` |
| `GET /prometheus/metrics/models` | Model metrics | `model_inference_requests_total`, `model_inference_duration_seconds`, `tokens_used_total`, `credits_used_total` |
| `GET /prometheus/metrics/business` | Business metrics | `active_api_keys`, `subscription_count`, `trial_active`, `tokens_used_total`, `credits_used_total` |
| `GET /prometheus/metrics/performance` | Latency metrics | Histograms for latency distributions |
| `GET /prometheus/metrics/summary` | JSON summary | Summary statistics |
| `GET /prometheus/metrics/docs` | Documentation | Markdown with examples |

### Example Usage

```bash
# Get all provider metrics
curl http://localhost:8000/prometheus/metrics/providers

# Get model metrics for Grafana
curl "http://localhost:8000/prometheus/metrics/models"

# Get JSON summary
curl http://localhost:8000/prometheus/metrics/summary

# Get summary for specific category
curl "http://localhost:8000/prometheus/metrics/summary?category=providers"
```

---

## Newly Implemented Metrics

### 1. Provider Health Score
**Metric**: `gatewayz_provider_health_score{provider}`
**Type**: Gauge (0.0 - 1.0)
**Calculation**: `(availability * 0.4) + ((1 - error_rate) * 0.3) + (latency_score * 0.3)`

Used in Grafana for:
- Provider ranking dashboard
- Health overview panels
- Alert thresholds

**Example PromQL**:
```promql
# Top 5 healthiest providers
topk(5, gatewayz_provider_health_score)

# Unhealthy providers (< 0.7)
gatewayz_provider_health_score < 0.7

# Provider health over time
gatewayz_provider_health_score{provider="openrouter"}
```

### 2. Model Uptime (24h)
**Metric**: `gatewayz_model_uptime_24h{model}`
**Type**: Gauge (0.0 - 1.0)
**Calculation**: `successful_requests / total_requests (last 24h)`

Used for:
- SLA tracking
- Model reliability monitoring
- Historical performance

**Example PromQL**:
```promql
# Models with <99% uptime (SLA breach)
gatewayz_model_uptime_24h < 0.99

# Average uptime across all models
avg(gatewayz_model_uptime_24h)

# Uptime trend for specific model
gatewayz_model_uptime_24h{model="anthropic/claude-opus-4.5"}
```

### 3. Cost by Provider
**Metric**: `gatewayz_cost_by_provider{provider}`
**Type**: Counter (USD)
**Updates**: Per API call to provider

Used for:
- Cost analysis and optimization
- Provider cost comparison
- Budget tracking

**Example PromQL**:
```promql
# Cost comparison (top 5 providers)
topk(5, gatewayz_cost_by_provider)

# Cost rate (per minute)
rate(gatewayz_cost_by_provider[5m]) * 60

# Total cost for specific provider
gatewayz_cost_by_provider{provider="openrouter"}
```

### 4. Token Efficiency
**Metric**: `gatewayz_token_efficiency{model}`
**Type**: Gauge
**Calculation**: `total_output_tokens / total_input_tokens`

Used for:
- Model quality analysis
- Efficiency optimization
- Output quality monitoring

**Example PromQL**:
```promql
# Models with best token efficiency
topk(5, gatewayz_token_efficiency)

# Efficiency trends
gatewayz_token_efficiency{model="anthropic/claude-opus-4.5"}

# Average efficiency across models
avg(gatewayz_token_efficiency)
```

### 5. Circuit Breaker State
**Metric**: `gatewayz_circuit_breaker_state{provider, state}`
**Type**: Gauge (0 or 1)
**States**: "open", "closed", "half_open"

Used for:
- Reliability monitoring
- Failure tracking
- Recovery visualization

**Example PromQL**:
```promql
# All open circuit breakers
gatewayz_circuit_breaker_state{state="open"} == 1

# Providers with circuit breakers open
gatewayz_circuit_breaker_state{state="open"} == 1

# Circuit breaker state changes (rate)
rate(gatewayz_circuit_breaker_state[5m])
```

### 6. Detected Anomalies
**Metric**: `gatewayz_detected_anomalies{type}`
**Type**: Counter
**Types**: "latency_spike", "error_surge", "unusual_pattern"

Used for:
- Anomaly detection dashboard
- Alert generation
- System health monitoring

**Example PromQL**:
```promql
# Count of anomalies by type
gatewayz_detected_anomalies

# Anomaly rate (per hour)
rate(gatewayz_detected_anomalies[1h]) * 3600

# Latency spikes detected
increase(gatewayz_detected_anomalies{type="latency_spike"}[10m])
```

---

## PromQL Queries Library

### System Health

```promql
# Request throughput (requests/minute)
sum(rate(fastapi_requests_total[5m])) * 60

# Error rate (%)
(sum(rate(fastapi_requests_total{status_code=~"5.."}[5m])) /
 sum(rate(fastapi_requests_total[5m]))) * 100

# Average request latency (p50, p95, p99)
histogram_quantile(0.50, rate(fastapi_requests_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(fastapi_requests_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(fastapi_requests_duration_seconds_bucket[5m]))

# In-progress requests
sum(fastapi_requests_in_progress)

# Active exceptions
sum(rate(fastapi_exceptions_total[5m])) * 60
```

### Provider Health

```promql
# Provider availability (%)
provider_availability{provider="openrouter"} * 100

# Provider error rate (%)
provider_error_rate{provider="openrouter"} * 100

# Provider response time (p95, milliseconds)
histogram_quantile(0.95, rate(provider_response_time_seconds_bucket[5m])) * 1000

# Provider health score
gatewayz_provider_health_score{provider="openrouter"}

# Top 10 healthiest providers
topk(10, gatewayz_provider_health_score)

# Unhealthy providers alert threshold
gatewayz_provider_health_score < 0.7
```

### Model Performance

```promql
# Model request rate (requests/minute)
sum(rate(model_inference_requests_total[5m])) by (model) * 60

# Model error rate (%)
(sum(rate(model_inference_requests_total{status!="success"}[5m])) by (model) /
 sum(rate(model_inference_requests_total[5m])) by (model)) * 100

# Model latency (p95, milliseconds)
histogram_quantile(0.95, rate(model_inference_duration_seconds_bucket[5m])) by (model) * 1000

# Token usage (per minute)
sum(rate(tokens_used_total[5m])) by (model) * 60

# Token efficiency
gatewayz_token_efficiency{model="anthropic/claude-opus-4.5"}

# Model uptime (24h)
gatewayz_model_uptime_24h{model="anthropic/claude-opus-4.5"}
```

### Business Metrics

```promql
# Active subscriptions
subscription_count

# Active API keys
active_api_keys

# Active trials
trial_active

# Total tokens used (cumulative)
sum(tokens_used_total)

# Total credits used (cumulative)
sum(credits_used_total)

# Cost by provider (cumulative USD)
gatewayz_cost_by_provider

# Cost rate (USD per minute)
sum(rate(gatewayz_cost_by_provider[5m])) * 60

# Cost per provider (pie chart)
topk(10, gatewayz_cost_by_provider)
```

### Performance Analysis

```promql
# Request latency distribution
histogram_quantile(vector, rate(fastapi_requests_duration_seconds_bucket[5m]))
# Replace 'vector' with: 0.5, 0.95, 0.99, 0.999

# Inference latency by provider
histogram_quantile(0.95, rate(model_inference_duration_seconds_bucket[5m])) by (provider) * 1000

# Database query latency
histogram_quantile(0.95, rate(database_query_duration_seconds_bucket[5m])) by (operation) * 1000

# Cache hit rate (%)
(sum(rate(cache_hits_total[5m])) /
 (sum(rate(cache_hits_total[5m])) + sum(rate(cache_misses_total[5m])))) * 100

# Rate-limited requests
sum(rate(rate_limited_requests[5m])) * 60
```

---

## Alert Rules Configuration

Create these alert rules in `prometheus.yml`:

```yaml
groups:
  - name: gatewayz-alerts
    interval: 30s
    rules:
      # High error rate alert
      - alert: HighErrorRate
        expr: |
          (sum(rate(fastapi_requests_total{status_code=~"5.."}[5m])) /
           sum(rate(fastapi_requests_total[5m]))) > 0.05
        for: 5m
        annotations:
          summary: "Error rate > 5% for 5 minutes"

      # Provider health degraded
      - alert: ProviderHealthDegraded
        expr: gatewayz_provider_health_score < 0.7
        for: 10m
        annotations:
          summary: "Provider {{ $labels.provider }} health score below 0.7"

      # High latency
      - alert: HighLatency
        expr: |
          histogram_quantile(0.95, rate(fastapi_requests_duration_seconds_bucket[5m])) > 1.0
        for: 5m
        annotations:
          summary: "Request latency p95 > 1 second"

      # Circuit breaker open
      - alert: CircuitBreakerOpen
        expr: gatewayz_circuit_breaker_state{state="open"} == 1
        for: 2m
        annotations:
          summary: "Circuit breaker open for {{ $labels.provider }}"

      # High cost rate
      - alert: HighCostRate
        expr: sum(rate(gatewayz_cost_by_provider[5m])) * 60 > 100
        for: 10m
        annotations:
          summary: "Cost rate exceeds $100/hour"

      # Anomaly detected
      - alert: AnomalyDetected
        expr: increase(gatewayz_detected_anomalies[5m]) > 0
        annotations:
          summary: "Anomaly detected: {{ $labels.type }}"
```

---

## Grafana Dashboard Setup

### Add Prometheus Data Source

```bash
# Via Grafana UI:
1. Configuration → Data Sources → New
2. Name: "Prometheus"
3. URL: "http://prometheus:9090"
4. HTTP Method: GET
5. Click "Save & Test"
```

### Create Dashboard Variables

```json
{
  "variable": "provider",
  "type": "query",
  "label": "Provider",
  "datasource": "Prometheus",
  "query": "label_values(gatewayz_provider_health_score, provider)",
  "includeAll": true,
  "refresh": "time"
}

{
  "variable": "model",
  "type": "query",
  "label": "Model",
  "datasource": "Prometheus",
  "query": "label_values(model_inference_requests_total, model)",
  "includeAll": true,
  "refresh": "time"
}
```

### Sample Dashboard Panels

**Panel 1: Provider Health Heatmap**
```promql
gatewayz_provider_health_score{provider=~"$provider"}
```
Type: Heatmap or Stat panel
Thresholds: 0.7 (red), 0.85 (yellow), 1.0 (green)

**Panel 2: Error Rate Gauge**
```promql
(sum(rate(fastapi_requests_total{status_code=~"5.."}[5m])) /
 sum(rate(fastapi_requests_total[5m]))) * 100
```
Type: Gauge panel
Thresholds: 1% (green), 5% (yellow), 10% (red)

**Panel 3: Token Usage Over Time**
```promql
sum(rate(tokens_used_total[5m])) by (model) * 60
```
Type: Time series
Legend: `{{model}}`

**Panel 4: Cost Analysis**
```promql
topk(10, gatewayz_cost_by_provider)
```
Type: Pie chart or Bar graph

**Panel 5: Provider Availability**
```promql
provider_availability{provider=~"$provider"} * 100
```
Type: Stat panel
Unit: percent

---

## Prometheus Configuration

Full `prometheus.yml` example:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    cluster: 'gatewayz-production'
    environment: 'staging'

scrape_configs:
  - job_name: 'gatewayz-backend'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
    scrape_timeout: 10s
    scheme: http
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']

rule_files:
  - '/etc/prometheus/rules/*.yml'
```

---

## Loki Integration (Logging)

The logging stack sends structured logs to Loki asynchronously (non-blocking):

**Loki Data Source in Grafana**:
```bash
1. Configuration → Data Sources → New
2. Name: "Loki"
3. URL: "http://loki:3100"
4. Click "Save & Test"
```

**LogQL Queries**:
```logql
# All logs from gatewayz service
{job="gatewayz"}

# Error logs only
{job="gatewayz"} | json | level="error"

# Logs for specific provider
{job="gatewayz"} | json | provider="openrouter"

# Logs with trace correlation (for tracing to Tempo)
{job="gatewayz"} | json | trace_id="${trace_id}"
```

---

## Tempo Integration (Traces)

OpenTelemetry traces are captured for distributed tracing:

**Tempo Data Source**:
```bash
1. Configuration → Data Sources → New
2. Name: "Tempo"
3. URL: "http://tempo:3200"
4. Click "Save & Test"
```

**Trace Correlation**:
- Logs → Traces: Click "trace_id" in log to view trace
- Traces → Logs: View related logs from same trace

---

## Monitoring Strategy

### Health Check Frequency

| Category | Frequency | Purpose |
|----------|-----------|---------|
| System metrics | Every 15s | Prometheus scrape |
| Provider health | Every 30s | Background task |
| Model availability | Every 5m | Health check |
| Anomaly detection | Every minute | Real-time alerts |
| Cost aggregation | Every hour | Billing cycles |

### Alert Response Times

| Alert | SLA | Response |
|-------|-----|----------|
| High error rate | 5m | Immediate investigation |
| Provider unhealthy | 10m | Check provider status |
| High latency | 5m | Identify bottleneck |
| Circuit breaker open | 2m | Manual intervention |
| High cost | 10m | Review usage patterns |

### Dashboard Update Intervals

- Real-time: 5s refresh (system metrics)
- Hourly: 1m refresh (business metrics)
- Daily: 5m refresh (cost analysis)

---

## Troubleshooting

### Metrics Not Appearing

1. Verify endpoint responds:
   ```bash
   curl http://localhost:8000/prometheus/metrics/all | head -20
   ```

2. Check Prometheus scrape status:
   - Go to http://prometheus:9090/targets
   - Look for `gatewayz-backend` job
   - Should show "UP" status

3. Check metric names:
   ```bash
   curl http://localhost:8000/prometheus/metrics/all | grep "^# HELP"
   ```

### High Cardinality Issues

If you see "high cardinality" errors:

1. Check for unbounded labels (e.g., user_id):
   ```bash
   # Bad: Millions of unique values
   metric{user_id="123"}

   # Good: Fixed set of values
   metric{service="gatewayz"}
   ```

2. Use relabel configs to drop high-cardinality labels:
   ```yaml
   relabel_configs:
     - source_labels: [__name__]
       regex: '.*user_id.*'
       action: drop
   ```

### Memory Usage

If Prometheus uses too much memory:

1. Reduce `max_samples_per_send`:
   ```yaml
   metric_relabel_configs:
     - action: keep
       regex: 'fastapi_requests_total|model_inference_requests_total'
       source_labels: [__name__]
   ```

2. Reduce retention period:
   ```bash
   # In prometheus.yml
   --storage.tsdb.retention.time=7d  # Was 15d
   ```

---

## Next Steps

1. ✅ Deploy Prometheus endpoints
2. ✅ Configure Grafana dashboards
3. ✅ Set up alert rules
4. Monitor for metric quality
5. Optimize high-cardinality metrics
6. Integrate Loki for log correlation
7. Add Tempo for distributed tracing

---

## References

- [Prometheus Documentation](https://prometheus.io/docs/)
- [PromQL Operators](https://prometheus.io/docs/prometheus/latest/querying/operators/)
- [Grafana Dashboard Design](https://grafana.com/docs/grafana/latest/dashboards/)
- [Loki LogQL](https://grafana.com/docs/loki/latest/logql/)
- [Tempo Distributed Tracing](https://grafana.com/docs/tempo/latest/)
