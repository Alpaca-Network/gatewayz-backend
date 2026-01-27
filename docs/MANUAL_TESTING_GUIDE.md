# Pricing Sync Scheduler - Manual Testing Guide

**Purpose**: Comprehensive manual testing procedures for all pricing sync scheduler features
**Scope**: Phases 2.5, 3, 4, 5, and 6
**Target**: Staging and Production environments
**Estimated Time**: 4-6 hours for complete testing

---

## Prerequisites

### Required Access
- [ ] Staging environment access
- [ ] Production environment access (read-only initially)
- [ ] Admin API key for both environments
- [ ] Supabase dashboard access
- [ ] Railway/hosting platform access
- [ ] Grafana dashboard access (if deployed)
- [ ] Prometheus access (if deployed)

### Required Tools
```bash
# Install required tools
brew install curl jq   # macOS
# or
apt-get install curl jq  # Linux

# Verify tools
curl --version
jq --version
```

### Environment Setup
```bash
# Set environment variables
export STAGING_URL="https://gatewayz-staging.up.railway.app"
export STAGING_ADMIN_KEY="your_staging_admin_key"

export PROD_URL="https://api.gatewayz.ai"
export PROD_ADMIN_KEY="your_production_admin_key"

# Verify connectivity
curl -I $STAGING_URL/health
curl -I $PROD_URL/health
```

---

## Testing Overview

| Phase | Feature | Test Type | Priority | Time |
|-------|---------|-----------|----------|------|
| 2.5 | Scheduler Lifecycle | Integration | Critical | 30 min |
| 2.5 | Automated Sync | Integration | Critical | 1 hour |
| 3 | Admin Endpoints | API | Critical | 30 min |
| 4 | Test Suite | Unit | High | 15 min |
| 5 | Deployment | E2E | Critical | 1 hour |
| 6 | Monitoring | Integration | High | 1-2 hours |

---

## Part 1: Database Migration Verification

**Purpose**: Verify migration was applied successfully
**Time**: 10 minutes
**Risk**: Low

### Step 1.1: Check Tables Exist

**Via Supabase Dashboard**:
1. Go to: https://app.supabase.com/project/YOUR_PROJECT/editor
2. Click on "Table Editor"
3. Look for:
   - `model_pricing_history`
   - `pricing_sync_log`

**Via SQL Editor**:
```sql
-- Check tables exist
SELECT
    tablename,
    schemaname,
    hasindexes,
    hasrules,
    hastriggers
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename;

-- Expected: 2 rows returned
```

✅ **Pass Criteria**: Both tables exist

### Step 1.2: Verify Table Structure

```sql
-- Check model_pricing_history columns
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'model_pricing_history'
ORDER BY ordinal_position;

-- Expected columns:
-- id, model_id, price_per_input_token, price_per_output_token,
-- previous_input_price, previous_output_price, changed_at, changed_by

-- Check pricing_sync_log columns
SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'pricing_sync_log'
ORDER BY ordinal_position;

-- Expected columns:
-- id, provider_slug, sync_started_at, sync_completed_at, status,
-- models_fetched, models_updated, models_skipped, errors, error_message,
-- triggered_by, duration_ms
```

✅ **Pass Criteria**: All expected columns present

### Step 1.3: Check Indexes

```sql
SELECT
    indexname,
    tablename,
    indexdef
FROM pg_indexes
WHERE tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename, indexname;

-- Expected: 7 indexes total
-- model_pricing_history: 3 indexes
-- pricing_sync_log: 4 indexes
```

✅ **Pass Criteria**: All indexes created

### Step 1.4: Verify RLS Policies

```sql
SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd
FROM pg_policies
WHERE tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename, policyname;

-- Expected: 4 policies total
-- 2 for service_role (full access)
-- 2 for authenticated (read-only)
```

✅ **Pass Criteria**: All RLS policies present

### Step 1.5: Test Insert Permissions

```sql
-- Test insert (should work with service_role)
INSERT INTO pricing_sync_log (
    provider_slug,
    sync_started_at,
    status,
    triggered_by,
    models_fetched,
    models_updated
) VALUES (
    'test_manual',
    NOW(),
    'success',
    'manual_verification',
    100,
    50
);

-- Verify insert
SELECT * FROM pricing_sync_log WHERE provider_slug = 'test_manual';

-- Cleanup
DELETE FROM pricing_sync_log WHERE provider_slug = 'test_manual';
```

