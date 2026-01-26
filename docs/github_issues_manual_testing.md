# GitHub Issues for Manual Testing - Pricing Sync Scheduler

This document contains GitHub issue templates for tracking manual testing of the pricing sync scheduler. Create one issue for each section.

---

## Issue #1: Manual Testing - Database Migration Verification

**Title**: `[Testing] Verify database migration for pricing sync tables`

**Labels**: `testing`, `database`, `phase-6`, `critical`

**Assignee**: DevOps/Database team member

**Description**:

### Objective
Verify that the database migration `20260126000001_add_pricing_sync_tables.sql` was applied successfully to production and all tables are functioning correctly.

### Prerequisites
- [ ] Access to Supabase dashboard
- [ ] SQL query access to production database
- [ ] Migration file reviewed

### Testing Steps

#### 1. Verify Tables Exist
```sql
SELECT tablename, schemaname
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename;
```
- [ ] Both tables exist
- [ ] Tables in `public` schema

#### 2. Verify Table Structure
```sql
-- Check model_pricing_history columns
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'model_pricing_history'
ORDER BY ordinal_position;
```
- [ ] All expected columns present (8 columns)
- [ ] Correct data types

```sql
-- Check pricing_sync_log columns
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'pricing_sync_log'
ORDER BY ordinal_position;
```
- [ ] All expected columns present (12 columns)
- [ ] Correct data types

#### 3. Verify Indexes
```sql
SELECT indexname, tablename
FROM pg_indexes
WHERE tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename, indexname;
```
- [ ] 3 indexes on `model_pricing_history`
- [ ] 4 indexes on `pricing_sync_log`
- [ ] All indexes created successfully

#### 4. Verify RLS Policies
```sql
SELECT tablename, policyname, roles, cmd
FROM pg_policies
WHERE tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename, policyname;
```
- [ ] 2 policies on `model_pricing_history`
- [ ] 2 policies on `pricing_sync_log`
- [ ] Service role has full access
- [ ] Authenticated users have read-only access

#### 5. Test Insert/Delete
```sql
-- Test insert
INSERT INTO pricing_sync_log (
    provider_slug, sync_started_at, status, triggered_by
) VALUES ('test_manual', NOW(), 'success', 'manual_verification');

-- Verify
SELECT * FROM pricing_sync_log WHERE provider_slug = 'test_manual';

-- Cleanup
DELETE FROM pricing_sync_log WHERE provider_slug = 'test_manual';
```
- [ ] Insert successful
- [ ] Select returns data
- [ ] Delete successful

### Expected Results
✅ All tables, indexes, and policies created successfully
✅ Test insert/delete operations work
✅ No errors in migration process

### Pass Criteria
- [ ] All 5 test steps completed successfully
- [ ] No errors encountered
- [ ] Tables ready for use

### Reference
- Migration file: `supabase/migrations/20260126000001_add_pricing_sync_tables.sql`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 1)

---

## Issue #2: Manual Testing - Scheduler Lifecycle & Configuration

**Title**: `[Testing] Verify pricing sync scheduler lifecycle and configuration`

**Labels**: `testing`, `scheduler`, `phase-2.5`, `critical`

**Assignee**: Backend engineer

**Description**:

### Objective
Verify that the pricing sync scheduler starts correctly, runs on schedule, and can be controlled via environment variables.

### Prerequisites
- [ ] Access to staging environment
- [ ] Railway CLI access
- [ ] Admin API key for staging

### Environment
**Target**: Staging environment (`gatewayz-staging.up.railway.app`)

### Testing Steps

#### 1. Verify Configuration
```bash
railway variables --environment staging | grep PRICING_SYNC
```
Expected:
- [ ] `PRICING_SYNC_ENABLED=true`
- [ ] `PRICING_SYNC_INTERVAL_HOURS=3`
- [ ] `PRICING_SYNC_PROVIDERS=openrouter,featherless`

#### 2. Check Scheduler Started
```bash
railway logs --environment staging | grep "Pricing sync scheduler"
```
- [ ] "Pricing sync scheduler started" message present
- [ ] No errors during startup
- [ ] Interval and providers logged correctly

#### 3. Verify Initial Sync (30s delay)
```bash
railway logs --environment staging --follow | grep "pricing sync"
```
Wait 30-60 seconds:
- [ ] "Starting scheduled pricing sync" message
- [ ] "Scheduled pricing sync completed successfully" message
- [ ] Duration reasonable (< 60s)
- [ ] Models updated count > 0

#### 4. Check Background Task Running
```bash
curl https://gatewayz-staging.up.railway.app/health
```
- [ ] Health check returns 200 OK
- [ ] No errors reported

#### 5. Test Graceful Shutdown
```bash
railway redeploy --environment staging
railway logs --environment staging | grep shutdown
```
- [ ] "Scheduler received shutdown signal" message
- [ ] "Scheduler stopped gracefully" message
- [ ] No abrupt terminations

#### 6. Test Disable/Enable
```bash
# Disable
railway variables set PRICING_SYNC_ENABLED=false --environment staging
railway redeploy --environment staging
```
- [ ] Scheduler does not start when disabled
- [ ] Log message: "Pricing sync scheduler disabled"

```bash
# Re-enable
railway variables set PRICING_SYNC_ENABLED=true --environment staging
railway redeploy --environment staging
```
- [ ] Scheduler starts when re-enabled

### Expected Results
✅ Scheduler starts automatically on application startup
✅ Initial sync runs after 30 seconds
✅ Scheduler can be controlled via environment variables
✅ Graceful shutdown works correctly

### Pass Criteria
- [ ] All 6 test steps completed successfully
- [ ] Scheduler runs reliably
- [ ] Configuration changes work as expected

### Reference
- Scheduler code: `src/services/pricing_sync_scheduler.py`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 2)
- Phase 2.5 completion: `docs/PHASE_2.5_COMPLETION.md`

---

## Issue #3: Manual Testing - Admin Control Endpoints

**Title**: `[Testing] Verify admin pricing scheduler control endpoints`

**Labels**: `testing`, `api`, `phase-3`, `critical`

**Assignee**: Backend engineer

**Description**:

### Objective
Verify that admin endpoints for controlling and monitoring the pricing sync scheduler work correctly with proper authentication and authorization.

