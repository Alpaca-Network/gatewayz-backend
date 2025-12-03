

# Health Monitoring System for 10,000+ Models

## Overview

The Gatewayz Health Monitoring System provides comprehensive, scalable monitoring for 10,000+ AI models across 20+ gateways and providers. The system uses intelligent tiered monitoring, database persistence, and real-time alerting to ensure high availability.

## Table of Contents

- [Architecture](#architecture)
- [Monitoring Tiers](#monitoring-tiers)
- [Database Schema](#database-schema)
- [API Endpoints](#api-endpoints)
- [Alerting](#alerting)
- [Setup & Configuration](#setup--configuration)
- [Usage](#usage)
- [Performance Considerations](#performance-considerations)
- [Troubleshooting](#troubleshooting)

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                     Intelligent Health Monitor                  │
│  • Tiered scheduling (critical/popular/standard/on-demand)     │
│  • Priority queue for efficient checking                       │
│  • Redis coordination for distributed deployments              │
│  • Circuit breaker pattern                                     │
└────────┬────────────────────────────────────────────┬──────────┘
         │                                            │
    ┌────▼─────────────────┐              ┌──────────▼──────────┐
    │  Health Check         │              │  Alerting Service   │
    │  Workers              │              │  • Email            │
    │  • Concurrent checks  │              │  • Slack            │
    │  • Timeout handling   │              │  • Discord          │
    │  • Retry logic        │              │  • PagerDuty        │
    └────┬──────────────────┘              │  • Webhooks         │
         │                                 └─────────────────────┘
    ┌────▼──────────────────────────────────────────────────────┐
    │              PostgreSQL (Supabase)                        │
    │  Tables:                                                  │
    │  • model_health_tracking - Current health status         │
    │  • model_health_history - Time-series data              │
    │  • model_health_incidents - Incident tracking           │
    │  • model_health_aggregates - Pre-computed stats         │
    │  Views:                                                   │
    │  • model_status_current - Current status for UI         │
    │  • provider_health_current - Provider aggregations      │
    └───────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Scheduler** queries database for models due for health checks
2. **Priority Queue** orders models by:
   - Monitoring tier (critical first)
   - Usage frequency
   - Time since last check
   - Current health status
3. **Workers** perform concurrent health checks with rate limiting
4. **Results** are persisted to database (tracking, history, incidents)
5. **Alerting** triggers notifications for critical issues
6. **API** serves current status to frontend/status page

## Monitoring Tiers

The system uses intelligent tiering to efficiently monitor 10,000+ models:

### Tier 1: Critical (Top 5% by usage)
- **Check Interval**: Every 5 minutes
- **Criteria**: Models with highest 24h usage
- **Priority**: Highest
- **Use Case**: Production-critical models (GPT-4, Claude, etc.)

### Tier 2: Popular (Next 20%)
- **Check Interval**: Every 30 minutes
- **Criteria**: Moderately used models
- **Priority**: High
- **Use Case**: Frequently requested models

### Tier 3: Standard (Remaining 75%)
- **Check Interval**: Every 2 hours
- **Criteria**: Models with some usage
- **Priority**: Normal
- **Use Case**: Less frequently used models

### Tier 4: On-Demand
- **Check Interval**: Every 4 hours (or when requested)
- **Criteria**: Models with no recent usage
- **Priority**: Low
- **Use Case**: Rarely used or new models

**Automatic Tier Updates**: The system automatically adjusts model tiers based on usage patterns every hour using the `update_model_tier()` database function.

## Database Schema

### model_health_tracking

Main table tracking current health status for each model:

```sql
CREATE TABLE model_health_tracking (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT,
    monitoring_tier TEXT,              -- critical/popular/standard/on_demand
    last_status TEXT,                  -- success/error/timeout/rate_limited
    last_response_time_ms NUMERIC,
    last_called_at TIMESTAMP,
    call_count INTEGER,
    success_count INTEGER,
    error_count INTEGER,
    average_response_time_ms NUMERIC,
    consecutive_failures INTEGER,
    consecutive_successes INTEGER,
    circuit_breaker_state TEXT,        -- closed/open/half_open
    uptime_percentage_24h NUMERIC,
    uptime_percentage_7d NUMERIC,
    uptime_percentage_30d NUMERIC,
    last_incident_at TIMESTAMP,
    last_success_at TIMESTAMP,
    last_failure_at TIMESTAMP,
    next_check_at TIMESTAMP,
    check_interval_seconds INTEGER,
    priority_score NUMERIC,
    usage_count_24h INTEGER,
    usage_count_7d INTEGER,
    usage_count_30d INTEGER,
    is_enabled BOOLEAN,
    metadata JSONB,
    PRIMARY KEY (provider, model)
);
```

### model_health_history

Time-series history for trend analysis:

```sql
CREATE TABLE model_health_history (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT,
    model TEXT,
    gateway TEXT,
    checked_at TIMESTAMP,
    status TEXT,
    response_time_ms NUMERIC,
    error_message TEXT,
    http_status_code INTEGER,
    circuit_breaker_state TEXT,
    metadata JSONB
);
```

### model_health_incidents

Incident tracking and management:

```sql
CREATE TABLE model_health_incidents (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT,
    model TEXT,
    gateway TEXT,
    incident_type TEXT,              -- outage/degradation/timeout
    severity TEXT,                   -- critical/high/medium/low
    started_at TIMESTAMP,
    resolved_at TIMESTAMP,
    duration_seconds INTEGER,
    error_message TEXT,
    error_count INTEGER,
    affected_requests INTEGER,
    status TEXT,                     -- active/resolved/acknowledged
    resolution_notes TEXT,
    metadata JSONB
);
```

### model_health_aggregates

Pre-computed statistics for fast querying:

```sql
CREATE TABLE model_health_aggregates (
    provider TEXT,
    model TEXT,
    gateway TEXT,
    aggregation_period TEXT,         -- hour/day/week/month
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    total_checks INTEGER,
    successful_checks INTEGER,
    failed_checks INTEGER,
    avg_response_time_ms NUMERIC,
    p50_response_time_ms NUMERIC,
    p95_response_time_ms NUMERIC,
    p99_response_time_ms NUMERIC,
    uptime_percentage NUMERIC,
    incident_count INTEGER,
    PRIMARY KEY (provider, model, gateway, aggregation_period, period_start)
);
```

### Views for Status Page

**model_status_current**: Current status for all models
**provider_health_current**: Provider-level health aggregations

These views are optimized for the public status page and include calculated status indicators.

## API Endpoints

### Public Status Page (No Authentication)

#### GET /v1/status/
Get overall system status
```json
{
  "status": "operational",
  "status_message": "All Systems Operational",
  "uptime_percentage": 99.95,
  "total_models": 10547,
  "healthy_models": 10542,
  "offline_models": 5,
  "active_incidents": 0
}
```

#### GET /v1/status/providers
List all provider statuses
```json
[
  {
    "name": "openai",
    "gateway": "openrouter",
    "status": "operational",
    "uptime_24h": 99.98,
    "uptime_7d": 99.95,
    "total_models": 8,
    "healthy_models": 8,
    "avg_response_time_ms": 450
  }
]
```

#### GET /v1/status/models
List model statuses (supports filtering and pagination)

Query Parameters:
- `provider`: Filter by provider
- `gateway`: Filter by gateway
- `status`: Filter by status (operational/degraded/offline)
- `tier`: Filter by monitoring tier
- `limit`: Max results (default: 100)
- `offset`: Pagination offset

#### GET /v1/status/models/{provider}/{model_id}
Get detailed status for a specific model

#### GET /v1/status/incidents
List recent incidents

Query Parameters:
- `status`: Filter by status (active/resolved)
- `severity`: Filter by severity
- `provider`: Filter by provider
- `limit`: Max results (default: 50)

#### GET /v1/status/uptime/{provider}/{model_id}
Get uptime history for charts

Query Parameters:
- `period`: Time period (24h/7d/30d)
- `gateway`: Specific gateway

#### GET /v1/status/search
Search for models

Query Parameters:
- `q`: Search query (min 2 chars)
- `limit`: Max results

#### GET /v1/status/stats
Get overall statistics

### Authenticated Admin Endpoints

#### POST /health/check/now
Trigger immediate health check

#### POST /health/monitoring/start
Start health monitoring service

#### POST /health/monitoring/stop
Stop health monitoring service

#### GET /health/monitoring/status
Get monitoring service status

## Alerting

### Alert Types

- **PROVIDER_DOWN**: Entire provider is offline
- **PROVIDER_DEGRADED**: Provider experiencing issues
- **CRITICAL_MODEL_DOWN**: Critical tier model offline
- **MODEL_DEGRADED**: Model performance degraded
- **HIGH_ERROR_RATE**: Elevated error rate detected
- **SLOW_RESPONSE**: Response times exceed threshold
- **CIRCUIT_BREAKER_OPEN**: Circuit breaker activated

### Alert Severity

- **CRITICAL**: Immediate action required (sent to PagerDuty)
- **HIGH**: Action required soon
- **MEDIUM**: Should be addressed
- **LOW**: Informational

### Alert Channels

- **Email**: Via Resend
- **Slack**: Webhook integration
- **Discord**: Webhook integration
- **PagerDuty**: For critical alerts
- **Custom Webhooks**: For integrations

### Alert De-duplication

Alerts are de-duplicated with a 30-minute cooldown period to prevent alert fatigue.

## Setup & Configuration

### 1. Run Database Migration

```bash
# Apply the migration
supabase migration up

# Or if using Railway/production
# The migration will auto-apply on next deployment
```

### 2. Environment Variables

Add to your `.env` file:

```bash
# Required for basic monitoring
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key

# Optional: Redis for distributed coordination
REDIS_URL=your_redis_url

# Optional: Alerting channels
ADMIN_EMAIL=admin@your-domain.com
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
PAGERDUTY_INTEGRATION_KEY=your_integration_key
ALERT_WEBHOOK_URL=https://your-custom-webhook.com

# Gateway API keys (for health checks)
OPENROUTER_KEY=...
FEATHERLESS_KEY=...
DEEPINFRA_KEY=...
# ... etc for each gateway
```

### 3. Start Monitoring

The monitoring service starts automatically with the application. To manually control:

```python
from src.services.intelligent_health_monitor import intelligent_health_monitor

# Start monitoring
await intelligent_health_monitor.start_monitoring()

# Stop monitoring
await intelligent_health_monitor.stop_monitoring()
```

### 4. Register Routes

In `src/main.py`:

```python
from src.routes.status_page import router as status_page_router

app.include_router(status_page_router)
```

## Usage

### Checking Model Health Programmatically

```python
from src.services.intelligent_health_monitor import intelligent_health_monitor

# Perform on-demand health check
result = await intelligent_health_monitor.check_model_on_demand(
    provider="openai",
    model="gpt-4",
    gateway="openrouter"
)

print(f"Status: {result.status}")
print(f"Response Time: {result.response_time_ms}ms")
```

### Sending Custom Alerts

```python
from src.services.health_alerting import health_alerting_service, Alert, AlertType, AlertSeverity

alert = Alert(
    alert_type=AlertType.HIGH_ERROR_RATE,
    severity=AlertSeverity.MEDIUM,
    title="Custom Alert",
    message="Your custom message",
    provider="openai",
    metrics={"error_rate": 15.5}
)

await health_alerting_service.send_alert(alert)
```

### Querying Health Data

```python
from src.config.supabase_config import supabase

# Get current health for all models
response = supabase.table("model_status_current").select("*").execute()
models = response.data

# Get active incidents
incidents = (
    supabase.table("model_health_incidents")
    .select("*")
    .eq("status", "active")
    .execute()
).data

# Get health history
history = (
    supabase.table("model_health_history")
    .select("*")
    .eq("provider", "openai")
    .eq("model", "gpt-4")
    .order("checked_at", desc=True)
    .limit(100)
    .execute()
).data
```

## Performance Considerations

### Scalability Features

1. **Intelligent Scheduling**: Only checks models when needed based on priority
2. **Concurrent Checking**: Performs multiple checks simultaneously (default: 20 concurrent)
3. **Batch Processing**: Processes models in batches to avoid overwhelming system
4. **Redis Coordination**: Prevents duplicate checks in distributed deployments
5. **Database Indexing**: Optimized indexes for fast queries
6. **Pre-aggregated Data**: Statistics computed ahead of time
7. **Circuit Breaker**: Stops checking consistently failing models

### Resource Usage

For 10,000 models with default configuration:

- **Critical Tier (500 models)**: 500 checks / 5 min = 100/min = ~1.7/sec
- **Popular Tier (2000 models)**: 2000 checks / 30 min = 67/min = ~1.1/sec
- **Standard Tier (7000 models)**: 7000 checks / 120 min = 58/min = ~1/sec
- **On-Demand Tier (500 models)**: 500 checks / 240 min = 2/min = ~0.03/sec

**Total**: ~4 health checks per second average (manageable load)

### Cost Optimization

- **Minimal Tokens**: Each health check uses only 5-10 tokens
- **Smart Intervals**: Checks less frequently for stable models
- **Circuit Breaker**: Stops checking known-failing models
- **Batch Coordination**: Avoids duplicate checks

## Troubleshooting

### Issue: Monitoring not starting

**Check:**
```bash
# Verify database connection
curl https://your-api.com/health/database

# Check monitoring status
curl https://your-api.com/health/monitoring/status
```

**Solution**: Ensure Supabase credentials are correct and database migration has been applied.

### Issue: High database load

**Check:**
```sql
-- Check number of active models being monitored
SELECT COUNT(*) FROM model_health_tracking WHERE is_enabled = TRUE;

-- Check check frequency distribution
SELECT monitoring_tier, COUNT(*)
FROM model_health_tracking
WHERE is_enabled = TRUE
GROUP BY monitoring_tier;
```

**Solution**: Adjust tier distribution or increase check intervals:

```python
# In intelligent_health_monitor.py
self.tier_config = {
    MonitoringTier.CRITICAL: {"interval_seconds": 600},  # Increase from 300
    # ...
}
```

### Issue: Missing health data

**Check:**
```sql
-- Check when last health check was performed
SELECT MAX(last_called_at) FROM model_health_tracking;

-- Check for errors in health history
SELECT status, COUNT(*)
FROM model_health_history
WHERE checked_at > NOW() - INTERVAL '1 hour'
GROUP BY status;
```

**Solution**: Check application logs for errors, ensure gateway API keys are valid.

### Issue: Too many alerts

**Adjust cooldown period:**

```python
# In health_alerting.py
health_alerting_service.alert_cooldown_minutes = 60  # Increase from 30
```

### Issue: Slow status page loading

**Check:**
```sql
-- Verify views are being used
EXPLAIN ANALYZE SELECT * FROM model_status_current LIMIT 100;

-- Check for missing indexes
SELECT * FROM pg_stat_user_indexes WHERE relname LIKE 'model_health%';
```

**Solution**: Ensure database views and indexes from migration are in place.

## Maintenance

### Data Retention

Clean old health history data:

```sql
-- Run periodically (recommended: daily via cron)
SELECT clean_old_health_history(90);  -- Keep 90 days
```

### Tier Optimization

Tiers are automatically updated hourly, but can be manually triggered:

```sql
SELECT update_model_tier();
```

### Incident Management

Manually resolve stuck incidents:

```sql
UPDATE model_health_incidents
SET resolved_at = NOW(),
    status = 'resolved',
    resolution_notes = 'Manually resolved'
WHERE id = <incident_id>;
```

## Future Enhancements

- [ ] Machine learning for anomaly detection
- [ ] Predictive maintenance (predict failures before they occur)
- [ ] Geographic distribution monitoring
- [ ] SLA tracking and reporting
- [ ] Automated capacity planning
- [ ] Integration with APM tools (DataDog, New Relic)
- [ ] Custom dashboard builder
- [ ] Model performance benchmarking
- [ ] Cost optimization recommendations

## Support

For issues or questions:
- GitHub Issues: https://github.com/your-repo/issues
- Documentation: https://docs.gatewayz.ai
- Email: support@gatewayz.ai
