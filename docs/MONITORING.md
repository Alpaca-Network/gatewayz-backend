# Gatewayz Monitoring & Observability Guide

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Metrics Collection](#metrics-collection)
4. [Alert Rules](#alert-rules)
5. [Monitoring API](#monitoring-api)
6. [Dashboards](#dashboards)
7. [Setup & Configuration](#setup--configuration)
8. [Troubleshooting](#troubleshooting)
9. [Best Practices](#best-practices)

---

## Overview

Gatewayz implements a comprehensive monitoring stack to track performance, reliability, costs, and user experience across 15+ AI providers and 100+ models.

### Key Capabilities

- **Real-time Metrics**: Sub-second granularity via Redis
- **Long-term Storage**: Historical data in PostgreSQL
- **Automated Alerts**: 34 alert rules across 11 categories
- **Health Monitoring**: Active + passive provider health tracking
- **Circuit Breakers**: Automatic failover for failing providers
- **Cost Tracking**: Real-time cost analysis and anomaly detection
- **Analytics**: Business intelligence on trials, conversions, efficiency

### Monitoring Stack

| Component | Purpose | Technology |
|-----------|---------|------------|
| **Metrics Collection** | Prometheus format metrics | prometheus_client |
| **Real-time Storage** | Short-term metrics (1-2 hours) | Redis |
| **Long-term Storage** | Historical data, analytics | PostgreSQL (Supabase) |
| **Visualization** | Dashboards, graphs | Grafana Cloud |
| **Alerting** | Automated notifications | Prometheus Alert Manager |
| **Error Tracking** | Exception monitoring | Sentry |
| **Distributed Tracing** | Request tracing | Tempo (optional) |
| **Log Aggregation** | Centralized logging | Loki (optional) |

---

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Requests                             │
│                  (Chat, Messages, Images)                       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Metrics Recording                             │
│  ┌───────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Prometheus    │  │ Redis        │  │ Health Monitor     │  │
│  │ Metrics       │  │ Metrics      │  │ (Active+Passive)   │  │
│  └───────────────┘  └──────────────┘  └────────────────────┘  │
└────────────┬───────────────┬────────────────────┬──────────────┘
             │               │                    │
             │               │                    ▼
             │               │          ┌──────────────────────┐
             │               │          │ Circuit Breakers     │
             │               │          │ (Provider Failover)  │
             │               │          └──────────────────────┘
             │               │
             ▼               ▼
┌──────────────────┐  ┌──────────────────────────────┐
│ Prometheus       │  │ Redis (TTL: 2 hours)         │
│ /metrics endpoint│  │ - Request counters           │
│                  │  │ - Latency percentiles        │
│                  │  │ - Error tracking             │
│                  │  │ - Health scores              │
└────────┬─────────┘  └──────────┬───────────────────┘
         │                       │
         │                       ▼
         │            ┌───────────────────────────────┐
         │            │ Hourly Aggregation Job        │
         │            │ (Cron/Background Task)        │
         │            └──────────┬────────────────────┘
         │                       │
         ▼                       ▼
┌────────────────────────────────────────────────────┐
│              Grafana Cloud                         │
│  ┌──────────────────┐  ┌─────────────────────────┐│
│  │ Prometheus       │  │ PostgreSQL (Supabase)   ││
│  │ (Metrics)        │  │ - metrics_hourly_       ││
│  │                  │  │   aggregates            ││
│  │ Remote Write     │  │ - Materialized views    ││
│  └────────┬─────────┘  └─────────┬───────────────┘│
│           │                      │                 │
│           ▼                      ▼                 │
│  ┌──────────────────────────────────────────────┐ │
│  │            Dashboards & Alerts               │ │
│  └──────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────┘
```

### Components

#### 1. Prometheus Metrics (`src/services/prometheus_metrics.py`)

Exposes metrics in Prometheus format at `/metrics`:

```python
# Example metrics
model_inference_requests_total{provider="openrouter",model="gpt-4",status="success"} 1000
model_inference_duration_seconds{provider="openrouter",model="gpt-4",quantile="0.95"} 2.5
tokens_used_total{provider="openrouter",model="gpt-4",token_type="input"} 50000
credits_used_total{provider="openrouter",model="gpt-4"} 12.50
```

#### 2. Redis Metrics (`src/services/redis_metrics.py`)

Real-time metrics storage for dashboards:

- **Request counters**: Hourly aggregates per provider
- **Latency tracking**: Sorted sets with TTL
- **Error tracking**: Last 100 errors per provider
- **Health scores**: 0-100 score per provider
- **Circuit breaker state**: Sync with availability service

#### 3. Database Metrics (`supabase/migrations/20251127000000_add_metrics_hourly_aggregates.sql`)

Long-term storage and analytics:

- **Table**: `metrics_hourly_aggregates`
- **Retention**: Unlimited (managed via database policies)
- **Indexes**: Optimized for time-series queries
- **Materialized View**: `provider_stats_24h` for fast lookups

#### 4. Metrics Aggregator (`src/services/metrics_aggregator.py`)

Hourly job to transfer Redis → Database:

- Runs every 60 minutes (configurable)
- Calculates latency percentiles (P50, P95, P99)
- Aggregates counters and costs
- Cleans up old Redis data
- Refreshes materialized views

#### 5. Analytics Service (`src/services/analytics.py`)

Business intelligence and operational analytics:

- Trial funnel metrics
- Cost analysis by provider
- Latency trends
- Error rates by model
- Token efficiency
- Provider comparison
- Anomaly detection

#### 6. Monitoring API (`src/routes/monitoring.py`)

REST API for accessing metrics:

- Provider health status
- Recent errors
- Real-time statistics
- Circuit breaker states
- Latency percentiles
- Anomalies
- Business metrics

---

## Metrics Collection

### Automatic Instrumentation

Metrics are automatically collected from:

1. **Chat Completions** (`src/routes/chat.py`)
   - Inference requests (success/failure)
   - Latency (duration, TTFB)
   - Token usage (input/output)
   - Cost (credits)
   - Provider health (passive monitoring)

2. **Database Queries** (`src/services/prometheus_metrics.py`)
   - Query duration per table/operation
   - Error rates
   - Connection pool usage

3. **HTTP Requests** (Middleware)
   - Request counts by endpoint/status
   - Request duration
   - Response size

4. **Cache Operations**
   - Hit/miss rates
   - Cache size
   - Eviction counts

5. **Rate Limiting**
   - Rate limit hits
   - Blocked requests
   - Per-user limits

### Manual Instrumentation

Example: Recording custom metrics

```python
from src.services.prometheus_metrics import (
    model_inference_requests,
    model_inference_duration,
    tokens_used,
    credits_used
)
from src.services.redis_metrics import get_redis_metrics
from src.services.model_health_monitor import capture_model_health

# Prometheus metrics
model_inference_requests.labels(
    provider="openrouter",
    model="gpt-4",
    status="success"
).inc()

model_inference_duration.labels(
    provider="openrouter",
    model="gpt-4"
).observe(elapsed_seconds)

tokens_used.labels(
    provider="openrouter",
    model="gpt-4",
    token_type="input"
).inc(prompt_tokens)

credits_used.labels(
    provider="openrouter",
    model="gpt-4"
).inc(cost)

# Redis metrics (real-time)
redis_metrics = get_redis_metrics()
await redis_metrics.record_request(
    provider="openrouter",
    model="gpt-4",
    latency_ms=int(elapsed_seconds * 1000),
    success=True,
    cost=cost,
    tokens_input=prompt_tokens,
    tokens_output=completion_tokens
)

# Health monitoring (background)
capture_model_health(
    provider="openrouter",
    model="gpt-4",
    response_time_ms=int(elapsed_seconds * 1000),
    health_status="healthy"
)
```

---

## Alert Rules

### Alert Configuration

Alerts are defined in `prometheus-alerts.yml` and loaded into Grafana Cloud or Prometheus.

### Alert Categories

#### 1. Model Inference Alerts (5 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **HighModelErrorRate** | >10% errors for 5m | Critical | Model error rate exceeded threshold |
| **ModelLatencyHigh** | P95 > 30s for 5m | Warning | High latency detected |
| **ModelLatencyCritical** | P95 > 60s for 5m | Critical | Critical latency, users affected |
| **NoInferenceRequests** | 0 requests for 15m | Warning | Provider receiving no traffic |
| **TokenUsageSpike** | >2x avg for 10m | Warning | Unusual token consumption |

#### 2. Provider Health Alerts (4 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **ProviderUnhealthy** | Health < 50 for 5m | Critical | Provider unhealthy, circuit breakers opening |
| **ProviderDegraded** | Health 50-80 for 10m | Warning | Provider degraded, performance impacted |
| **CircuitBreakerOpen** | State = OPEN for 2m | Critical | Circuit breaker opened, traffic blocked |
| **MultipleCircuitBreakersOpen** | >3 open for 5m | Critical | Major provider outage detected |

#### 3. Database Alerts (3 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **DatabaseQuerySlow** | P95 > 2s for 5m | Warning | Slow database queries detected |
| **DatabaseQueryCritical** | P95 > 5s for 5m | Critical | Critical database slowness |
| **HighDatabaseErrorRate** | >5% errors for 5m | Critical | High database error rate |

#### 4. Cache Alerts (2 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **LowCacheHitRate** | <70% for 15m | Warning | Cache performance degraded |
| **CacheConnectionFailure** | >0 failures for 2m | Critical | Redis connection failures |

#### 5. Rate Limiting Alerts (2 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **HighRateLimitHitRate** | >10% for 10m | Warning | High rate of rate limiting |
| **RateLimitSystemOverload** | >25% for 5m | Critical | System under attack or misconfigured client |

#### 6. Business Metrics Alerts (1 rule)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **CreditCostSpike** | >2x avg for 15m | Warning | Unusual credit burn rate |

#### 7. HTTP/API Alerts (4 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **HighHTTP5xxRate** | >5% for 5m | Critical | High rate of server errors |
| **HighHTTP4xxRate** | >20% for 10m | Warning | High rate of client errors |
| **APIEndpointDown** | 0 requests for 5m | Critical | Critical endpoint receiving no traffic |
| **HighRequestLatency** | P95 > 10s for 5m | Warning | High API latency |

#### 8. System Health Alerts (3 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **HighMemoryUsage** | >90% for 10m | Critical | High memory usage |
| **HighCPUUsage** | >80% for 10m | Warning | High CPU usage |
| **TooManyOpenConnections** | >80 for 5m | Warning | Connection pool exhausted |

#### 9. Anomaly Detection Alerts (2 rules)

| Alert | Threshold | Severity | Description |
|-------|-----------|----------|-------------|
| **TrafficAnomalyDetected** | >3x avg for 5m | Warning | Unusual traffic spike (DDoS?) |
| **UnusualErrorPattern** | >5x avg for 5m | Critical | Unusual error spike (deployment issue?) |

### Alert Routing

Configure alert routing in Grafana Cloud or Alertmanager:

```yaml
# Example alertmanager.yml
route:
  receiver: 'default'
  routes:
    - match:
        severity: critical
      receiver: 'pagerduty'
      continue: true
    - match:
        severity: warning
      receiver: 'slack'
    - match:
        component: billing
      receiver: 'finance-team'

receivers:
  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: '<your-pagerduty-key>'
  - name: 'slack'
    slack_configs:
      - api_url: '<your-slack-webhook>'
        channel: '#alerts'
  - name: 'finance-team'
    email_configs:
      - to: 'finance@gatewayz.ai'
```

---

## Monitoring API

### Base URL

```
https://api.gatewayz.ai/api/monitoring
```

### Authentication

Monitoring endpoints may require authentication depending on deployment configuration. Use an API key or admin token.

### Endpoints

#### Provider Health

**Get All Provider Health Scores**
```http
GET /api/monitoring/health
```

Response:
```json
[
  {
    "provider": "openrouter",
    "health_score": 95.0,
    "status": "healthy",
    "last_updated": "2025-11-27T14:30:00Z"
  },
  {
    "provider": "fireworks",
    "health_score": 72.0,
    "status": "degraded",
    "last_updated": "2025-11-27T14:30:00Z"
  }
]
```

**Get Provider Health Score**
```http
GET /api/monitoring/health/{provider}
```

#### Error Tracking

**Get Recent Errors**
```http
GET /api/monitoring/errors/{provider}?limit=100
```

Response:
```json
[
  {
    "model": "gpt-4",
    "error": "Rate limit exceeded",
    "timestamp": 1732716000.0,
    "latency_ms": 1500
  }
]
```

#### Statistics

**Get Real-time Statistics**
```http
GET /api/monitoring/stats/realtime?hours=1
```

Response:
```json
{
  "timestamp": "2025-11-27T14:30:00Z",
  "providers": {
    "openrouter": {
      "total_requests": 1000,
      "total_cost": 12.50,
      "health_score": 95.0
    }
  },
  "total_requests": 1000,
  "total_cost": 12.50,
  "avg_health_score": 95.0
}
```

**Get Hourly Stats**
```http
GET /api/monitoring/stats/hourly/{provider}?hours=24
```

#### Circuit Breakers

**Get All Circuit Breaker States**
```http
GET /api/monitoring/circuit-breakers
```

Response:
```json
[
  {
    "provider": "openrouter",
    "model": "gpt-4",
    "state": "CLOSED",
    "failure_count": 0,
    "is_available": true,
    "last_updated": 1732716000.0
  },
  {
    "provider": "fireworks",
    "model": "llama-3-70b",
    "state": "OPEN",
    "failure_count": 5,
    "is_available": false,
    "last_updated": 1732716000.0
  }
]
```

#### Provider Comparison

**Compare All Providers**
```http
GET /api/monitoring/providers/comparison
```

Response:
```json
{
  "timestamp": "2025-11-27T14:30:00Z",
  "providers": [
    {
      "provider": "openrouter",
      "total_requests": 10000,
      "successful_requests": 9500,
      "failed_requests": 500,
      "avg_latency_ms": 500.0,
      "total_cost": 125.50,
      "total_tokens": 750000,
      "avg_error_rate": 0.05,
      "unique_models": 15,
      "success_rate": 0.95
    }
  ],
  "total_providers": 1
}
```

#### Latency

**Get Latency Percentiles**
```http
GET /api/monitoring/latency/{provider}/{model}?percentiles=50,95,99
```

Response:
```json
{
  "provider": "openrouter",
  "model": "gpt-4",
  "count": 100,
  "avg": 500.0,
  "p50": 450.0,
  "p95": 800.0,
  "p99": 1200.0
}
```

**Get Latency Trends**
```http
GET /api/monitoring/latency-trends/{provider}?hours=24
```

#### Anomalies

**Detect Anomalies**
```http
GET /api/monitoring/anomalies
```

Response:
```json
{
  "timestamp": "2025-11-27T14:30:00Z",
  "anomalies": [
    {
      "type": "cost_spike",
      "provider": "openrouter",
      "hour": "2025-11-27:14",
      "value": 50.0,
      "expected": 12.5,
      "severity": "warning"
    }
  ],
  "total_count": 1,
  "critical_count": 0,
  "warning_count": 1
}
```

#### Business Metrics

**Get Trial Analytics**
```http
GET /api/monitoring/trial-analytics
```

Response:
```json
{
  "timestamp": "2025-11-27T14:30:00Z",
  "signups": 1000,
  "started_trial": 750,
  "converted": 50,
  "conversion_rate": 5.0,
  "activation_rate": 75.0,
  "avg_time_to_conversion_days": 7.5
}
```

**Get Cost Analysis**
```http
GET /api/monitoring/cost-analysis?days=7
```

**Get Error Rates by Model**
```http
GET /api/monitoring/error-rates?hours=24
```

**Get Token Efficiency**
```http
GET /api/monitoring/token-efficiency/{provider}/{model}
```

---

## Dashboards

### Grafana Cloud Dashboards

Create the following dashboards in Grafana Cloud:

#### 1. Inference Overview Dashboard

**Panels:**
- Total requests (time series)
- Error rate by provider (time series)
- P95 latency by provider (time series)
- Cost per hour (time series)
- Token usage by type (time series)
- Top 10 models by requests (bar chart)

**PromQL Queries:**
```promql
# Total requests
sum(rate(model_inference_requests_total[5m]))

# Error rate by provider
sum(rate(model_inference_requests_total{status="error"}[5m])) by (provider)
/ sum(rate(model_inference_requests_total[5m])) by (provider)

# P95 latency by provider
histogram_quantile(0.95,
  sum(rate(model_inference_duration_seconds_bucket[5m])) by (provider, le)
)

# Cost per hour
sum(increase(credits_used_total[1h])) by (provider)
```

#### 2. Provider Health Dashboard

**Panels:**
- Provider health scores (gauge)
- Circuit breaker states (state timeline)
- Failure counts (time series)
- Recovery times (histogram)
- Provider comparison table

**PromQL Queries:**
```promql
# Health scores
provider_health_score

# Circuit breaker states
circuit_breaker_state

# Failure counts
circuit_breaker_failure_count
```

#### 3. Database Performance Dashboard

**Panels:**
- Query duration by table (time series)
- Queries per second by operation (time series)
- Error rate by table (time series)
- Active connections (gauge)
- Slow query log (table)

**PromQL Queries:**
```promql
# Query duration P95 by table
histogram_quantile(0.95,
  sum(rate(database_query_duration_seconds_bucket[5m])) by (table, le)
)

# Queries per second
sum(rate(database_query_total[5m])) by (operation)

# Active connections
active_database_connections
```

#### 4. Business Metrics Dashboard

**Panels:**
- Trial conversion funnel (funnel chart)
- Cost by provider (pie chart)
- Revenue vs cost (time series)
- MAU/DAU (time series)
- Top users by spend (table)

**PromQL Queries:**
```promql
# Total cost
sum(increase(credits_used_total[1d]))

# Cost by provider
sum(increase(credits_used_total[1d])) by (provider)
```

#### 5. Anomaly Detection Dashboard

**Panels:**
- Detected anomalies (annotations)
- Cost anomalies (time series with anomaly highlighting)
- Latency anomalies (time series with anomaly highlighting)
- Error rate anomalies (time series with anomaly highlighting)
- Anomaly summary table

### Dashboard JSON

Export dashboard JSON from Grafana Cloud and commit to repo:

```bash
# Export dashboard
curl -H "Authorization: Bearer <api-key>" \
  https://grafana.com/api/dashboards/uid/<dashboard-uid> \
  > dashboards/inference-overview.json

# Import dashboard
curl -X POST -H "Authorization: Bearer <api-key>" \
  -H "Content-Type: application/json" \
  -d @dashboards/inference-overview.json \
  https://grafana.com/api/dashboards/db
```

---

## Setup & Configuration

### Prerequisites

- PostgreSQL (Supabase)
- Redis (for real-time metrics)
- Grafana Cloud account (or self-hosted Prometheus + Grafana)

### Environment Variables

Add to `.env`:

```bash
# Grafana Cloud
GRAFANA_CLOUD_ENABLED=true
GRAFANA_PROMETHEUS_REMOTE_WRITE_URL=https://prometheus-prod-xx-prod-us-central-x.grafana.net/api/prom/push
GRAFANA_PROMETHEUS_USERNAME=123456
GRAFANA_PROMETHEUS_API_KEY=glc_your-grafana-cloud-api-key

# Redis
REDIS_URL=redis://localhost:6379
REDIS_ENABLED=true
REDIS_MAX_CONNECTIONS=50

# Metrics Aggregation
METRICS_AGGREGATION_ENABLED=true
METRICS_AGGREGATION_INTERVAL_MINUTES=60
METRICS_REDIS_RETENTION_HOURS=2

# Sentry (Error Monitoring)
SENTRY_DSN=https://your-sentry-dsn@sentry.io/your-project-id
SENTRY_ENABLED=true
SENTRY_ENVIRONMENT=production
```

### Database Migration

Run the metrics aggregation migration:

```bash
# Using Supabase CLI
supabase migration up

# Or apply directly
psql $DATABASE_URL -f supabase/migrations/20251127000000_add_metrics_hourly_aggregates.sql
```

Verify tables:

```sql
SELECT * FROM metrics_hourly_aggregates LIMIT 10;
SELECT * FROM provider_stats_24h;
```

### Redis Setup

Install and start Redis:

```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis

# Docker
docker run -d -p 6379:6379 redis:latest
```

Verify Redis connection:

```bash
redis-cli ping
# Should return: PONG
```

### Metrics Aggregation Job

Run the hourly aggregation job:

**Option 1: Cron Job**

```bash
# Add to crontab (every hour at :00)
0 * * * * cd /path/to/gatewayz-backend && python -m src.services.metrics_aggregator

# Or using systemd timer (recommended)
sudo systemctl enable gatewayz-metrics-aggregator.timer
sudo systemctl start gatewayz-metrics-aggregator.timer
```

**Option 2: Background Task**

Add to your FastAPI startup:

```python
# src/services/startup.py
from src.services.metrics_aggregator import get_metrics_aggregator

async def lifespan(app: FastAPI):
    # Start metrics aggregation in background
    aggregator = get_metrics_aggregator()
    asyncio.create_task(aggregator.run_periodic_aggregation(interval_minutes=60))

    yield

    # Cleanup on shutdown
    logger.info("Shutting down metrics aggregation...")
```

**Option 3: Manual Run**

```bash
# Run once
python -m src.services.metrics_aggregator

# Run periodic
python -m src.services.metrics_aggregator --periodic
```

### Grafana Cloud Setup

1. **Create Account**: https://grafana.com/auth/sign-up
2. **Create Stack**: Select a region (us-central-1, eu-west-1, etc.)
3. **Get Credentials**:
   - Navigate to **Configuration** > **Data Sources** > **Prometheus**
   - Copy **Remote Write URL**
   - Copy **Username** (instance ID)
   - Generate **API Key** (Access Policies > Create token)

4. **Configure Remote Write**:

   Update `prometheus.yml` (if self-hosting Prometheus):

   ```yaml
   remote_write:
     - url: https://prometheus-prod-xx-prod-us-central-x.grafana.net/api/prom/push
       basic_auth:
         username: 123456
         password: glc_your-grafana-cloud-api-key
   ```

   Or use Grafana Agent for auto-discovery.

5. **Load Alert Rules**:

   ```bash
   # Upload to Grafana Cloud
   curl -X POST \
     -H "Authorization: Bearer glc_your-api-key" \
     -H "Content-Type: application/yaml" \
     --data-binary @prometheus-alerts.yml \
     https://grafana.com/api/v1/provisioning/alert-rules
   ```

6. **Create Dashboards**: Import or create dashboards (see [Dashboards](#dashboards))

### Sentry Setup

1. **Create Project**: https://sentry.io/signup/
2. **Get DSN**: Project Settings > Client Keys (DSN)
3. **Configure**:

   ```bash
   SENTRY_DSN=https://your-key@sentry.io/your-project-id
   SENTRY_ENABLED=true
   SENTRY_ENVIRONMENT=production
   ```

4. **Verify**: Trigger test error at `/sentry-debug`

---

## Troubleshooting

### Metrics Not Appearing in Grafana

**Check 1: Prometheus Endpoint**

```bash
curl http://localhost:8000/metrics
```

Expected output:
```
# HELP model_inference_requests_total Total model inference requests
# TYPE model_inference_requests_total counter
model_inference_requests_total{provider="openrouter",model="gpt-4",status="success"} 1000.0
```

**Check 2: Remote Write**

Verify Grafana Cloud credentials:

```bash
curl -u $GRAFANA_PROMETHEUS_USERNAME:$GRAFANA_PROMETHEUS_API_KEY \
  $GRAFANA_PROMETHEUS_REMOTE_WRITE_URL
```

**Check 3: Firewall**

Ensure outbound HTTPS (443) is allowed for Grafana Cloud endpoints.

### Redis Connection Failures

**Check 1: Redis Running**

```bash
redis-cli ping
```

**Check 2: Connection String**

```bash
redis-cli -u $REDIS_URL ping
```

**Check 3: Firewall**

Ensure Redis port (6379) is accessible.

**Check 4: Fallback**

If Redis is unavailable, metrics will degrade gracefully (Redis metrics skipped, but Prometheus still works).

### Database Slow Queries

**Check 1: Missing Indexes**

```sql
SELECT * FROM pg_stat_user_tables WHERE schemaname = 'public';
```

Ensure indexes exist on:
- `metrics_hourly_aggregates(hour, provider, model)`
- `metrics_hourly_aggregates(hour DESC)`

**Check 2: Vacuum**

```sql
VACUUM ANALYZE metrics_hourly_aggregates;
```

**Check 3: Query Plans**

```sql
EXPLAIN ANALYZE
SELECT * FROM metrics_hourly_aggregates
WHERE hour >= NOW() - INTERVAL '24 hours';
```

### Alerts Not Firing

**Check 1: Alert Rules Loaded**

Verify in Grafana Cloud:
- Navigate to **Alerting** > **Alert rules**
- Confirm rules are present and enabled

**Check 2: Alert Evaluation**

Check alert state (Pending, Firing, Normal):
- Navigate to **Alerting** > **Alert rules**
- Click on rule to see evaluation history

**Check 3: Notification Channels**

Verify notification channels configured:
- Navigate to **Alerting** > **Contact points**
- Test notification

**Check 4: Thresholds**

Review alert query and thresholds:
- May need to adjust for your traffic patterns

### High Memory Usage

**Check 1: Prometheus Metrics**

Check `/metrics` size:

```bash
curl -s http://localhost:8000/metrics | wc -l
```

If very large (>100k lines), consider:
- Reducing metric cardinality (fewer labels)
- Sampling high-cardinality metrics

**Check 2: Redis Memory**

```bash
redis-cli info memory
```

If high:
- Reduce TTL (currently 2 hours)
- Implement LRU eviction policy

**Check 3: Database Queries**

Monitor active connections:

```sql
SELECT count(*) FROM pg_stat_activity;
```

If high (>50):
- Check for connection leaks
- Reduce connection pool size

---

## Best Practices

### 1. Metric Naming

Follow Prometheus naming conventions:

- Use lowercase with underscores: `model_inference_requests_total`
- Suffix counters with `_total`: `credits_used_total`
- Suffix histograms with `_bucket`, `_sum`, `_count`
- Use descriptive labels: `{provider="openrouter",model="gpt-4"}`

### 2. Label Cardinality

Avoid high-cardinality labels:

**Bad:**
```python
requests.labels(user_id=user.id)  # Thousands of unique users
```

**Good:**
```python
requests.labels(subscription_tier=user.tier)  # 3-5 unique tiers
```

### 3. Alert Fatigue

Avoid noisy alerts:

- Set appropriate thresholds based on actual traffic
- Use `for` clauses to wait before firing (e.g., `for: 5m`)
- Group related alerts (e.g., "Provider Outage" instead of 10 model alerts)
- Route low-priority alerts to separate channels

### 4. Dashboard Design

Create actionable dashboards:

- Use consistent time ranges across panels
- Add annotations for deployments/incidents
- Use template variables for filtering (provider, model, etc.)
- Include links to runbooks in panel descriptions

### 5. Data Retention

Balance storage vs. granularity:

- **Redis**: 1-2 hours (real-time only)
- **Prometheus**: 15 days (scraped metrics)
- **PostgreSQL**: Unlimited (hourly aggregates)

Periodically archive old data:

```sql
-- Archive data older than 90 days
DELETE FROM metrics_hourly_aggregates
WHERE hour < NOW() - INTERVAL '90 days';
```

### 6. Cost Optimization

Reduce monitoring costs:

1. **Sentry**: Use adaptive sampling (already configured)
   - Critical endpoints: 20%
   - Other endpoints: 10%
   - Health checks: 0%

2. **Grafana Cloud**: Use remote write filtering
   ```yaml
   remote_write:
     - url: ...
       write_relabel_configs:
         - source_labels: [__name__]
           regex: 'go_.*|process_.*'  # Drop Go runtime metrics
           action: drop
   ```

3. **Redis**: Reduce TTL if real-time data not needed

4. **Database**: Use materialized views for expensive queries

### 7. Security

Protect monitoring data:

- **API Authentication**: Require API keys for monitoring endpoints
- **Grafana Access**: Use RBAC to restrict dashboard access
- **Metrics Endpoint**: Consider IP allowlist for `/metrics`
- **Sensitive Data**: Don't include PII in labels (user IDs, emails, etc.)

### 8. Runbooks

Create runbooks for common incidents:

**Example: High Error Rate Runbook**

1. **Identify affected provider/model**:
   - Check Grafana dashboard or `/api/monitoring/errors/{provider}`

2. **Check circuit breaker state**:
   - View `/api/monitoring/circuit-breakers/{provider}`

3. **Review recent errors**:
   - Inspect error messages for patterns (rate limits, timeouts, etc.)

4. **Mitigation**:
   - If provider issue: Circuit breaker will auto-failover
   - If rate limit: Contact provider for limit increase
   - If timeout: Check network connectivity or provider status page

5. **Resolution**:
   - Monitor health score recovery
   - Verify circuit breaker closes after recovery
   - Post-mortem: Adjust alert thresholds if needed

---

## Appendix

### Metric Reference

Complete list of available metrics:

**Inference Metrics:**
- `model_inference_requests_total{provider, model, status}`
- `model_inference_duration_seconds{provider, model}`
- `tokens_used_total{provider, model, token_type}`
- `credits_used_total{provider, model}`

**Database Metrics:**
- `database_query_total{table, operation, status}`
- `database_query_duration_seconds{table, operation}`
- `active_database_connections`

**Cache Metrics:**
- `cache_hits_total{cache_type}`
- `cache_misses_total{cache_type}`
- `cache_total{cache_type}`
- `redis_connection_failures_total`

**HTTP Metrics:**
- `http_requests_total{method, endpoint, status}`
- `http_request_duration_seconds{method, endpoint}`

**Provider Health:**
- `provider_health_score{provider}`
- `circuit_breaker_state{provider, model, state}`
- `circuit_breaker_failure_count{provider, model}`

**Rate Limiting:**
- `rate_limit_hits_total{limit_type}`

**Business Metrics:**
- `trial_signups_total`
- `trial_conversions_total`
- `user_credits_remaining{user_id}`
- `active_subscriptions_total{tier}`

### API Reference

See [Monitoring API](#monitoring-api) section for full endpoint documentation.

### Contributing

To add new metrics:

1. Define metric in `src/services/prometheus_metrics.py`
2. Instrument code to record metric
3. Add alert rule to `prometheus-alerts.yml`
4. Update dashboard to visualize metric
5. Document metric in this guide

### Support

For questions or issues:

- **Documentation**: https://docs.gatewayz.ai
- **GitHub Issues**: https://github.com/gatewayz/gatewayz-backend/issues
- **Discord**: https://discord.gg/gatewayz
- **Email**: support@gatewayz.ai

---

**Last Updated**: 2025-11-27
**Version**: 2.0.3