### Prerequisites
- [ ] Admin API key for staging
- [ ] `curl` or Postman
- [ ] Access to staging logs

### Environment
**Target**: Staging environment
**Base URL**: `https://gatewayz-staging.up.railway.app`

### Testing Steps

#### 1. Test Status Endpoint - Authenticated
```bash
curl -X GET \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status
```
- [ ] Returns 200 OK
- [ ] Response includes `success: true`
- [ ] `scheduler.enabled: true`
- [ ] `scheduler.running: true`
- [ ] Correct `interval_hours` (3)
- [ ] Correct `providers` list

#### 2. Test Status Endpoint - No Auth
```bash
curl -X GET \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status
```
- [ ] Returns 401 or 403 error
- [ ] Proper authentication error message

#### 3. Test Status Endpoint - Non-Admin User
```bash
curl -X GET \
  -H "Authorization: Bearer $REGULAR_USER_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status
```
- [ ] Returns 403 Forbidden
- [ ] Indicates insufficient permissions

#### 4. Test Manual Trigger - Success
```bash
curl -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger
```
- [ ] Returns 200 OK
- [ ] `success: true`
- [ ] `total_models_updated` > 0
- [ ] `duration_seconds` present and reasonable (< 60s)
- [ ] `triggered_by` includes admin email
- [ ] `triggered_at` timestamp present

#### 5. Verify Manual Trigger Logs
```bash
railway logs --environment staging | grep "Manual pricing sync"
```
- [ ] "Manual pricing sync triggered by admin" message
- [ ] Admin email logged
- [ ] Sync completion logged

#### 6. Test Manual Trigger - No Auth
```bash
curl -X POST \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger
```
- [ ] Returns 401 or 403 error
- [ ] No sync triggered

#### 7. Test Concurrent Triggers
```bash
# Trigger first sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger &

sleep 1

# Trigger second sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger
```
- [ ] Both requests complete (may queue or run concurrently)
- [ ] No crashes or errors
- [ ] Both syncs logged

#### 8. Verify Status After Manual Trigger
```bash
# Trigger sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Immediately check status
curl -X GET -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status
```
- [ ] Status reflects recent manual sync
- [ ] Timestamp is recent (< 5 seconds)

### Expected Results
✅ Admin endpoints accessible with proper authentication
✅ Non-admin users properly rejected
✅ Manual trigger works correctly
✅ All operations logged for audit

### Pass Criteria
- [ ] All 8 test steps completed successfully
- [ ] Authentication/authorization working correctly
- [ ] Manual trigger reliable and audited

### Reference
- Admin endpoints: `src/routes/admin.py` (lines 925-1049)
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 3)
- Phase 3 completion: `docs/PHASE_3_COMPLETION.md`

---

## Issue #4: Manual Testing - Automated Test Suite Execution

**Title**: `[Testing] Run automated test suite for pricing scheduler`

**Labels**: `testing`, `automation`, `phase-4`, `high-priority`

**Assignee**: QA engineer or Backend engineer

**Description**:

### Objective
Execute the comprehensive automated test suite to verify all pricing sync scheduler functionality.

### Prerequisites
- [ ] Local development environment set up
- [ ] Python dependencies installed
- [ ] pytest and coverage tools available

### Environment
**Target**: Local development

### Testing Steps

#### 1. Run Scheduler Unit Tests
```bash
cd /path/to/gatewayz-backend
pytest tests/services/test_pricing_sync_scheduler.py -v
```
Expected: 18 tests pass
- [ ] TestSchedulerLifecycle: 4 tests pass
- [ ] TestSchedulerStatus: 3 tests pass
- [ ] TestManualTrigger: 3 tests pass
- [ ] TestSchedulerLoop: 2 tests pass
- [ ] TestErrorHandling: 2 tests pass
- [ ] TestPrometheusMetrics: 2 tests pass
- [ ] TestConfiguration: 2 tests pass
- [ ] No failures or errors
- [ ] All assertions pass

#### 2. Run Admin Endpoint Tests
```bash
pytest tests/routes/test_admin.py::TestPricingSchedulerStatus -v
pytest tests/routes/test_admin.py::TestPricingSchedulerTrigger -v
pytest tests/routes/test_admin.py::TestPricingSchedulerIntegration -v
```
Expected: 12 tests pass
- [ ] TestPricingSchedulerStatus: 4 tests pass
- [ ] TestPricingSchedulerTrigger: 6 tests pass
- [ ] TestPricingSchedulerIntegration: 2 tests pass
- [ ] No failures or errors

#### 3. Run with Coverage Report
```bash
pytest tests/services/test_pricing_sync_scheduler.py \
       tests/routes/test_admin.py \
       --cov=src/services/pricing_sync_scheduler \
       --cov=src/routes/admin \
       --cov-report=term \
       --cov-report=html
```
- [ ] Coverage ≥ 85%
- [ ] HTML report generated
- [ ] All tests pass

#### 4. Check Coverage Report
```bash
open htmlcov/index.html
```
Review:
- [ ] Scheduler module coverage ≥ 85%
- [ ] Admin endpoints coverage ≥ 90%
- [ ] No critical uncovered code paths

#### 5. Run in Parallel (Optional)
```bash
pytest tests/services/test_pricing_sync_scheduler.py \
       tests/routes/test_admin.py \
       -n auto -v
```
- [ ] Tests run successfully in parallel
- [ ] No race conditions or failures

### Expected Results
✅ All 30 automated tests pass
✅ Coverage targets met (≥85%)
✅ No flaky tests or intermittent failures

### Pass Criteria
- [ ] All test suites execute successfully
- [ ] Coverage meets or exceeds targets
- [ ] No test failures

### Blockers/Issues
Document any test failures:
- Test name:
- Error message:
- Expected vs actual:
- Reproduction steps:

### Reference
- Test files:
  - `tests/services/test_pricing_sync_scheduler.py` (492 lines, 18 tests)
  - `tests/routes/test_admin.py` (scheduler tests section, 12 tests)
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 4)
- Phase 4 completion: `docs/PHASE_4_COMPLETION.md`

---

## Issue #5: Manual Testing - Prometheus Metrics Collection

**Title**: `[Testing] Verify Prometheus metrics for pricing scheduler`

