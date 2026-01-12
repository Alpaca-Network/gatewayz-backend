# API Key Tracking Quality Improvements

**Date**: 2026-01-12
**Issue**: Some chat completion requests don't have an API key attached (`api_key_id` is NULL)
**Status**: ✅ All recommendations implemented

---

## Problem Analysis

Investigation revealed that NULL `api_key_id` values in the `chat_completion_requests` table occur for several legitimate and technical reasons:

### Root Causes Identified

1. **Anonymous Requests** (Primary - Intentional)
   - The system explicitly supports unauthenticated/anonymous requests
   - These are intentional and should have NULL `api_key_id`

2. **Development Mode Bypass** (Technical)
   - Development environment uses dummy key `local-dev-bypass-key`
   - This dummy key doesn't exist in database, causing NULL lookups

3. **Failed API Key Lookups** (Data Quality Issue)
   - Database connection errors
   - Race conditions (key deleted after validation)
   - Query timeouts

4. **Legacy Data** (Historical)
   - The `api_key_id` column was added on 2026-01-06
   - All records before this date have NULL values
   - *Note: This was addressed by the user with a backfill script*

---

## Implemented Solutions

### ✅ Recommendation #1: Backfill Historical Data
**Status**: Completed by user
**Impact**: All historical records with valid `user_id` now have `api_key_id` populated

---

### ✅ Recommendation #2: Improve Development Mode Handling

**Problem**: Development mode used a dummy key that couldn't be tracked in the database.

**Solution**: Created real development API keys with database records.

#### Files Created:
- **`src/utils/dev_api_key.py`**
  - `get_or_create_dev_api_key()`: Creates/retrieves a real dev API key
  - Auto-creates development user (`dev@localhost`) if needed
  - Uses predictable key format: `dev_local_{user_id}_local_development`
  - Falls back to bypass key if creation fails

#### Files Modified:
- **`src/security/deps.py`** (lines 109-121)
  - Changed from returning `"local-dev-bypass-key"`
  - Now calls `get_or_create_dev_api_key()` for real tracking
  - Maintains fallback to bypass key for safety

**Benefits**:
- Development requests now tracked with real API key IDs
- Better testing parity with production
- Analytics include development usage

---

### ✅ Recommendation #3: Add Retry Logic for API Key Lookups

**Problem**: Transient database errors caused permanent NULL `api_key_id` values.

**Solution**: Implemented robust retry mechanism with exponential backoff.

#### Files Created:
- **`src/utils/api_key_lookup.py`**
  - `get_api_key_id_with_retry()`: Lookup with automatic retries
  - Default: 3 attempts with 0.1s delay
  - Skips known invalid keys (`local-dev-bypass-key`, `anonymous`)
  - Integrates with Prometheus metrics for observability

#### Files Modified:
- **`src/routes/chat.py`** (2 locations)
  - Line 1286-1295: `/v1/chat/completions` endpoint
  - Line 2208-2217: `/v1/responses` endpoint
  - Replaced direct lookup with retry-enabled function
  - Added warning logs when lookup fails

**Benefits**:
- Resilient to transient database errors
- Automatic retry reduces NULL values by ~90% (estimated)
- Clear logging for debugging

---

### ✅ Recommendation #4: Add Monitoring & Alerting

**Problem**: No visibility into tracking quality or failure rates.

**Solution**: Comprehensive monitoring with admin endpoints and Prometheus metrics.

#### Files Created:
- **`src/routes/api_key_monitoring.py`**
  - **GET `/admin/monitoring/api-key-tracking-quality`**
    - Real-time tracking quality metrics
    - Breakdown by authenticated vs anonymous
    - Alert status (ok/warning/critical)
    - Actionable recommendations
  - **GET `/admin/monitoring/api-key-tracking-trend`**
    - Daily trend analysis (up to 30 days)
    - Historical tracking rate visualization

#### Files Modified:
- **`src/services/prometheus_metrics.py`** (lines 222-244)
  - Added 4 new metrics:
    - `api_key_lookup_attempts_total{status}` - Tracks success/failure/retry
    - `api_key_tracking_success_total{request_type}` - Successful tracking by type
    - `api_key_tracking_failures_total{reason}` - Failed tracking by reason
    - `api_key_tracking_rate` - Current success rate (0-1)

- **`src/utils/api_key_lookup.py`**
  - Integrated Prometheus metrics into lookup function
  - Records all lookup attempts and outcomes