✅ **Pass Criteria**: Insert and delete successful

---

## Part 2: Scheduler Lifecycle Testing

**Purpose**: Verify scheduler starts, runs, and stops correctly
**Time**: 30 minutes
**Environment**: Staging

### Step 2.1: Verify Scheduler Configuration

```bash
# Check environment variables
railway variables --environment staging | grep PRICING_SYNC

# Expected output:
# PRICING_SYNC_ENABLED=true
# PRICING_SYNC_INTERVAL_HOURS=3
# PRICING_SYNC_PROVIDERS=openrouter,featherless
```

✅ **Pass Criteria**: All variables set correctly

### Step 2.2: Check Scheduler Started

```bash
# Check application logs for startup
railway logs --environment staging | grep "Pricing sync scheduler"

# Expected output:
# ✅ Pricing sync scheduler started
# Scheduler will run every 3 hours
# Providers configured: openrouter, featherless
```

✅ **Pass Criteria**: Startup message present, no errors

### Step 2.3: Verify Initial Sync (30 second delay)

```bash
# Monitor logs for initial sync
railway logs --environment staging --follow | grep "pricing sync"

# Expected within 30 seconds:
# 🔄 Starting scheduled pricing sync...
# ✅ Scheduled pricing sync completed successfully (duration: Xs, updated: Y)

# Wait and verify
sleep 35

# Check logs
railway logs --environment staging | grep "Scheduled pricing sync completed" | tail -1
```

✅ **Pass Criteria**: Initial sync completes within 60 seconds of startup

### Step 2.4: Verify Background Task Running

```bash
# Check application health
curl $STAGING_URL/health | jq '.'

# Check system status
curl $STAGING_URL/system/status | jq '.background_tasks'

# Expected: Task named "pricing_sync_scheduler_loop" should be present
```

✅ **Pass Criteria**: Health check passes, background task running

### Step 2.5: Test Graceful Shutdown

```bash
# Trigger redeploy to test shutdown
railway redeploy --environment staging

# Monitor logs for shutdown message
railway logs --environment staging --follow | grep -E "(shutdown|stopping)"

# Expected:
# Scheduler received shutdown signal
# Waiting for current sync to complete...
# Scheduler stopped gracefully
```

✅ **Pass Criteria**: No abrupt terminations, graceful shutdown messages

---

## Part 3: Admin Endpoints Testing

**Purpose**: Verify admin control endpoints work correctly
**Time**: 30 minutes
**Environment**: Staging

### Step 3.1: Test Status Endpoint

```bash
# Get scheduler status
curl -X GET \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/status | jq '.'

# Expected response:
# {
#   "success": true,
#   "scheduler": {
#     "enabled": true,
#     "running": true,
#     "interval_hours": 3,
#     "providers": ["openrouter", "featherless"]
#   },
#   "timestamp": "2026-01-26T..."
# }
```

✅ **Pass Criteria**:
- Status code 200
- `enabled: true`
- `running: true`
- Correct interval and providers

### Step 3.2: Test Status Without Auth

```bash
# Try without auth header (should fail)
curl -X GET \
  $STAGING_URL/admin/pricing/scheduler/status

# Expected: 401 Unauthorized or 403 Forbidden
```

✅ **Pass Criteria**: Proper authentication error

### Step 3.3: Test Manual Trigger

```bash
# Trigger manual sync
curl -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger | jq '.'

# Expected response:
# {
#   "success": true,
#   "status": "success",
#   "total_models_updated": 150,
#   "duration_seconds": 12.5,
#   "providers_synced": ["openrouter", "featherless"],
#   "triggered_by": "admin@gatewayz.ai",
#   "triggered_at": "2026-01-26T..."
# }
```

✅ **Pass Criteria**:
- Status code 200
- `success: true`
- Models updated > 0
- Duration reasonable (< 60s)

### Step 3.4: Verify Manual Trigger Logs

```bash
# Check logs for manual trigger
railway logs --environment staging | grep "Manual pricing sync triggered"

# Expected:
# Manual pricing sync triggered by admin: admin@gatewayz.ai
# Starting manual pricing sync...
# ✅ Manual pricing sync completed successfully
```

✅ **Pass Criteria**: Manual sync logged with admin user

### Step 3.5: Test Concurrent Manual Triggers

```bash
# Try triggering while a sync is running
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger &

sleep 1

curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger

# Expected: Both should complete (may queue or run concurrently)
```