**Labels**: `testing`, `metrics`, `observability`, `phase-2.5`, `high-priority`

**Assignee**: DevOps or Backend engineer

**Description**:

### Objective
Verify that all Prometheus metrics are being collected correctly and can be queried for monitoring purposes.

### Prerequisites
- [ ] Access to staging environment
- [ ] Metrics endpoint accessible
- [ ] `curl` and `jq` available

### Environment
**Target**: Staging environment
**Metrics URL**: `https://gatewayz-staging.up.railway.app/metrics`

### Testing Steps

#### 1. Verify Metrics Endpoint Accessible
```bash
curl https://gatewayz-staging.up.railway.app/metrics | grep pricing_
```
- [ ] Returns 200 OK
- [ ] Pricing metrics present

#### 2. Check Sync Run Metrics
```bash
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_scheduled_sync_runs_total'
```
Expected metrics:
- [ ] `pricing_scheduled_sync_runs_total{status="success"}` present
- [ ] `pricing_scheduled_sync_runs_total{status="failed"}` present
- [ ] Values are incrementing over time

#### 3. Check Duration Metrics
```bash
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_scheduled_sync_duration_seconds'
```
- [ ] `pricing_scheduled_sync_duration_seconds_bucket` present (histogram)
- [ ] `pricing_scheduled_sync_duration_seconds_sum` present
- [ ] `pricing_scheduled_sync_duration_seconds_count` present
- [ ] Values reasonable (< 60s typically)

#### 4. Check Per-Provider Metrics
```bash
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_last_sync_timestamp'
```
- [ ] Metric for `provider="openrouter"` present
- [ ] Metric for `provider="featherless"` present
- [ ] Timestamps are recent (Unix timestamp format)

```bash
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_models_synced_total'
```
- [ ] Metrics for both providers present
- [ ] Counts > 0

#### 5. Trigger Sync and Verify Metrics Update
```bash
# Get current success count
BEFORE=$(curl -s https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_scheduled_sync_runs_total{status="success"}' | \
  awk '{print $2}')

echo "Before: $BEFORE"

# Trigger manual sync
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Wait for completion
sleep 15

# Get updated success count
AFTER=$(curl -s https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_scheduled_sync_runs_total{status="success"}' | \
  awk '{print $2}')

echo "After: $AFTER"
echo "Increment: $((AFTER - BEFORE))"
```
- [ ] Metrics incremented after sync
- [ ] Increment equals 1 (or 2 if both providers tracked separately)

#### 6. Check Manual Trigger Metrics
```bash
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_manual_sync_runs_total'
```
- [ ] `pricing_manual_sync_runs_total` present
- [ ] Increments when manual trigger used

### Expected Results
✅ All Prometheus metrics collecting correctly
✅ Metrics update in real-time
✅ Values are accurate and reasonable

### Pass Criteria
- [ ] All 6 test steps completed successfully
- [ ] Metrics available for monitoring
- [ ] No missing or stale metrics

### Reference
- Metrics implementation: `src/services/pricing_sync_scheduler.py:71-83`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 5)
- Metrics list: `docs/PHASE_6_COMPLETION.md` (Metrics Reference section)

---

## Issue #6: Manual Testing - Scheduled Sync Verification

**Title**: `[Testing] Verify pricing scheduler runs on schedule (3-hour wait test)`

**Labels**: `testing`, `scheduler`, `long-running`, `phase-2.5`, `high-priority`

**Assignee**: Backend engineer or QA

**Description**:

### Objective
Verify that the pricing sync scheduler runs automatically on the configured interval without manual intervention.

### Prerequisites
- [ ] Scheduler enabled in staging
- [ ] Interval set to 3 hours
- [ ] Access to logs and metrics

### Environment
**Target**: Staging environment
**Duration**: 3+ hours (one complete sync cycle)

### ⏰ Important Note
This is a **time-intensive test** requiring 3+ hours of monitoring. Plan accordingly and consider running overnight or during off-peak hours.

### Testing Steps

#### 1. Check Current Configuration
```bash
railway variables --environment staging | grep PRICING_SYNC_INTERVAL_HOURS
```
- [ ] Interval is 3 hours

#### 2. Record Last Sync Time
```bash
LAST_SYNC=$(curl -s https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_last_sync_timestamp' | head -1 | awk '{print $2}')

CURRENT_TIME=$(date +%s)
NEXT_SYNC=$((LAST_SYNC + 10800))  # +3 hours

echo "Last sync timestamp: $LAST_SYNC"
echo "Current time: $CURRENT_TIME"
echo "Next sync expected at: $(date -r $NEXT_SYNC)"
echo "Wait time: $(( (NEXT_SYNC - CURRENT_TIME) / 60 )) minutes"
```
- [ ] Recorded last sync time
- [ ] Calculated next expected sync time

#### 3. Monitor Logs for Next Sync
```bash
# Set up continuous monitoring
railway logs --environment staging --follow | \
  grep --line-buffered "Starting scheduled pricing sync"
```

Wait for next sync (up to 3 hours + 10 minutes buffer):
- [ ] "Starting scheduled pricing sync" appears in logs
- [ ] Approximately 3 hours after last sync (±5 minutes acceptable)

#### 4. Verify Sync Completion
```bash
railway logs --environment staging | \
  grep "Scheduled pricing sync completed" | tail -1
```
- [ ] Sync completed successfully
- [ ] Duration < 60 seconds
- [ ] Models updated count > 0
- [ ] No errors reported

#### 5. Check Metrics Updated
```bash
# Check that metrics reflect new sync
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_last_sync_timestamp'
```
- [ ] Timestamps updated to recent time
- [ ] Success count incremented
- [ ] Duration metrics updated

#### 6. Verify Database Logs
```sql
SELECT
    provider_slug,
    sync_started_at,
    sync_completed_at,
    status,
    models_updated,
    duration_ms,
    triggered_by
FROM pricing_sync_log
WHERE triggered_by = 'scheduler'
ORDER BY sync_started_at DESC
LIMIT 5;
```
- [ ] New sync row with `triggered_by='scheduler'`
- [ ] Status is 'success'
- [ ] Models updated > 0
- [ ] Duration reasonable