- **`src/routes/chat.py`**
  - Line 1276-1282: Track anonymous requests
  - Line 1296-1303: Track successful authenticated lookups

- **`src/main.py`** (line 412)
  - Registered `api_key_monitoring` route

**Benefits**:
- Real-time visibility into tracking quality
- Proactive alerting for degradation
- Grafana dashboard-ready metrics
- Historical trend analysis

---

### ✅ Recommendation #5: Add `is_anonymous` Column

**Problem**: Can't distinguish intentional anonymous requests from data quality issues.

**Solution**: Added explicit boolean flag to database.

#### Files Created:
- **`supabase/migrations/20260112000000_add_is_anonymous_to_chat_completion_requests.sql`**
  - Adds `is_anonymous BOOLEAN NOT NULL DEFAULT FALSE`
  - Creates index for query performance
  - Backfills existing data (sets TRUE where both user_id and api_key_id are NULL)
  - Creates view `api_key_tracking_quality` for easy analysis

#### Files Modified:
- **`src/db/chat_completion_requests.py`** (lines 135-191)
  - Added `is_anonymous: bool = False` parameter
  - Includes in request_data when inserting

- **`src/routes/chat.py`** (3 locations)
  - Line 967: Stream processing - passes `is_anonymous` flag
  - Line 2150: Non-streaming response - passes `is_anonymous` flag
  - Line 3256: `/v1/responses` endpoint - passes `is_anonymous=False`

**Benefits**:
- Clear distinction between intentional and problematic NULL values
- Better analytics queries
- Simplified monitoring logic
- Database view for easy reporting

---

## Monitoring & Alerting

### Admin Endpoints

```bash
# Get current tracking quality
GET /admin/monitoring/api-key-tracking-quality?hours=24
Authorization: Bearer <ADMIN_API_KEY>

Response:
{
  "total_requests": 1000,
  "requests_with_api_key": 950,
  "requests_without_api_key": 50,
  "tracking_rate_percent": 95.0,
  "breakdown": {
    "null_key_with_valid_user": 5,
    "both_null_likely_anonymous": 45,
    "null_key_with_valid_user_percent": 0.5,
    "both_null_percent": 4.5
  },
  "alert_status": "ok",
  "recommendations": [
    "API key tracking quality is good. No action needed."
  ]
}

# Get 7-day trend
GET /admin/monitoring/api-key-tracking-trend?days=7
Authorization: Bearer <ADMIN_API_KEY>
```

### Prometheus Metrics

```promql
# Success rate over time
rate(api_key_tracking_success_total[5m]) /
  (rate(api_key_tracking_success_total[5m]) + rate(api_key_tracking_failures_total[5m]))

# Failed lookups by reason
rate(api_key_tracking_failures_total[5m]) by (reason)

# Retry attempts
rate(api_key_lookup_attempts_total{status="retry"}[5m])
```

### Alert Thresholds

- **OK**: Tracking rate ≥ 90%
- **WARNING**: Tracking rate 70-89%
- **CRITICAL**: Tracking rate < 70%

---

## Database Schema Changes

### New Column: `is_anonymous`

```sql
ALTER TABLE chat_completion_requests
  ADD COLUMN is_anonymous BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_chat_completion_requests_is_anonymous
  ON chat_completion_requests (is_anonymous);
```

### New View: `api_key_tracking_quality`

```sql
CREATE VIEW api_key_tracking_quality AS
SELECT
  DATE_TRUNC('hour', created_at) as hour,
  COUNT(*) as total_requests,
  COUNT(api_key_id) as requests_with_api_key,
  COUNT(*) FILTER (WHERE api_key_id IS NULL) as requests_without_api_key,
  COUNT(*) FILTER (WHERE is_anonymous = TRUE) as anonymous_requests,
  COUNT(*) FILTER (WHERE api_key_id IS NULL AND user_id IS NOT NULL) as potential_lookup_failures,
  ROUND((COUNT(api_key_id)::NUMERIC / NULLIF(COUNT(*), 0)) * 100, 2) as tracking_rate_percent
FROM chat_completion_requests
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY DATE_TRUNC('hour', created_at)
ORDER BY hour DESC;
```

---

## Expected Improvements

### Before Implementation
- **Tracking Rate**: ~70-80% (estimated)
- **Anonymous Requests**: Indistinguishable from failures
- **Failed Lookups**: Permanent NULL values
- **Monitoring**: Manual database queries only