✅ **Pass Criteria**: No crashes, both syncs complete or second queued

### Step 3.6: Test Status After Manual Trigger

```bash
# Get status immediately after manual trigger
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger > /dev/null

sleep 2

curl -X GET -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/status | jq '.scheduler.last_sync'

# Expected: last_sync timestamp should be very recent (< 5 seconds ago)
```

✅ **Pass Criteria**: Status reflects recent manual sync

---

## Part 4: Automated Test Suite

**Purpose**: Run comprehensive test suite
**Time**: 15 minutes
**Environment**: Local

### Step 4.1: Run All Tests

```bash
# Navigate to project root
cd /path/to/gatewayz-backend

# Run all pricing sync scheduler tests
pytest tests/services/test_pricing_sync_scheduler.py -v

# Expected: All 18 tests pass
# TestSchedulerLifecycle: 4 tests
# TestSchedulerStatus: 3 tests
# TestManualTrigger: 3 tests
# TestSchedulerLoop: 2 tests
# TestErrorHandling: 2 tests
# TestPrometheusMetrics: 2 tests
# TestConfiguration: 2 tests
```

✅ **Pass Criteria**: All 18 tests pass

### Step 4.2: Run Admin Endpoint Tests

```bash
# Run admin pricing endpoint tests
pytest tests/routes/test_admin.py::TestPricingSchedulerStatus -v
pytest tests/routes/test_admin.py::TestPricingSchedulerTrigger -v
pytest tests/routes/test_admin.py::TestPricingSchedulerIntegration -v

# Expected: All 12 tests pass
# TestPricingSchedulerStatus: 4 tests
# TestPricingSchedulerTrigger: 6 tests
# TestPricingSchedulerIntegration: 2 tests
```

✅ **Pass Criteria**: All 12 tests pass

### Step 4.3: Run with Coverage

```bash
# Run with coverage report
pytest tests/services/test_pricing_sync_scheduler.py \
       tests/routes/test_admin.py \
       --cov=src/services/pricing_sync_scheduler \
       --cov=src/routes/admin \
       --cov-report=term \
       --cov-report=html

# Expected: Coverage > 85%
```

✅ **Pass Criteria**: Coverage ≥ 85%, all tests pass

---

## Part 5: Metrics Verification

**Purpose**: Verify Prometheus metrics are collecting
**Time**: 15 minutes
**Environment**: Staging

### Step 5.1: Check Metrics Endpoint

```bash
# Get all pricing metrics
curl $STAGING_URL/metrics | grep pricing_

# Expected metrics:
# pricing_scheduled_sync_runs_total{status="success"} X
# pricing_scheduled_sync_runs_total{status="failed"} Y
# pricing_scheduled_sync_duration_seconds_bucket{...}
# pricing_scheduled_sync_duration_seconds_sum X
# pricing_scheduled_sync_duration_seconds_count Y
# pricing_last_sync_timestamp{provider="openrouter"} X
# pricing_last_sync_timestamp{provider="featherless"} X
# pricing_models_synced_total{provider="openrouter"} X
# pricing_models_synced_total{provider="featherless"} X
```

✅ **Pass Criteria**: All expected metrics present

### Step 5.2: Verify Sync Success Metrics

```bash
# Check success count
curl $STAGING_URL/metrics | grep 'pricing_scheduled_sync_runs_total{status="success"}'

# Expected: Count > 0
```

✅ **Pass Criteria**: Success count > 0

### Step 5.3: Verify Duration Metrics

```bash
# Check duration metrics
curl $STAGING_URL/metrics | grep 'pricing_scheduled_sync_duration_seconds'

# Expected: Histogram with buckets, sum, and count
```

✅ **Pass Criteria**: Duration histogram present

### Step 5.4: Verify Per-Provider Metrics

```bash
# Check per-provider metrics
curl $STAGING_URL/metrics | grep 'pricing_last_sync_timestamp'

# Expected: Timestamp for each provider (openrouter, featherless)
```

✅ **Pass Criteria**: Metrics for both providers

### Step 5.5: Trigger Sync and Verify Metrics Update

```bash
# Get current metrics
BEFORE=$(curl -s $STAGING_URL/metrics | grep 'pricing_scheduled_sync_runs_total{status="success"}' | awk '{print $2}')

# Trigger manual sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger > /dev/null

# Wait for completion
sleep 15

# Get updated metrics
AFTER=$(curl -s $STAGING_URL/metrics | grep 'pricing_scheduled_sync_runs_total{status="success"}' | awk '{print $2}')

# Check increment
echo "Before: $BEFORE, After: $AFTER"
```