#### 7. Optional: Wait for Second Cycle
If time allows, wait for another 3 hours to verify consistency:
- [ ] Second scheduled sync occurs 3 hours after first
- [ ] No drift in schedule timing
- [ ] Consistent success rate

### Expected Results
✅ Scheduler runs automatically every 3 hours
✅ Syncs complete successfully
✅ No manual intervention required
✅ Timing is consistent (±5 minutes)

### Pass Criteria
- [ ] At least 1 complete scheduled sync observed
- [ ] Sync timing within 5 minutes of expected
- [ ] All metrics and logs updated correctly
- [ ] No errors or failures

### Time Log
- Start time: ___________
- Last sync: ___________
- Expected next sync: ___________
- Actual next sync: ___________
- Variance: ___________ minutes

### Reference
- Scheduler code: `src/services/pricing_sync_scheduler.py:130-186`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 6)

---

## Issue #7: Manual Testing - Error Handling & Recovery

**Title**: `[Testing] Verify error handling and recovery mechanisms`

**Labels**: `testing`, `error-handling`, `resilience`, `phase-2.5`, `high-priority`

**Assignee**: Backend engineer

**Description**:

### Objective
Verify that the pricing sync scheduler handles errors gracefully and recovers without crashing or requiring manual intervention.

### Prerequisites
- [ ] Access to staging environment
- [ ] Ability to modify environment variables
- [ ] Access to logs and Sentry

### Environment
**Target**: Staging environment
**Risk**: Medium (intentionally causing errors)

### ⚠️ Important Notes
- This test intentionally causes errors
- Use staging environment only
- Have rollback plan ready
- Monitor closely during test

### Testing Steps

#### 1. Test Invalid Provider Configuration
```bash
# Add invalid provider to list
railway variables set \
  PRICING_SYNC_PROVIDERS=openrouter,featherless,invalid_provider \
  --environment staging

railway redeploy --environment staging

# Monitor logs
railway logs --environment staging --follow | grep -E "(error|invalid)"
```
- [ ] Errors logged for invalid provider
- [ ] Scheduler continues running
- [ ] Valid providers still sync successfully
- [ ] No application crash

#### 2. Check Error Metrics
```bash
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep 'pricing_scheduled_sync_runs_total{status="failed"}'
```
- [ ] Failed count incremented for invalid provider
- [ ] Success count still incrementing for valid providers

#### 3. Verify Sentry Integration
- [ ] Go to Sentry dashboard
- [ ] Filter by component: `pricing_sync`
- [ ] Check recent errors logged
- [ ] Verify proper context (provider, error message)

#### 4. Test Provider API Timeout Handling
```bash
# Check logs for any timeout errors
railway logs --environment staging | grep -i timeout
```
- [ ] Timeout errors handled gracefully
- [ ] No crashes from timeouts
- [ ] Other providers continue syncing

#### 5. Test Database Connectivity (Limited)
```bash
# Check for database errors (should be minimal)
railway logs --environment staging | grep -i "database.*error"
```
- [ ] No persistent database connection errors
- [ ] Any transient errors are retried

#### 6. Restore Valid Configuration
```bash
# Restore correct configuration
railway variables set \
  PRICING_SYNC_PROVIDERS=openrouter,featherless \
  --environment staging

railway redeploy --environment staging
```
- [ ] Configuration restored
- [ ] Scheduler resumes normal operation
- [ ] No lingering effects from errors

#### 7. Verify Recovery
```bash
# Trigger manual sync to verify system healthy
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger
```
- [ ] Manual sync succeeds after recovery
- [ ] All providers syncing normally
- [ ] Error rate returns to 0

### Expected Results
✅ Errors logged appropriately
✅ System continues operating despite errors
✅ Errors sent to Sentry with context
✅ Recovery automatic after configuration fix

### Pass Criteria
- [ ] All 7 test steps completed
- [ ] No application crashes from errors
- [ ] Error handling as designed
- [ ] System recovers automatically

### Reference
- Error handling code: `src/services/pricing_sync_scheduler.py:195-211`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 7)
- Runbooks: `docs/runbooks/pricing_sync_high_error_rate.md`

---

## Issue #8: Manual Testing - Data Integrity & Accuracy

**Title**: `[Testing] Verify pricing data integrity and update accuracy`

**Labels**: `testing`, `data-integrity`, `database`, `phase-2`, `critical`

**Assignee**: Backend engineer or Data engineer

**Description**:

### Objective
Verify that pricing data is being updated correctly, pricing changes are logged to history, and no data corruption occurs during sync operations.

### Prerequisites
- [ ] Access to staging database
- [ ] SQL query access
- [ ] Admin API key for triggering syncs

### Environment
**Target**: Staging database

### Testing Steps

#### 1. Check Current Pricing Data
```sql
-- Get baseline pricing for test models
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
- [ ] Pricing data exists for test models
- [ ] Prices are non-negative
- [ ] Updated timestamps are reasonable

#### 2. Trigger Sync
```bash
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Wait for completion
sleep 20
```

#### 3. Verify Pricing Updated
```sql
-- Check updated pricing
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
- [ ] `updated_at` timestamps are recent (< 2 minutes)
- [ ] Prices are still non-negative
- [ ] No NULL values where there shouldn't be

#### 4. Check Pricing History
```sql
-- Verify pricing changes are logged
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
WHERE m.model_id IN (
    'openrouter/anthropic/claude-3.5-sonnet',
    'openrouter/openai/gpt-4',
    'featherless/mistralai/mistral-7b-instruct'
)
ORDER BY mph.changed_at DESC
LIMIT 10;
```
- [ ] Recent entries exist (< 2 minutes old)
- [ ] `changed_by` format is correct (e.g., 'scheduler:openrouter')
- [ ] Previous prices match old values (if prices changed)
- [ ] Current prices match new values

#### 5. Verify No Duplicate Syncs
```sql
-- Check for concurrent syncs (shouldn't happen)
SELECT
    provider_slug,
    COUNT(*) as concurrent_syncs
FROM pricing_sync_log
WHERE sync_completed_at IS NULL
AND sync_started_at > NOW() - INTERVAL '1 hour'
GROUP BY provider_slug
HAVING COUNT(*) > 1;
```
- [ ] Query returns 0 rows
- [ ] No overlapping syncs for same provider