### After Implementation
- **Tracking Rate**: ~95-98% (expected)
- **Anonymous Requests**: Explicitly flagged with `is_anonymous=TRUE`
- **Failed Lookups**: Automatic retry reduces by ~90%
- **Monitoring**: Real-time dashboards + alerts

---

## Usage Examples

### Query Anonymous vs Authenticated Requests

```sql
-- Get breakdown of request types
SELECT
  is_anonymous,
  COUNT(*) as total,
  COUNT(api_key_id) as with_key,
  COUNT(*) - COUNT(api_key_id) as without_key
FROM chat_completion_requests
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY is_anonymous;
```

### Identify Potential Lookup Failures

```sql
-- Find authenticated requests missing API key ID
SELECT *
FROM chat_completion_requests
WHERE is_anonymous = FALSE
  AND api_key_id IS NULL
  AND user_id IS NOT NULL
  AND created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;
```

### Check Hourly Tracking Quality

```sql
-- Use the new view
SELECT * FROM api_key_tracking_quality
LIMIT 24;
```

---

## Testing Recommendations

### 1. Verify Development Mode
```bash
# Start development server
APP_ENV=development python src/main.py

# Make request without auth
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "test"}]}'

# Check database - should have api_key_id
SELECT api_key_id, is_anonymous FROM chat_completion_requests ORDER BY created_at DESC LIMIT 1;
```

### 2. Test Retry Logic
```bash
# Temporarily break database connection and restore
# Should see retry attempts in logs
# Should eventually succeed or fail gracefully
```

### 3. Monitor Metrics
```bash
# Check Prometheus endpoint
curl http://localhost:8000/metrics | grep api_key

# Expected metrics:
# api_key_lookup_attempts_total{status="success"}
# api_key_tracking_success_total{request_type="authenticated"}
# api_key_tracking_failures_total{reason="anonymous"}
```

### 4. Test Admin Endpoints
```bash
# Get tracking quality
curl http://localhost:8000/admin/monitoring/api-key-tracking-quality \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# Get trend data
curl http://localhost:8000/admin/monitoring/api-key-tracking-trend?days=7 \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

---

## Migration Instructions

### 1. Apply Database Migration
```bash
# Run migration
supabase migration up

# Or manually via psql
psql $DATABASE_URL -f supabase/migrations/20260112000000_add_is_anonymous_to_chat_completion_requests.sql
```

### 2. Deploy Code Changes
```bash
# Ensure all new files are included
git add src/utils/dev_api_key.py
git add src/utils/api_key_lookup.py
git add src/routes/api_key_monitoring.py
git add supabase/migrations/20260112000000_add_is_anonymous_to_chat_completion_requests.sql

# Commit and deploy
git commit -m "feat: improve API key tracking quality with retry logic, monitoring, and is_anonymous flag"
git push origin main
```

### 3. Verify Deployment
```bash
# Check health
curl https://api.gatewayz.ai/health

# Verify new endpoints
curl https://api.gatewayz.ai/admin/monitoring/api-key-tracking-quality \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# Check metrics
curl https://api.gatewayz.ai/metrics | grep api_key
```

---

## Maintenance

### Regular Checks

1. **Daily**: Review tracking rate in admin dashboard
2. **Weekly**: Check trend for degradation patterns
3. **Monthly**: Analyze `potential_lookup_failures` for patterns

### Alert Configuration (Grafana)

```yaml
alerts:
  - alert: LowAPIKeyTrackingRate
    expr: api_key_tracking_rate < 0.90
    for: 5m
    annotations:
      summary: "API key tracking rate below 90%"
      description: "Current rate: {{ $value }}%"

  - alert: HighLookupFailures
    expr: rate(api_key_tracking_failures_total{reason="lookup_failed"}[5m]) > 10
    for: 5m
    annotations:
      summary: "High rate of API key lookup failures"
```

---

## Summary

All 5 recommendations have been successfully implemented:

1. ✅ **Backfill historical data** (completed by user)
2. ✅ **Improve development mode** - Real dev API keys
3. ✅ **Add retry logic** - 3 attempts with backoff
4. ✅ **Add monitoring** - Admin endpoints + Prometheus metrics
5. ✅ **Add is_anonymous flag** - Clear distinction in database

**Expected Impact**:
- Tracking rate improvement: 70-80% → 95-98%
- Better visibility into data quality
- Proactive alerting for issues
- Clear separation of anonymous vs failed lookups

**Next Steps**:
1. Apply database migration
2. Deploy code changes
3. Configure Grafana alerts
4. Monitor tracking quality dashboard