✅ **Pass Criteria**: Metrics incremented after sync

---

## Part 6: Scheduled Sync Verification

**Purpose**: Verify syncs run on schedule
**Time**: 1 hour (wait time for scheduled sync)
**Environment**: Staging

### Step 6.1: Check Sync Interval Configuration

```bash
# Verify interval setting
railway variables --environment staging | grep PRICING_SYNC_INTERVAL_HOURS

# Expected: PRICING_SYNC_INTERVAL_HOURS=3
```

✅ **Pass Criteria**: Interval set to 3 hours

### Step 6.2: Monitor for Next Scheduled Sync

```bash
# Get last sync timestamp
LAST_SYNC=$(curl -s $STAGING_URL/metrics | grep 'pricing_last_sync_timestamp' | head -1 | awk '{print $2}')

echo "Last sync timestamp: $LAST_SYNC"
echo "Current time: $(date +%s)"
echo "Next sync expected at: $(date -r $(echo "$LAST_SYNC + 10800" | bc))"

# Monitor logs for next sync
railway logs --environment staging --follow | grep "Starting scheduled pricing sync"

# Wait up to 3 hours for next sync...
```

✅ **Pass Criteria**: Sync runs approximately 3 hours after last sync

### Step 6.3: Verify Sync Completes Successfully

```bash
# After sync starts, verify completion
railway logs --environment staging | grep "Scheduled pricing sync completed" | tail -1

# Expected:
# ✅ Scheduled pricing sync completed successfully (duration: Xs, updated: Y)
```

✅ **Pass Criteria**: Sync completes with success message

### Step 6.4: Check Database Logs

```sql
-- Check pricing_sync_log for recent syncs
SELECT
    id,
    provider_slug,
    sync_started_at,
    sync_completed_at,
    status,
    models_fetched,
    models_updated,
    models_skipped,
    errors,
    duration_ms,
    triggered_by
FROM pricing_sync_log
ORDER BY sync_started_at DESC
LIMIT 10;

-- Expected: Multiple rows with status='success', triggered_by='scheduler'
```

✅ **Pass Criteria**: Database logs show scheduled syncs

---

## Part 7: Error Handling Testing

**Purpose**: Verify system handles errors gracefully
**Time**: 30 minutes
**Environment**: Staging

### Step 7.1: Test Invalid Provider

```bash
# Temporarily add invalid provider
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless,invalid_provider --environment staging
railway redeploy --environment staging

# Monitor logs
railway logs --environment staging --follow | grep -E "(error|failed|invalid)"

# Expected: Errors logged, but scheduler continues
```

✅ **Pass Criteria**: Errors handled gracefully, no crashes

### Step 7.2: Test Provider API Timeout

```bash
# Check logs for timeout handling
railway logs --environment staging | grep -i timeout

# Expected: Timeout errors logged but not crashing
```

✅ **Pass Criteria**: Timeouts logged, sync continues for other providers

### Step 7.3: Test Database Connectivity Issues

```bash
# This is harder to test manually without disrupting service
# Check logs for any database errors
railway logs --environment staging | grep -i "database.*error"

# Expected: Minimal or no database errors
```

✅ **Pass Criteria**: No persistent database errors

### Step 7.4: Verify Error Metrics

```bash
# Check for failure metrics
curl $STAGING_URL/metrics | grep 'pricing_scheduled_sync_runs_total{status="failed"}'

# Expected: Count should be 0 or very low
```

✅ **Pass Criteria**: Failure count is 0 or minimal

### Step 7.5: Test Sentry Integration

```bash
# Check if errors are being sent to Sentry
# Go to: https://sentry.io/organizations/gatewayz/issues/

# Filter by:
# - Tag: component=pricing_sync
# - Date: Last 24 hours

# Expected: Errors logged to Sentry with proper context
```

✅ **Pass Criteria**: Errors visible in Sentry with context

### Step 7.6: Restore Configuration

```bash
# Restore correct configuration
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless --environment staging
railway redeploy --environment staging
```

---

## Part 8: Data Integrity Verification

**Purpose**: Verify pricing data is being updated correctly
**Time**: 20 minutes
**Environment**: Staging