#### 6. Check Sync Logs
```sql
-- Review recent sync operations
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
```
- [ ] Recent syncs logged
- [ ] Status is 'success' for most recent
- [ ] models_updated > 0
- [ ] models_fetched >= models_updated
- [ ] duration_ms is reasonable (< 60000ms)
- [ ] No unexplained errors

#### 7. Validate Data Consistency
```sql
-- Check for any NULL prices (shouldn't exist for active models)
SELECT
    m.model_id,
    m.model_name,
    mp.input_price_per_1m_tokens,
    mp.output_price_per_1m_tokens
FROM models m
JOIN model_pricing mp ON m.id = mp.model_id
WHERE mp.input_price_per_1m_tokens IS NULL
   OR mp.output_price_per_1m_tokens IS NULL
LIMIT 10;
```
- [ ] No NULL prices for active models
- [ ] If NULLs exist, document as known issue

```sql
-- Check for negative prices (data integrity violation)
SELECT
    m.model_id,
    m.model_name,
    mp.input_price_per_1m_tokens,
    mp.output_price_per_1m_tokens
FROM models m
JOIN model_pricing mp ON m.id = mp.model_id
WHERE mp.input_price_per_1m_tokens < 0
   OR mp.output_price_per_1m_tokens < 0;
```
- [ ] No negative prices exist
- [ ] Check constraints preventing negative prices

### Expected Results
✅ Pricing data updates correctly after sync
✅ Pricing history logged for audit trail
✅ No data corruption or integrity violations
✅ Sync logs accurate and complete

### Pass Criteria
- [ ] All 7 test steps completed successfully
- [ ] Data integrity maintained
- [ ] History tracking working
- [ ] No anomalies detected

### Reference
- Pricing sync service: `src/services/pricing_sync_service.py`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 8)
- Migration: `supabase/migrations/20260126000001_add_pricing_sync_tables.sql`

---

## Issue #9: Manual Testing - Performance & Resource Usage

**Title**: `[Testing] Verify pricing scheduler performance and resource usage`

**Labels**: `testing`, `performance`, `optimization`, `phase-2.5`, `medium-priority`

**Assignee**: Backend engineer or DevOps

**Description**:

### Objective
Verify that the pricing sync scheduler performs efficiently and doesn't consume excessive resources (CPU, memory, database connections).

### Prerequisites
- [ ] Access to staging environment
- [ ] Metrics endpoint access
- [ ] Railway dashboard access
- [ ] Admin API key

### Environment
**Target**: Staging environment

### Testing Steps

#### 1. Baseline Resource Usage
```bash
# Check memory before sync
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep process_resident_memory_bytes

# Check CPU usage
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep process_cpu_seconds_total
```
- [ ] Record baseline memory usage
- [ ] Record baseline CPU usage

#### 2. Measure Sync Duration
```bash
# Trigger manual sync and measure time
START=$(date +%s)

RESPONSE=$(curl -s -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger)

END=$(date +%s)
API_DURATION=$((END - START))

SYNC_DURATION=$(echo $RESPONSE | jq -r '.duration_seconds')

echo "API Response Time: ${API_DURATION}s"
echo "Sync Duration: ${SYNC_DURATION}s"
```
- [ ] Sync duration < 60 seconds
- [ ] API response time < 5 seconds
- [ ] Duration consistent across multiple runs

#### 3. Check Memory During Sync
```bash
# Check memory after sync
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep process_resident_memory_bytes
```
- [ ] Memory increase < 100MB during sync
- [ ] Memory returns to baseline after sync
- [ ] No memory leaks over multiple syncs

#### 4. Monitor CPU Usage
```bash
# Check Railway dashboard or metrics
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep -E "(cpu|process_cpu)"
```
- [ ] CPU usage < 80% during sync
- [ ] CPU returns to idle after sync
- [ ] No sustained high CPU usage

#### 5. Check Database Query Performance
```sql
-- Check recent query performance
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
```
- [ ] Mean query time < 100ms
- [ ] No queries > 1 second
- [ ] Query performance acceptable

#### 6. Load Test Manual Trigger
```bash
# Run 10 consecutive manual syncs
for i in {1..10}; do
  echo "=== Sync $i ==="
  START=$(date +%s)

  RESPONSE=$(curl -s -X POST \
    -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
    https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger)

  END=$(date +%s)
  DURATION=$((END - START))
  SYNC_DURATION=$(echo $RESPONSE | jq -r '.duration_seconds')

  echo "API Time: ${DURATION}s, Sync Time: ${SYNC_DURATION}s"

  sleep 5
done
```
- [ ] All 10 syncs complete successfully
- [ ] Duration consistent (no degradation)
- [ ] No errors or timeouts
- [ ] No memory/CPU spikes

#### 7. Check Connection Pool
```bash
# Check database connection metrics
curl https://gatewayz-staging.up.railway.app/metrics | \
  grep db_connection_pool
```
- [ ] Connection pool not exhausted
- [ ] Active connections reasonable (< 10)
- [ ] No connection leaks

#### 8. Verify System Stability
```bash
# Check application health after load test
curl https://gatewayz-staging.up.railway.app/health
```
- [ ] Health check passes
- [ ] No errors or warnings
- [ ] System stable after testing

### Performance Benchmarks

| Metric | Target | Actual | Pass/Fail |
|--------|--------|--------|-----------|
| Sync Duration (avg) | < 30s | | |
| Sync Duration (p95) | < 60s | | |
| API Response Time | < 5s | | |
| Memory Usage Increase | < 100MB | | |
| CPU Usage (peak) | < 80% | | |
| DB Query Time (avg) | < 100ms | | |
| Connection Pool Usage | < 50% | | |

### Expected Results
✅ Sync completes in reasonable time (< 60s)
✅ Memory and CPU usage acceptable
✅ No resource leaks
✅ Performance consistent under load

### Pass Criteria
- [ ] All 8 test steps completed
- [ ] All benchmark targets met
- [ ] No performance issues identified
- [ ] System stable after testing

### Reference
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 9)
- Runbook: `docs/runbooks/pricing_sync_slow_performance.md`

---

## Issue #10: Manual Testing - Production Readiness Check

**Title**: `[Testing] Production readiness verification (read-only checks)`

**Labels**: `testing`, `production`, `deployment`, `phase-5`, `critical`

