# Prometheus Metrics Implementation Guide
## Adding Pricing Sync Metrics to Gatewayz Backend

**Issue**: #962 - E2E Test identified missing Prometheus metrics for pricing sync
**Priority**: HIGH (Blocking for production)
**Estimated Time**: 2-3 hours
**Created**: January 26, 2026

---

## Table of Contents
1. [Overview](#overview)
2. [Step 1: Add Metrics Definitions](#step-1-add-metrics-definitions)
3. [Step 2: Integrate Metrics into Pricing Sync Service](#step-2-integrate-metrics)
4. [Step 3: Test the Implementation](#step-3-test)
5. [Step 4: Create Grafana Dashboard](#step-4-grafana)
6. [Verification Checklist](#verification-checklist)

---

## Overview

The pricing sync scheduler currently operates without Prometheus metrics, making it impossible to monitor sync operations, performance, and failures in production. This guide implements comprehensive metrics following the existing Prometheus infrastructure patterns in the codebase.

### Metrics to Implement

| Metric | Type | Purpose |
|--------|------|---------|
| `pricing_sync_duration_seconds` | Histogram | Sync duration by provider |
| `pricing_sync_total` | Counter | Total syncs (success/failure) |
| `pricing_sync_models_updated_total` | Counter | Models updated count |
| `pricing_sync_models_skipped_total` | Counter | Models skipped count |
| `pricing_sync_errors_total` | Counter | Error count by provider |
| `pricing_sync_last_success_timestamp` | Gauge | Last successful sync timestamp |
| `pricing_sync_job_duration_seconds` | Histogram | Background job duration |
| `pricing_sync_job_queue_size` | Gauge | Current job queue size |

---

## Step 1: Add Metrics Definitions

### File: `src/services/prometheus_metrics.py`

Add the following metrics definitions at the end of the file (around line 375, after the performance stage metrics):

```python
# ==================== Pricing Sync Metrics ====================
# Metrics for monitoring pricing sync scheduler operations

pricing_sync_duration_seconds = get_or_create_metric(
    Histogram,
    "pricing_sync_duration_seconds",
    "Duration of pricing sync operations by provider",
    ["provider", "status"],  # status: success, failed
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180),
)

pricing_sync_total = get_or_create_metric(
    Counter,
    "pricing_sync_total",
    "Total number of pricing sync operations",
    ["provider", "status", "triggered_by"],  # triggered_by: manual, scheduler, api
)

pricing_sync_models_updated_total = get_or_create_metric(
    Counter,
    "pricing_sync_models_updated_total",
    "Total number of models with updated pricing",
    ["provider"],
)

pricing_sync_models_skipped_total = get_or_create_metric(
    Counter,
    "pricing_sync_models_skipped_total",
    "Total number of models skipped during sync",
    ["provider", "reason"],  # reason: zero_pricing, dynamic_pricing, unchanged, error
)

pricing_sync_errors_total = get_or_create_metric(
    Counter,
    "pricing_sync_errors_total",
    "Total number of pricing sync errors",
    ["provider", "error_type"],  # error_type: api_fetch_failed, db_error, validation_error
)

pricing_sync_last_success_timestamp = get_or_create_metric(
    Gauge,
    "pricing_sync_last_success_timestamp",
    "Unix timestamp of last successful pricing sync",
    ["provider"],
)

pricing_sync_job_duration_seconds = get_or_create_metric(
    Histogram,
    "pricing_sync_job_duration_seconds",
    "Duration of background pricing sync jobs",
    ["status"],  # status: completed, failed
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300),
)

pricing_sync_job_queue_size = get_or_create_metric(
    Gauge,
    "pricing_sync_job_queue_size",
    "Current number of pricing sync jobs in queue",
    ["status"],  # status: queued, running, completed, failed
)

pricing_sync_models_fetched_total = get_or_create_metric(
    Counter,
    "pricing_sync_models_fetched_total",
    "Total number of models fetched from provider APIs",
    ["provider"],
)

pricing_sync_price_changes_total = get_or_create_metric(
    Counter,
    "pricing_sync_price_changes_total",
    "Total number of detected price changes",
    ["provider"],
)

# ==================== Helper Functions for Pricing Sync ====================

@contextmanager
def track_pricing_sync(provider: str, triggered_by: str = "scheduler"):
    """
    Context manager to track pricing sync operations.

    Usage:
        with track_pricing_sync("openrouter", triggered_by="manual"):
            # Perform sync
            result = sync_pricing()
    """
    start_time = time.time()
    status = "success"
    try:
        yield
    except Exception as e:
        status = "failed"
        logger.error(f"Pricing sync failed for {provider}: {e}")
        raise
    finally:
        duration = time.time() - start_time
        pricing_sync_duration_seconds.labels(provider=provider, status=status).observe(duration)
        pricing_sync_total.labels(provider=provider, status=status, triggered_by=triggered_by).inc()

        if status == "success":
            pricing_sync_last_success_timestamp.labels(provider=provider).set(time.time())


def record_pricing_sync_models_updated(provider: str, count: int):
    """Record number of models updated during sync."""
    if count > 0:
        pricing_sync_models_updated_total.labels(provider=provider).inc(count)


def record_pricing_sync_models_skipped(provider: str, count: int, reason: str):
    """Record number of models skipped during sync."""
    if count > 0:
        pricing_sync_models_skipped_total.labels(provider=provider, reason=reason).inc(count)


def record_pricing_sync_error(provider: str, error_type: str):
    """Record pricing sync error."""
    pricing_sync_errors_total.labels(provider=provider, error_type=error_type).inc()


def record_pricing_sync_models_fetched(provider: str, count: int):
    """Record number of models fetched from provider API."""
    if count > 0:
        pricing_sync_models_fetched_total.labels(provider=provider).inc(count)


def record_pricing_sync_price_changes(provider: str, count: int):
    """Record number of price changes detected."""
    if count > 0:
        pricing_sync_price_changes_total.labels(provider=provider).inc(count)


def set_pricing_sync_job_queue_size(status: str, count: int):
    """Set current job queue size."""
    pricing_sync_job_queue_size.labels(status=status).set(count)


def track_pricing_sync_job(duration: float, status: str):
    """Track pricing sync background job duration."""
    pricing_sync_job_duration_seconds.labels(status=status).observe(duration)
```

---

## Step 2: Integrate Metrics into Pricing Sync Service

### File: `src/routes/pricing_sync.py`

Find the manual trigger endpoint and add metrics tracking:

```python
from src.services.prometheus_metrics import (
    track_pricing_sync,
    record_pricing_sync_models_updated,
    record_pricing_sync_models_skipped,
    record_pricing_sync_models_fetched,
    record_pricing_sync_price_changes,
    record_pricing_sync_error,
    track_pricing_sync_job,
    set_pricing_sync_job_queue_size,
)

# In the manual trigger endpoint (around line 100-150)
@router.post("/trigger")
async def trigger_manual_sync(
    user_email: str = Depends(get_current_admin_user_email),
):
    """Trigger manual pricing sync"""

    # Track job queue size
    try:
        # Count jobs by status
        queued_count = supabase.table("pricing_sync_jobs")\
            .select("id", count="exact")\
            .eq("status", "queued")\
            .execute()
        running_count = supabase.table("pricing_sync_jobs")\
            .select("id", count="exact")\
            .eq("status", "running")\
            .execute()

        set_pricing_sync_job_queue_size("queued", queued_count.count or 0)
        set_pricing_sync_job_queue_size("running", running_count.count or 0)
    except Exception as e:
        logger.warning(f"Failed to track job queue size: {e}")

    # Create job and track metrics
    job_id = str(uuid.uuid4())
    job_start_time = time.time()

    try:
        # ... existing job creation code ...

        # After job completes, track metrics
        job_duration = time.time() - job_start_time
        track_pricing_sync_job(job_duration, job_record.get("status", "completed"))

        return {
            "success": True,
            "sync_id": job_id,
            # ... rest of response ...
        }
    except Exception as e:
        job_duration = time.time() - job_start_time
        track_pricing_sync_job(job_duration, "failed")
        raise
```

### File: `src/services/pricing_sync_service.py`

Add metrics to the sync operations:

```python
from src.services.prometheus_metrics import (
    track_pricing_sync,
    record_pricing_sync_models_updated,
    record_pricing_sync_models_skipped,
    record_pricing_sync_models_fetched,
    record_pricing_sync_price_changes,
    record_pricing_sync_error,
)

# In the sync_provider method (around line 200-300)
async def sync_provider_pricing(
    self,
    provider_slug: str,
    triggered_by: str = "scheduler",
) -> dict:
    """Sync pricing for a specific provider"""

    result = {
        "provider": provider_slug,
        "models_fetched": 0,
        "models_updated": 0,
        "models_skipped": 0,
        "models_unchanged": 0,
        "errors": 0,
        "error_details": [],
        "price_changes": [],
        "status": "success",
    }

    try:
        with track_pricing_sync(provider_slug, triggered_by=triggered_by):
            # Fetch models from provider API
            models = await self._fetch_provider_models(provider_slug)
            result["models_fetched"] = len(models)

            # Record fetched count
            record_pricing_sync_models_fetched(provider_slug, len(models))

            # Process models
            for model in models:
                try:
                    # Check for price changes
                    if self._is_price_changed(model):
                        result["models_updated"] += 1
                        result["price_changes"].append(model["id"])
                    elif self._should_skip(model):
                        result["models_skipped"] += 1
                        result["error_details"].append({
                            "model_id": model["id"],
                            "reason": self._get_skip_reason(model)
                        })
                    else:
                        result["models_unchanged"] += 1

                except Exception as e:
                    result["errors"] += 1
                    logger.error(f"Error processing model: {e}")

            # Record metrics
            record_pricing_sync_models_updated(provider_slug, result["models_updated"])
            record_pricing_sync_price_changes(provider_slug, len(result["price_changes"]))

            # Record skipped models by reason
            skip_reasons = {}
            for detail in result["error_details"]:
                reason = detail.get("reason", "unknown")
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

            for reason, count in skip_reasons.items():
                record_pricing_sync_models_skipped(provider_slug, count, reason)

            return result

    except Exception as e:
        logger.error(f"Pricing sync failed for {provider_slug}: {e}")
        result["status"] = "failed"
        result["error_message"] = str(e)

        # Record error
        error_type = self._classify_error(e)
        record_pricing_sync_error(provider_slug, error_type)

        raise

    finally:
        return result


def _classify_error(self, error: Exception) -> str:
    """Classify error type for metrics"""
    error_str = str(error).lower()

    if "api" in error_str or "fetch" in error_str or "request" in error_str:
        return "api_fetch_failed"
    elif "database" in error_str or "supabase" in error_str:
        return "db_error"
    elif "validation" in error_str or "invalid" in error_str:
        return "validation_error"
    else:
        return "unknown"


def _get_skip_reason(self, model: dict) -> str:
    """Get reason for skipping model"""
    pricing = model.get("pricing", {})

    if pricing.get("input") == 0 and pricing.get("output") == 0:
        return "zero_pricing"
    elif "dynamic" in str(pricing).lower():
        return "dynamic_pricing"
    else:
        return "unchanged"
```

---

## Step 3: Test the Implementation

### 3.1 Local Testing

```python
# Create test script: scripts/test_pricing_metrics.py

import requests
import time

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "your_admin_key_here"

def test_pricing_metrics():
    print("Testing pricing sync metrics...")

    # 1. Check current metrics
    print("\n1. Current metrics (before sync):")
    metrics = requests.get(f"{STAGING_URL}/metrics").text
    pricing_metrics = [line for line in metrics.split('\n') if 'pricing_sync' in line and not line.startswith('#')]
    for metric in pricing_metrics[:10]:
        print(f"  {metric}")

    # 2. Trigger manual sync
    print("\n2. Triggering manual sync...")
    response = requests.post(
        f"{STAGING_URL}/admin/pricing/scheduler/trigger",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    )
    print(f"  Status: {response.status_code}")
    print(f"  Response: {response.json()}")

    # 3. Wait for sync to complete
    print("\n3. Waiting 60 seconds for sync...")
    time.sleep(60)

    # 4. Check updated metrics
    print("\n4. Updated metrics (after sync):")
    metrics = requests.get(f"{STAGING_URL}/metrics").text
    pricing_metrics = [line for line in metrics.split('\n') if 'pricing_sync' in line and not line.startswith('#')]
    for metric in pricing_metrics[:20]:
        print(f"  {metric}")

    # 5. Verify specific metrics
    print("\n5. Verification:")
    has_duration = any('pricing_sync_duration_seconds' in m for m in pricing_metrics)
    has_total = any('pricing_sync_total' in m for m in pricing_metrics)
    has_updated = any('pricing_sync_models_updated_total' in m for m in pricing_metrics)
    has_timestamp = any('pricing_sync_last_success_timestamp' in m for m in pricing_metrics)

    print(f"  ✓ Duration metric: {'✅' if has_duration else '❌'}")
    print(f"  ✓ Total counter: {'✅' if has_total else '❌'}")
    print(f"  ✓ Models updated: {'✅' if has_updated else '❌'}")
    print(f"  ✓ Last success timestamp: {'✅' if has_timestamp else '❌'}")

    if all([has_duration, has_total, has_updated, has_timestamp]):
        print("\n✅ All pricing sync metrics are working!")
        return True
    else:
        print("\n❌ Some metrics are missing")
        return False

if __name__ == "__main__":
    success = test_pricing_metrics()
    exit(0 if success else 1)
```

Run the test:
```bash
python3 scripts/test_pricing_metrics.py
```

### 3.2 Manual Verification

```bash
# 1. Check metrics endpoint
curl -s "https://gatewayz-staging.up.railway.app/metrics" | grep pricing_sync

# Expected output (example):
# pricing_sync_duration_seconds_bucket{provider="openrouter",status="success",le="30.0"} 1.0
# pricing_sync_duration_seconds_sum{provider="openrouter",status="success"} 37.4
# pricing_sync_total{provider="openrouter",status="success",triggered_by="manual"} 1.0
# pricing_sync_models_updated_total{provider="openrouter"} 0.0
# pricing_sync_models_skipped_total{provider="openrouter",reason="zero_pricing"} 34.0
# pricing_sync_last_success_timestamp{provider="openrouter"} 1706310347.0

# 2. Trigger manual sync
curl -X POST -H "Authorization: Bearer $ADMIN_KEY" \
  "https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger"

# 3. Wait 60 seconds, then check metrics again
sleep 60
curl -s "https://gatewayz-staging.up.railway.app/metrics" | grep pricing_sync | head -30
```

---

## Step 4: Create Grafana Dashboard

### 4.1 Import Dashboard JSON

Create file: `grafana/pricing_sync_dashboard.json`

```json
{
  "dashboard": {
    "title": "Pricing Sync Scheduler",
    "panels": [
      {
        "title": "Sync Duration by Provider",
        "targets": [
          {
            "expr": "rate(pricing_sync_duration_seconds_sum[5m]) / rate(pricing_sync_duration_seconds_count[5m])",
            "legendFormat": "{{provider}} - {{status}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Sync Success Rate",
        "targets": [
          {
            "expr": "rate(pricing_sync_total{status=\"success\"}[5m]) / rate(pricing_sync_total[5m]) * 100",
            "legendFormat": "{{provider}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Models Updated",
        "targets": [
          {
            "expr": "increase(pricing_sync_models_updated_total[1h])",
            "legendFormat": "{{provider}}"
          }
        ],
        "type": "stat"
      },
      {
        "title": "Models Skipped by Reason",
        "targets": [
          {
            "expr": "increase(pricing_sync_models_skipped_total[1h])",
            "legendFormat": "{{provider}} - {{reason}}"
          }
        ],
        "type": "bar"
      },
      {
        "title": "Errors by Provider",
        "targets": [
          {
            "expr": "increase(pricing_sync_errors_total[1h])",
            "legendFormat": "{{provider}} - {{error_type}}"
          }
        ],
        "type": "bar"
      },
      {
        "title": "Time Since Last Successful Sync",
        "targets": [
          {
            "expr": "time() - pricing_sync_last_success_timestamp",
            "legendFormat": "{{provider}}"
          }
        ],
        "type": "stat",
        "unit": "seconds"
      }
    ]
  }
}
```

### 4.2 Grafana Queries

**Panel 1: Sync Duration**
```promql
# Average sync duration (last 5 minutes)
rate(pricing_sync_duration_seconds_sum[5m]) / rate(pricing_sync_duration_seconds_count[5m])

# 95th percentile
histogram_quantile(0.95, rate(pricing_sync_duration_seconds_bucket[5m]))
```

**Panel 2: Success Rate**
```promql
# Success rate percentage
rate(pricing_sync_total{status="success"}[5m]) / rate(pricing_sync_total[5m]) * 100
```

**Panel 3: Models Updated**
```promql
# Total models updated in last hour
increase(pricing_sync_models_updated_total[1h])
```

**Panel 4: Error Rate**
```promql
# Errors per minute
rate(pricing_sync_errors_total[5m]) * 60
```

**Panel 5: Job Queue Depth**
```promql
# Current queue size
pricing_sync_job_queue_size{status="queued"}
```

---

## Verification Checklist

### Pre-Deployment Checks

- [ ] **Code Changes**
  - [ ] Added metrics definitions to `prometheus_metrics.py`
  - [ ] Added helper functions for metrics
  - [ ] Integrated metrics into `pricing_sync_service.py`
  - [ ] Integrated metrics into `pricing_sync.py` routes
  - [ ] Added error classification logic

- [ ] **Testing**
  - [ ] Ran local tests with test script
  - [ ] Verified metrics appear in `/metrics` endpoint
  - [ ] Triggered manual sync and confirmed metrics update
  - [ ] Checked all metric types (Counter, Histogram, Gauge)
  - [ ] Verified metric labels are correct

- [ ] **Documentation**
  - [ ] Updated README with metrics information
  - [ ] Created Grafana dashboard JSON
  - [ ] Documented metric meanings

### Post-Deployment Verification

```bash
# 1. Check metrics endpoint
curl https://api.gatewayz.ai/metrics | grep pricing_sync | wc -l
# Expected: >20 lines

# 2. Verify specific metrics exist
curl -s https://api.gatewayz.ai/metrics | grep -E "pricing_sync_(duration|total|models|errors|last_success)"

# 3. Trigger sync and verify updates
curl -X POST -H "Authorization: Bearer $ADMIN_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/trigger

sleep 60

curl -s https://api.gatewayz.ai/metrics | grep pricing_sync_total
```

### Expected Metrics Output

```prometheus
# HELP pricing_sync_duration_seconds Duration of pricing sync operations by provider
# TYPE pricing_sync_duration_seconds histogram
pricing_sync_duration_seconds_bucket{provider="openrouter",status="success",le="10.0"} 0.0
pricing_sync_duration_seconds_bucket{provider="openrouter",status="success",le="30.0"} 1.0
pricing_sync_duration_seconds_bucket{provider="openrouter",status="success",le="60.0"} 1.0
pricing_sync_duration_seconds_sum{provider="openrouter",status="success"} 37.4
pricing_sync_duration_seconds_count{provider="openrouter",status="success"} 1.0

# HELP pricing_sync_total Total number of pricing sync operations
# TYPE pricing_sync_total counter
pricing_sync_total{provider="openrouter",status="success",triggered_by="manual"} 2.0
pricing_sync_total{provider="featherless",status="failed",triggered_by="manual"} 1.0

# HELP pricing_sync_models_updated_total Total number of models with updated pricing
# TYPE pricing_sync_models_updated_total counter
pricing_sync_models_updated_total{provider="openrouter"} 0.0

# HELP pricing_sync_models_skipped_total Total number of models skipped during sync
# TYPE pricing_sync_models_skipped_total counter
pricing_sync_models_skipped_total{provider="openrouter",reason="zero_pricing"} 34.0

# HELP pricing_sync_last_success_timestamp Unix timestamp of last successful pricing sync
# TYPE pricing_sync_last_success_timestamp gauge
pricing_sync_last_success_timestamp{provider="openrouter"} 1706310347.0
```

---

## Troubleshooting

### Issue: Metrics not appearing

**Check 1**: Verify metrics are registered
```python
# Add debug logging
from src.services import prometheus_metrics
print(dir(prometheus_metrics))  # Should show pricing_sync_* metrics
```

**Check 2**: Check for duplicate metric errors
```bash
# Look for errors in logs
railway logs | grep -i "prometheus\|metric"
```

**Check 3**: Verify imports are correct
```python
from src.services.prometheus_metrics import (
    track_pricing_sync,  # Should not throw ImportError
)
```

### Issue: Metrics show zero values

**Check**: Ensure metrics are being called
```python
# Add debug logging in sync function
logger.info(f"Recording {count} models updated for {provider}")
record_pricing_sync_models_updated(provider, count)
```

### Issue: Wrong metric values

**Check**: Verify label values match
```python
# Labels must be consistent
pricing_sync_total.labels(
    provider="openrouter",  # Not "OpenRouter" or "open_router"
    status="success",  # Not "succeeded" or "Success"
    triggered_by="manual"  # Not "user" or "api"
)
```

---

## Time Estimate Breakdown

| Task | Time | Difficulty |
|------|------|------------|
| Add metrics definitions | 30 min | Easy |
| Integrate into sync service | 60 min | Medium |
| Integrate into routes | 30 min | Easy |
| Testing (local + staging) | 30 min | Easy |
| Create Grafana dashboard | 30 min | Medium |
| **Total** | **3 hours** | **Medium** |

---

## Success Criteria

- [✓] All 8 core metrics exposed in `/metrics` endpoint
- [✓] Metrics update correctly after manual sync
- [✓] Metrics update correctly after scheduled sync
- [✓] Grafana dashboard displays all metrics
- [✓] No duplicate metric errors in logs
- [✓] Metrics persist across application restarts
- [✓] Documentation updated

---

## References

- **Existing Prometheus Infrastructure**: `src/services/prometheus_metrics.py`
- **Pricing Sync Service**: `src/services/pricing_sync_service.py`
- **Pricing Sync Routes**: `src/routes/pricing_sync.py`
- **E2E Test Report**: `E2E_TEST_UPDATED_REPORT_ISSUE_962.md`
- **Prometheus Best Practices**: https://prometheus.io/docs/practices/naming/

---

**Status**: Ready for Implementation
**Last Updated**: January 26, 2026
**Next Review**: After implementation and testing