### Step 8.1: Check Model Pricing Before Sync

```sql
-- Get current pricing for sample models
SELECT
    m.model_id,
    m.model_name,
    mp.input_price_per_1m_tokens,
    mp.output_price_per_1m_tokens,
    mp.updated_at
FROM models m
JOIN model_pricing mp ON m.id = mp.model_id
WHERE m.model_id IN (
    'openrouter/anthropic/claude-3.5-sonnet',
    'openrouter/openai/gpt-4',
    'featherless/mistralai/mistral-7b-instruct'
)
ORDER BY m.model_id;
```

✅ **Pass Criteria**: Pricing data exists for test models

### Step 8.2: Trigger Sync and Check Updates

```bash
# Trigger manual sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger | jq '.'

# Wait for completion
sleep 20
```

### Step 8.3: Verify Pricing Updated

```sql
-- Check updated timestamps
SELECT
    m.model_id,
    m.model_name,
    mp.input_price_per_1m_tokens,
    mp.output_price_per_1m_tokens,
    mp.updated_at
FROM models m
JOIN model_pricing mp ON m.id = mp.model_id
WHERE m.model_id IN (
    'openrouter/anthropic/claude-3.5-sonnet',
    'openrouter/openai/gpt-4',
    'featherless/mistralai/mistral-7b-instruct'
)
ORDER BY m.model_id;

-- Compare with previous results - updated_at should be recent
```

✅ **Pass Criteria**: `updated_at` timestamps are recent (within last 5 minutes)

### Step 8.4: Check Pricing History

```sql
-- Check if pricing changes are logged
SELECT
    mph.id,
    m.model_id,
    mph.price_per_input_token,
    mph.price_per_output_token,
    mph.previous_input_price,
    mph.previous_output_price,
    mph.changed_at,
    mph.changed_by
FROM model_pricing_history mph
JOIN models m ON mph.model_id = m.id
ORDER BY mph.changed_at DESC
LIMIT 10;

-- Expected: Recent entries with changed_by like 'scheduler:provider_slug'
```

✅ **Pass Criteria**: Pricing changes logged to history table

### Step 8.5: Verify No Duplicate Updates

```sql
-- Check for duplicate sync logs (shouldn't have overlapping syncs)
SELECT
    provider_slug,
    COUNT(*) as concurrent_syncs
FROM pricing_sync_log
WHERE sync_completed_at IS NULL
AND sync_started_at > NOW() - INTERVAL '1 hour'
GROUP BY provider_slug
HAVING COUNT(*) > 1;

-- Expected: 0 rows (no concurrent syncs for same provider)
```

✅ **Pass Criteria**: No duplicate concurrent syncs

---

## Part 9: Performance Testing

**Purpose**: Verify system performance is acceptable
**Time**: 30 minutes
**Environment**: Staging

### Step 9.1: Measure Sync Duration

```bash
# Trigger sync and measure time
START=$(date +%s)

curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger | jq '.duration_seconds'

END=$(date +%s)
DURATION=$((END - START))

echo "API Response Time: ${DURATION}s"
```

✅ **Pass Criteria**:
- Sync duration < 60 seconds
- API response time < 5 seconds

### Step 9.2: Check Memory Usage During Sync

```bash
# Check memory metrics
curl $STAGING_URL/metrics | grep process_resident_memory_bytes

# Trigger sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger > /dev/null

sleep 5

# Check memory again
curl $STAGING_URL/metrics | grep process_resident_memory_bytes

# Memory increase should be reasonable (< 100MB)
```

✅ **Pass Criteria**: Memory increase < 100MB during sync

### Step 9.3: Check CPU Usage

```bash
# Monitor Railway metrics during sync
railway logs --environment staging | grep -E "(cpu|memory)"

# Or check via Railway dashboard
```

✅ **Pass Criteria**: CPU usage < 80% during sync

### Step 9.4: Verify Database Query Performance

```sql
-- Check slow queries
SELECT
    query,
    calls,
    total_time,
    mean_time,
    max_time
FROM pg_stat_statements
WHERE query LIKE '%model_pricing%'
OR query LIKE '%pricing_sync%'
ORDER BY mean_time DESC
LIMIT 10;

-- Expected: Mean query time < 100ms
```

✅ **Pass Criteria**: Database queries < 100ms average

### Step 9.5: Load Test Manual Trigger