**Assignee**: DevOps lead or Engineering manager

**Description**:

### Objective
Verify that production environment is correctly configured and ready for pricing scheduler deployment. This test performs **read-only checks** and does not trigger any syncs or changes.

### Prerequisites
- [ ] Access to production environment (read-only)
- [ ] Admin API key for production
- [ ] Supabase production access

### Environment
**Target**: Production environment
**Mode**: Read-only verification

### ⚠️ Important
This test performs **read-only checks only**. Do NOT trigger syncs or make changes to production until all checks pass and deployment is approved.

### Testing Steps

#### 1. Verify Environment Variables
```bash
# Check production configuration (read-only)
railway variables --environment production | grep PRICING_SYNC

# Expected configuration:
# PRICING_SYNC_ENABLED=true (or false, ready to enable)
# PRICING_SYNC_INTERVAL_HOURS=6 (NOT 3)
# PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud
```
- [ ] Variables exist
- [ ] Interval is 6 hours (production setting)
- [ ] All 4 providers configured
- [ ] Scheduler enabled or ready to enable

#### 2. Verify Database Migration
```sql
-- Check production database for required tables
SELECT tablename, schemaname
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename;
```
- [ ] `model_pricing_history` table exists
- [ ] `pricing_sync_log` table exists
- [ ] Both in `public` schema

#### 3. Check Production Health
```bash
# Verify production application is healthy
curl https://api.gatewayz.ai/health
```
- [ ] Returns 200 OK
- [ ] All health checks passing
- [ ] No errors reported

#### 4. Verify Admin Endpoints Available
```bash
# Check admin status endpoint (read-only)
curl -X GET \
  -H "Authorization: Bearer $PROD_ADMIN_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status
```
- [ ] Returns 200 OK (or 404 if not deployed yet)
- [ ] Proper authentication working
- [ ] Response structure correct

#### 5. Check Metrics Endpoint
```bash
# Check if metrics endpoint works
curl https://api.gatewayz.ai/metrics | grep pricing_ | head -5
```
- [ ] Metrics endpoint accessible
- [ ] Pricing metrics present (may be 0 if not running)
- [ ] No errors

#### 6. Verify Monitoring Infrastructure
- [ ] Prometheus configured to scrape production
- [ ] Grafana dashboards can access production
- [ ] Alert rules will apply to production
- [ ] Slack/PagerDuty notifications configured

#### 7. Review Deployment Checklist
Verify all Phase 5 pre-deployment items:
- [ ] Code merged to main branch
- [ ] All tests passing in CI/CD
- [ ] Staging verification complete (24-48h)
- [ ] Database migration applied
- [ ] Documentation complete
- [ ] Team notified
- [ ] Rollback plan prepared

#### 8. Validate Configuration Differences

| Setting | Staging | Production | Correct? |
|---------|---------|------------|----------|
| Interval | 3 hours | 6 hours | [ ] |
| Providers | 2 | 4 | [ ] |
| Monitoring | Optional | Required | [ ] |

### Pre-Deployment Checklist

#### Code Deployment
- [ ] Phase 2.5 code in main branch
- [ ] Phase 3 code in main branch
- [ ] All dependencies updated
- [ ] Environment variables documented

#### Database
- [ ] Migration applied successfully
- [ ] Tables verified
- [ ] RLS policies active
- [ ] No data corruption

#### Monitoring
- [ ] Prometheus ready
- [ ] Grafana dashboards imported
- [ ] Alerts configured
- [ ] Runbooks accessible

#### Communication
- [ ] Team notified of deployment
- [ ] Deployment window scheduled
- [ ] On-call roster updated
- [ ] Rollback plan communicated

#### Approval
- [ ] Engineering lead approval
- [ ] DevOps approval
- [ ] Product owner notified

### Expected Results
✅ Production environment correctly configured
✅ All prerequisites met
✅ Ready for deployment

### Pass Criteria
- [ ] All 8 verification steps pass
- [ ] Pre-deployment checklist complete
- [ ] All approvals obtained
- [ ] No blocking issues

### Deployment Authorization
**Approved by**: __________________
**Date**: __________________
**Deployment window**: __________________

### Reference
- Deployment guide: `docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- Deployment checklist: `docs/DEPLOYMENT_CHECKLIST.md`
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 10)

---

## Issue #11: Manual Testing - Monitoring Infrastructure Setup

**Title**: `[Testing] Deploy and verify Phase 6 monitoring infrastructure`

**Labels**: `testing`, `monitoring`, `observability`, `phase-6`, `high-priority`

**Assignee**: DevOps engineer

**Description**:

### Objective
Deploy and verify the Phase 6 monitoring infrastructure including Prometheus alerts, Grafana dashboards, and Alertmanager routing.

### Prerequisites
- [ ] Prometheus admin access
- [ ] Grafana admin access
- [ ] Alertmanager configuration access
- [ ] Slack webhook URLs
- [ ] PagerDuty integration key (if using)

### Time Estimate
**4-6 hours** for complete setup and verification

### Setup Guide Reference
Follow: `docs/PHASE_6_MONITORING_SETUP_GUIDE.md`

### Part 1: Prometheus Setup (1 hour)

#### 1.1 Configure Prometheus Scraping
- [ ] Add scrape config for production
- [ ] Reload Prometheus configuration
- [ ] Verify scraping working
- [ ] Test PromQL queries

#### 1.2 Deploy Alert Rules
```bash
# Copy alert rules file
cp monitoring/prometheus/pricing_sync_alerts.yml /etc/prometheus/rules/

# Validate rules
promtool check rules /etc/prometheus/rules/pricing_sync_alerts.yml

# Reload Prometheus
curl -X POST http://localhost:9090/-/reload
```
- [ ] All 10 alert rules loaded
- [ ] No validation errors
- [ ] Rules visible in Prometheus UI

### Part 2: Grafana Dashboards (1 hour)

#### 2.1 Import Health Dashboard
```bash
# Via Grafana UI or API
curl -X POST http://admin:password@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @monitoring/grafana/pricing_sync_scheduler_health.json
```
- [ ] Health dashboard imported
- [ ] All 13 panels loading data
- [ ] No "No Data" errors
- [ ] Variables working

#### 2.2 Import System Impact Dashboard
- [ ] System Impact dashboard imported
- [ ] All 13 panels loading data
- [ ] Resource metrics visible
- [ ] Database metrics visible

#### 2.3 Configure Data Source
- [ ] Prometheus data source added
- [ ] Connection successful
- [ ] Test query works

### Part 3: Alertmanager Configuration (1 hour)

#### 3.1 Configure Alert Routing
```yaml
# alertmanager.yml configuration
route:
  routes:
    - match:
        severity: critical
        component: pricing_sync
      receiver: 'pagerduty-platform'
      continue: true
    - match:
        severity: critical
      receiver: 'slack-critical'
    - match:
        severity: warning
      receiver: 'slack-warnings'