```bash
# Trigger 10 manual syncs sequentially
for i in {1..10}; do
  echo "Sync $i:"
  START=$(date +%s)
  curl -s -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
    $STAGING_URL/admin/pricing/scheduler/trigger | jq '.duration_seconds'
  END=$(date +%s)
  echo "Total time: $((END - START))s"
  sleep 5
done

# Expected: All complete successfully, consistent timing
```

✅ **Pass Criteria**: All syncs succeed, no degradation over time

---

## Part 10: Production Verification (Read-Only)

**Purpose**: Verify production is ready for deployment
**Time**: 20 minutes
**Environment**: Production

### Step 10.1: Check Production Configuration

```bash
# Verify production env vars (read-only check)
railway variables --environment production | grep PRICING_SYNC

# Expected:
# PRICING_SYNC_ENABLED should be set
# PRICING_SYNC_INTERVAL_HOURS should be 6 (not 3)
# PRICING_SYNC_PROVIDERS should include all 4 providers
```

✅ **Pass Criteria**: Configuration appropriate for production

### Step 10.2: Check Production Database

```sql
-- Verify migration applied
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('model_pricing_history', 'pricing_sync_log');

-- Expected: Both tables exist
```

✅ **Pass Criteria**: Migration applied to production

### Step 10.3: Check Production Health

```bash
# Check production health
curl $PROD_URL/health | jq '.'

# Expected: 200 OK, all systems healthy
```

✅ **Pass Criteria**: Production healthy

### Step 10.4: Verify Admin Endpoints (Read-Only)

```bash
# Get status (doesn't trigger changes)
curl -X GET -H "Authorization: Bearer $PROD_ADMIN_KEY" \
  $PROD_URL/admin/pricing/scheduler/status | jq '.'

# Expected: Returns status successfully
```

✅ **Pass Criteria**: Admin endpoints accessible

### Step 10.5: Check Production Metrics

```bash
# Check if metrics endpoint is accessible
curl $PROD_URL/metrics | grep pricing_ | head -10

# Expected: Pricing metrics present (may be 0 if not running yet)
```

✅ **Pass Criteria**: Metrics endpoint works

---

## Part 11: Monitoring Setup Verification

**Purpose**: Verify monitoring infrastructure is working
**Time**: 1-2 hours
**Environment**: Production (if deployed)

### Step 11.1: Verify Prometheus Scraping

```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="gatewayz-api")'

# Expected: Target is UP, last scrape successful
```

✅ **Pass Criteria**: Target UP, scraping successfully

### Step 11.2: Check Alert Rules

```bash
# Check alert rules loaded
curl http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name=="pricing_sync_scheduler")'

# Expected: All 10 alert rules present
```

✅ **Pass Criteria**: All alert rules loaded

### Step 11.3: Verify Grafana Dashboards

```bash
# Open Grafana dashboard
open http://localhost:3000/d/pricing-sync-health

# Manually verify:
# - All panels loading data
# - No "No Data" messages
# - Recent data visible
```

✅ **Pass Criteria**: Dashboards load and display data

### Step 11.4: Test Alert Firing (Optional)

```bash
# Temporarily disable scheduler to test alert
railway variables set PRICING_SYNC_ENABLED=false --environment staging
railway redeploy --environment staging

# Wait for alert threshold (8 hours or configured test threshold)
# Check Alertmanager
curl http://localhost:9093/api/v1/alerts

# Expected: PricingSyncSchedulerStopped alert firing

# Re-enable
railway variables set PRICING_SYNC_ENABLED=true --environment staging
railway redeploy --environment staging
```

✅ **Pass Criteria**: Alert fires and resolves correctly

### Step 11.5: Test Slack Notifications

```bash
# Trigger test alert to Slack
# Use Alertmanager amtool or trigger actual condition

# Verify notification received in Slack channel
```

✅ **Pass Criteria**: Slack notification received

---

## Part 12: End-to-End Integration Test

**Purpose**: Full workflow test
**Time**: 30 minutes
**Environment**: Staging

### Step 12.1: Complete Workflow Test

1. **Disable scheduler**:
   ```bash
   railway variables set PRICING_SYNC_ENABLED=false --environment staging
   railway redeploy --environment staging
   ```

2. **Verify stopped**:
   ```bash
   curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
     $STAGING_URL/admin/pricing/scheduler/status | jq '.scheduler.enabled'
   # Expected: false
   ```