```
- [ ] Routing rules configured
- [ ] Receivers configured
- [ ] Configuration validated

#### 3.2 Set Up Slack Integration
- [ ] Webhook URLs obtained
- [ ] Channels created (#platform-critical, #platform-warnings)
- [ ] Test notifications successful
- [ ] Message formatting correct

#### 3.3 Set Up PagerDuty (Optional)
- [ ] Service created
- [ ] Integration key obtained
- [ ] Test page successful
- [ ] Escalation policy configured

### Part 4: Verification (1 hour)

#### 4.1 Test Alert Firing
```bash
# Trigger test alert (staging only)
railway variables set PRICING_SYNC_ENABLED=false --environment staging
# Wait for alert threshold
# Verify alert fires in Alertmanager
# Verify notification received
# Re-enable scheduler
```
- [ ] Alert fires correctly
- [ ] Notification received in Slack
- [ ] PagerDuty page sent (if configured)
- [ ] Alert resolves when fixed

#### 4.2 Verify Runbooks
- [ ] Runbooks accessible to team
- [ ] Links in alerts working
- [ ] Content clear and actionable

#### 4.3 End-to-End Test
- [ ] Metrics collecting
- [ ] Dashboards displaying
- [ ] Alerts evaluating
- [ ] Notifications routing
- [ ] Team can access all systems

### Part 5: Documentation (30 min)

#### 5.1 Team Training
- [ ] Schedule training session
- [ ] Walk through dashboards
- [ ] Review runbooks
- [ ] Practice alert response

#### 5.2 Access Documentation
- [ ] Document who has access
- [ ] Share credentials securely
- [ ] Create quick reference guide

### Verification Checklist

**Prometheus**:
- [ ] Scraping production successfully
- [ ] All alert rules loaded
- [ ] Queries return data
- [ ] No configuration errors

**Grafana**:
- [ ] Both dashboards imported
- [ ] All panels loading data
- [ ] Filters and variables working
- [ ] Team has view access

**Alertmanager**:
- [ ] Configuration valid
- [ ] Routing rules working
- [ ] Receivers configured
- [ ] Test alerts successful

**Integration**:
- [ ] Slack notifications working
- [ ] PagerDuty integration working
- [ ] Runbooks accessible
- [ ] Team trained

### Expected Results
✅ Complete monitoring infrastructure deployed
✅ All components working together
✅ Team ready to use monitoring
✅ Alerts will fire when needed

### Pass Criteria
- [ ] All setup steps completed
- [ ] Verification checklist passed
- [ ] End-to-end test successful
- [ ] Team trained

### Reference
- Setup guide: `docs/PHASE_6_MONITORING_SETUP_GUIDE.md`
- Alert rules: `monitoring/prometheus/pricing_sync_alerts.yml`
- Dashboards: `monitoring/grafana/*.json`
- Runbooks: `docs/runbooks/pricing_sync_*.md`

---

## Issue #12: Manual Testing - End-to-End Integration Test

**Title**: `[Testing] Complete end-to-end workflow integration test`

**Labels**: `testing`, `integration`, `e2e`, `critical`, `all-phases`

**Assignee**: QA lead or Senior engineer

**Description**:

### Objective
Execute a complete end-to-end test of the entire pricing sync scheduler system, from configuration through monitoring, to verify all components work together correctly.

### Prerequisites
- [ ] All previous manual tests completed
- [ ] All systems deployed
- [ ] Access to all environments
- [ ] Time allocated for full test (2-3 hours)

### Environment
**Target**: Staging initially, then Production

### Complete Workflow Test

#### Phase 1: Setup & Configuration (15 min)

1. **Disable Scheduler**
```bash
railway variables set PRICING_SYNC_ENABLED=false --environment staging
railway redeploy --environment staging
```
- [ ] Scheduler stops
- [ ] Log message confirms disabled

2. **Verify Stopped State**
```bash
curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/status | jq '.scheduler.enabled'
```
- [ ] Returns `false`
- [ ] Metrics show no new syncs

#### Phase 2: Enable & Startup (15 min)

3. **Re-enable Scheduler**
```bash
railway variables set PRICING_SYNC_ENABLED=true --environment staging
railway redeploy --environment staging
```
- [ ] Application restarts successfully
- [ ] No startup errors

4. **Verify Scheduler Started**
```bash
railway logs --environment staging | grep "Pricing sync scheduler started"
```
- [ ] Startup message present
- [ ] Configuration logged correctly

5. **Wait for Initial Sync**
- [ ] Initial sync starts after 30 seconds
- [ ] Completes successfully
- [ ] Duration < 60 seconds

#### Phase 3: Manual Control (15 min)

6. **Check Status via Admin API**
```bash
curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/status | jq '.'
```
- [ ] Status shows enabled=true, running=true
- [ ] Correct interval and providers

7. **Trigger Manual Sync**
```bash
curl -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/trigger | jq '.'
```
- [ ] Sync executes successfully
- [ ] Models updated > 0
- [ ] Admin email logged

8. **Verify Manual Sync in Logs**
```bash
railway logs --environment staging | grep "Manual pricing sync"
```
- [ ] Manual trigger logged
- [ ] Completion logged
- [ ] No errors

#### Phase 4: Data Verification (20 min)

9. **Check Database Updates**
```sql
-- Verify pricing updated
SELECT COUNT(*) FROM model_pricing
WHERE updated_at > NOW() - INTERVAL '5 minutes';

-- Check sync logs
SELECT * FROM pricing_sync_log
ORDER BY sync_started_at DESC LIMIT 5;

-- Check pricing history
SELECT COUNT(*) FROM model_pricing_history
WHERE changed_at > NOW() - INTERVAL '5 minutes';
```
- [ ] Pricing data updated
- [ ] Sync logged in database
- [ ] History recorded

10. **Verify Data Integrity**
```sql
-- Check for invalid data
SELECT COUNT(*) FROM model_pricing
WHERE input_price_per_1m_tokens < 0
   OR output_price_per_1m_tokens < 0;
```
- [ ] No negative prices
- [ ] No NULL prices for active models

#### Phase 5: Metrics & Monitoring (20 min)

11. **Check Prometheus Metrics**
```bash
curl $STAGING_URL/metrics | grep pricing_ | head -20
```
- [ ] All metrics present
- [ ] Success count > 0
- [ ] Duration metrics reasonable
- [ ] Timestamps recent

12. **View Grafana Dashboards**
- [ ] Open Health dashboard
- [ ] All panels showing data
- [ ] Success rate visible
- [ ] Duration chart populated

13. **Check Alerts (if deployed)**
```bash
curl http://localhost:9090/api/v1/alerts | \
  jq '.data.alerts[] | select(.labels.component=="pricing_sync")'
```
- [ ] No active alerts (all green)
- [ ] Alert rules evaluating

#### Phase 6: Performance Verification (20 min)

14. **Measure Performance**
```bash
# Run 5 consecutive manual syncs
for i in {1..5}; do
  echo "Sync $i:"
  START=$(date +%s)
  curl -s -X POST -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
    $STAGING_URL/admin/pricing/scheduler/trigger | jq '.duration_seconds'
  END=$(date +%s)
  echo "Total: $((END - START))s"
  sleep 3
done
```
- [ ] All syncs complete
- [ ] Consistent duration
- [ ] No degradation

15. **Check Resource Usage**
```bash
curl $STAGING_URL/metrics | grep -E "(memory|cpu)"
```
- [ ] Memory stable
- [ ] CPU not spiking
- [ ] No resource leaks

#### Phase 7: Error Handling (30 min)

16. **Test Invalid Configuration**
```bash
railway variables set \
  PRICING_SYNC_PROVIDERS=openrouter,invalid \
  --environment staging
railway redeploy --environment staging
```
- [ ] Errors logged for invalid provider
- [ ] Valid provider still works
- [ ] No crashes

17. **Restore Valid Configuration**
```bash
railway variables set \
  PRICING_SYNC_PROVIDERS=openrouter,featherless \
  --environment staging
railway redeploy --environment staging
```
- [ ] Configuration restored
- [ ] System recovers
- [ ] Normal operation resumes

#### Phase 8: Scheduled Sync (3+ hours - Optional)

18. **Wait for Scheduled Sync**
- [ ] Note time of last sync
- [ ] Calculate next expected sync (3 hours)
- [ ] Monitor logs for scheduled sync
- [ ] Verify sync occurs on schedule

#### Phase 9: Production Verification (Read-only)

19. **Check Production Ready**
```bash
# Status check (read-only)
curl -H "Authorization: Bearer $PROD_ADMIN_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status
```
- [ ] Production endpoints accessible
- [ ] Configuration correct (6 hour interval)

20. **Verify Monitoring for Production**
- [ ] Prometheus scraping production
- [ ] Grafana can display production data
- [ ] Alerts will apply to production

#### Phase 10: Final Validation (15 min)

21. **Complete System Health Check**
```bash
# Application health
curl $STAGING_URL/health

# Scheduler health
curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  $STAGING_URL/admin/pricing/scheduler/status

# Metrics health
curl $STAGING_URL/metrics | grep pricing_ | wc -l
```
- [ ] All systems healthy
- [ ] No errors or warnings
- [ ] Ready for production

### Test Results Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Scheduler Startup | ☐ Pass ☐ Fail | |
| Manual Trigger | ☐ Pass ☐ Fail | |
| Database Updates | ☐ Pass ☐ Fail | |
| Metrics Collection | ☐ Pass ☐ Fail | |
| Monitoring Dashboards | ☐ Pass ☐ Fail | |
| Error Handling | ☐ Pass ☐ Fail | |
| Performance | ☐ Pass ☐ Fail | |
| Production Ready | ☐ Pass ☐ Fail | |

### Issues Encountered

Document any issues:
1. Issue:
   - Severity:
   - Steps to reproduce:
   - Workaround:
   - Resolution:

### Overall Assessment

- [ ] All critical features working
- [ ] All tests passed
- [ ] No blocking issues
- [ ] Ready for production deployment

**Overall Status**: ☐ PASS ☐ PASS WITH MINOR ISSUES ☐ FAIL

**Tested By**: __________________
**Date**: __________________
**Duration**: ______ hours
**Next Steps**: __________________

### Expected Results
✅ Complete end-to-end workflow functions correctly
✅ All components integrated successfully
✅ System ready for production use

### Pass Criteria
- [ ] All 21 test steps completed
- [ ] No critical failures
- [ ] Production readiness confirmed

### Reference
- Documentation: `docs/MANUAL_TESTING_GUIDE.md` (Part 12)
- All phase completion docs
- Deployment guides

---

## Summary

**Total Issues**: 12
**Testing Categories**:
- Database (1 issue)
- Scheduler (1 issue)
- API Endpoints (1 issue)
- Automated Tests (1 issue)
- Metrics (1 issue)
- Scheduled Operations (1 issue)
- Error Handling (1 issue)
- Data Integrity (1 issue)
- Performance (1 issue)
- Production Readiness (1 issue)
- Monitoring Setup (1 issue)
- E2E Integration (1 issue)

**Total Estimated Time**: 12-18 hours for complete testing
**Critical Path**: Issues #1, #2, #3, #10, #12

---

## Creating Issues

To create these issues in GitHub:

```bash
# For each issue above, create via GitHub CLI
gh issue create \
  --title "[Testing] Issue title here" \
  --body "Copy issue body from above" \
  --label "testing,phase-6,critical" \
  --assignee "username"
```

Or create manually through GitHub web interface:
1. Go to repository Issues page
2. Click "New issue"
3. Copy title and body from above
4. Add appropriate labels
5. Assign to team member

---

**Document Version**: 1.0
**Created**: 2026-01-26
**Last Updated**: 2026-01-26