3. **Re-enable**:
   ```bash
   railway variables set PRICING_SYNC_ENABLED=true --environment staging
   railway redeploy --environment staging
   ```

4. **Verify started**:
   ```bash
   railway logs --environment staging | grep "Pricing sync scheduler started"
   ```

5. **Trigger manual sync**:
   ```bash
   curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
     $STAGING_URL/admin/pricing/scheduler/trigger | jq '.'
   ```

6. **Verify database updated**:
   ```sql
   SELECT COUNT(*) FROM pricing_sync_log
   WHERE triggered_by = 'manual'
   AND sync_started_at > NOW() - INTERVAL '5 minutes';
   ```

7. **Check metrics updated**:
   ```bash
   curl $STAGING_URL/metrics | grep pricing_manual_sync_runs_total
   ```

8. **Wait for scheduled sync** (3 hours)

9. **Verify scheduled sync ran**:
   ```bash
   railway logs --environment staging | grep "Scheduled pricing sync completed"
   ```

10. **Check all systems healthy**:
    ```bash
    curl $STAGING_URL/health | jq '.'
    ```

✅ **Pass Criteria**: Complete workflow succeeds without errors

---

## Testing Summary

After completing all tests, fill out this summary:

### Overall Results

| Category | Tests | Passed | Failed | Notes |
|----------|-------|--------|--------|-------|
| Database Migration | 5 | | | |
| Scheduler Lifecycle | 5 | | | |
| Admin Endpoints | 6 | | | |
| Test Suite | 3 | | | |
| Metrics | 5 | | | |
| Scheduled Sync | 4 | | | |
| Error Handling | 6 | | | |
| Data Integrity | 5 | | | |
| Performance | 5 | | | |
| Production Verification | 5 | | | |
| Monitoring | 5 | | | |
| E2E Integration | 1 | | | |
| **TOTAL** | **55** | | | |

### Issues Found

| Issue # | Severity | Description | Workaround | Status |
|---------|----------|-------------|------------|--------|
| | | | | |

### Sign-Off

- [ ] All critical tests passed
- [ ] All issues documented
- [ ] Ready for production deployment
- [ ] Team notified of results

**Tested By**: _________________
**Date**: _________________
**Environment**: Staging ☐ Production ☐
**Duration**: _____ hours
**Overall Status**: ☐ PASS ☐ PASS WITH ISSUES ☐ FAIL

---

## Troubleshooting Common Issues

### Issue: Scheduler Not Starting

**Symptoms**: No "scheduler started" message in logs

**Checks**:
1. `PRICING_SYNC_ENABLED=true`?
2. Any import errors?
3. Application running?

**Solution**: Check Phase 6 runbook: `pricing_sync_scheduler_stopped.md`

### Issue: Sync Failing

**Symptoms**: `status="failed"` in metrics

**Checks**:
1. Provider API keys valid?
2. Database accessible?
3. Network connectivity?

**Solution**: Check Phase 6 runbook: `pricing_sync_high_error_rate.md`

### Issue: Slow Performance

**Symptoms**: Sync duration > 60s

**Checks**:
1. Provider API response times?
2. Database query performance?
3. Resource usage?

**Solution**: Check Phase 6 runbook: `pricing_sync_slow_performance.md`

### Issue: Metrics Not Collecting

**Symptoms**: `/metrics` endpoint returns no pricing metrics

**Checks**:
1. Scheduler actually running?
2. At least one sync completed?
3. Prometheus scraping configured?

**Solution**: Check Prometheus configuration, verify scheduler started

### Issue: Admin Endpoints Not Working

**Symptoms**: 404 or 500 errors

**Checks**:
1. Routes registered?
2. Admin authentication working?
3. Application deployed correctly?

**Solution**: Check deployment logs, verify Phase 3 code deployed

---

## Quick Reference Commands

### Staging
```bash
# Health check
curl https://gatewayz-staging.up.railway.app/health

# Scheduler status
curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status

# Manual trigger
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Metrics
curl https://gatewayz-staging.up.railway.app/metrics | grep pricing_

# Logs
railway logs --environment staging --follow
```

### Production
```bash
# Health check
curl https://api.gatewayz.ai/health

# Scheduler status
curl -H "Authorization: Bearer $PROD_ADMIN_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status

# Metrics
curl https://api.gatewayz.ai/metrics | grep pricing_
```

---

**Last Updated**: 2026-01-26
**Version**: 1.0
**Maintainer**: Platform Team
