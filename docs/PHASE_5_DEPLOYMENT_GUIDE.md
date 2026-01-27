# Phase 5: Deployment & Rollout Guide

**Date**: January 26, 2026
**Status**: 🚀 DEPLOYMENT READY
**Previous Phases**:
- Phase 2.5 (Automated Sync Scheduler - commit 6075d285)
- Phase 3 (Admin Endpoints - commit 002304b0)
- Phase 4 (Comprehensive Testing - commit 9b971e78)

---

## Overview

This guide covers the deployment and rollout of the automated pricing sync scheduler (Phase 2.5) and admin endpoints (Phase 3) to staging and production environments.

**Components Being Deployed**:
- Automated pricing sync scheduler (background task)
- Admin status endpoint (`GET /admin/pricing/scheduler/status`)
- Admin trigger endpoint (`POST /admin/pricing/scheduler/trigger`)
- Prometheus metrics for monitoring
- Configuration via environment variables

---

## Pre-Deployment Checklist

### ✅ Code Complete

- [x] Phase 2.5: Automated sync scheduler implemented
- [x] Phase 3: Admin endpoints implemented
- [x] Phase 4: Comprehensive test suite added
- [x] All commits merged to staging branch
- [x] All tests passing

### ✅ Documentation Complete

- [x] Phase 2.5 completion docs
- [x] Phase 3 completion docs
- [x] Phase 4 completion docs
- [x] API documentation updated (OpenAPI/Swagger)
- [x] Deployment guide (this document)

### ✅ Testing Complete

- [x] Unit tests for scheduler (18 tests)
- [x] Integration tests for admin endpoints (12 tests)
- [x] All tests passing locally
- [x] No regressions in existing functionality

---

## Environment Variables

### Required Environment Variables

Add these to Railway/Vercel environment configuration:

```bash
# Pricing Sync Scheduler Configuration
PRICING_SYNC_ENABLED=true                    # Enable/disable scheduler
PRICING_SYNC_INTERVAL_HOURS=6                # Sync frequency (hours)
PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud  # Providers to sync
```

### Environment-Specific Values

#### Staging Environment

```bash
PRICING_SYNC_ENABLED=true
PRICING_SYNC_INTERVAL_HOURS=3                # More frequent for testing
PRICING_SYNC_PROVIDERS=openrouter,featherless  # Fewer providers for faster testing
```

#### Production Environment

```bash
PRICING_SYNC_ENABLED=true
PRICING_SYNC_INTERVAL_HOURS=6                # Standard interval
PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud  # All supported providers
```

### Existing Required Variables

Ensure these are already configured:

```bash
# Database
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-key

# Monitoring (Optional but recommended)
PROMETHEUS_ENABLED=true
TEMPO_ENABLED=true
LOKI_ENABLED=true
SENTRY_DSN=your-sentry-dsn

# Admin Access
ADMIN_API_KEY=your-admin-api-key  # For testing admin endpoints
```

---

## Deployment Steps

### Step 1: Staging Deployment

#### 1.1 Merge to Staging Branch

```bash
# Ensure you're on staging branch
git checkout staging

# Verify all Phase 2.5/3/4 commits are included
git log --oneline | head -10

# Should see:
# - b2e3e6a9 docs: add Phase 4 completion documentation
# - 9b971e78 test: Phase 4 comprehensive test suite
# - 8695c527 docs: add Phase 3 completion documentation
# - 002304b0 feat: Phase 3 admin endpoints
# - 2462287a docs: add Phase 2.5 completion documentation
# - 6075d285 feat: Phase 2.5 automated pricing sync scheduler
```

#### 1.2 Configure Environment Variables (Railway)

```bash
# Set staging environment variables
railway environment staging

# Add pricing sync configuration
railway variables set PRICING_SYNC_ENABLED=true
railway variables set PRICING_SYNC_INTERVAL_HOURS=3
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless

# Verify variables
railway variables
```

#### 1.3 Deploy to Staging

```bash
# Push to trigger deployment
git push origin staging

# Or trigger manual deployment
railway up --environment staging

# Monitor deployment logs
railway logs --environment staging
```

#### 1.4 Verify Staging Deployment

**Wait for deployment to complete** (~2-5 minutes):

```bash
# Check deployment status
railway status --environment staging

# Verify health endpoint
curl https://gatewayz-staging.up.railway.app/health

# Should return 200 OK with health data
```

**Verify scheduler started**:

```bash
# Check logs for scheduler startup message
railway logs --environment staging | grep "pricing sync scheduler"

# Should see:
# ✅ Pricing sync scheduler started
# 📅 Pricing sync scheduler started (interval: 3h)
```

---

### Step 2: Staging Verification

#### 2.1 Test Admin Status Endpoint

```bash
# Get scheduler status
curl -X GET "https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  | jq '.'

# Expected response:
{
  "success": true,
  "scheduler": {
    "enabled": true,
    "interval_hours": 3,
    "running": true,
    "providers": ["openrouter", "featherless"]
  },
  "timestamp": "2026-01-26T..."
}
```

#### 2.2 Test Manual Trigger (Optional - Creates Real Sync)

**⚠️ WARNING**: This triggers a real pricing sync. Only run if you want to update pricing immediately.

```bash
# Trigger manual pricing sync
curl -X POST "https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  | jq '.'

# Expected response (takes 10-60 seconds):
{
  "success": true,
  "status": "success",
  "duration_seconds": 12.5,
  "total_models_updated": 150,
  "total_errors": 0,
  "triggered_by": "admin@gatewayz.ai",
  "triggered_at": "2026-01-26T..."
}
```

#### 2.3 Verify Prometheus Metrics

```bash
# Get Prometheus metrics
curl https://gatewayz-staging.up.railway.app/metrics | grep pricing

# Should see metrics like:
# pricing_scheduled_sync_runs_total{status="success"} 1
# pricing_scheduled_sync_duration_seconds_sum 12.5
# pricing_last_sync_timestamp{provider="openrouter"} 1737900000.0
```

#### 2.4 Monitor First Scheduled Sync

**Wait 3 hours** (or configured interval) for first scheduled sync:

```bash
# Monitor logs for scheduled sync
railway logs --environment staging --follow | grep "pricing sync"

# Should see after 3 hours:
# 🔄 Starting scheduled pricing sync...
# ✅ Scheduled pricing sync completed successfully (duration: 12.5s, updated: 150)
```

#### 2.5 Verify Database Updates

```bash
# Query database to verify pricing was updated
# (Requires database access)

psql $SUPABASE_URL -c "
  SELECT
    model_id,
    input_price_per_1m_tokens,
    output_price_per_1m_tokens,
    updated_at
  FROM model_pricing
  WHERE updated_at > NOW() - INTERVAL '1 hour'
  ORDER BY updated_at DESC
  LIMIT 10;
"
```

---

### Step 3: Staging Monitoring (24-48 hours)

Monitor staging for 24-48 hours before production deployment:

#### 3.1 Check Scheduler Runs

```bash
# Check logs for scheduled sync runs
railway logs --environment staging | grep "Scheduled pricing sync"

# Verify syncs run every 3 hours
# Example timeline:
# 2026-01-26T12:00:00Z - First sync after 30s
# 2026-01-26T15:00:00Z - Second sync (3h later)
# 2026-01-26T18:00:00Z - Third sync (3h later)
```

#### 3.2 Check for Errors

```bash
# Check for any scheduler errors
railway logs --environment staging | grep "ERROR.*pricing"

# Should be empty or minimal errors
```

#### 3.3 Verify Metrics

Check Prometheus/Grafana for:
- `pricing_scheduled_sync_runs_total` - Should increase every 3 hours
- `pricing_scheduled_sync_duration_seconds` - Should be consistent (10-60s)
- `pricing_last_sync_timestamp` - Should update every 3 hours

#### 3.4 Test Admin Endpoints

Periodically test admin endpoints:
- Status endpoint should always work
- Manual trigger should complete successfully
- No authentication bypass issues

---

### Step 4: Production Deployment

**Prerequisites**:
- ✅ Staging running stable for 24-48 hours
- ✅ No critical errors in logs
- ✅ Scheduled syncs completing successfully
- ✅ Admin endpoints working correctly
- ✅ Metrics being collected properly

#### 4.1 Merge to Main Branch

```bash
# Switch to main branch
git checkout main

# Merge staging into main
git merge staging

# Push to main
git push origin main
```

#### 4.2 Configure Production Environment Variables

```bash
# Switch to production environment
railway environment production

# Add pricing sync configuration
railway variables set PRICING_SYNC_ENABLED=true
railway variables set PRICING_SYNC_INTERVAL_HOURS=6
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud

# Verify variables
railway variables
```

#### 4.3 Deploy to Production

```bash
# Deploy to production
railway up --environment production

# Monitor deployment
railway logs --environment production
```

#### 4.4 Verify Production Deployment

**Immediately after deployment**:

```bash
# Check health
curl https://api.gatewayz.ai/health

# Check scheduler status (requires admin key)
curl -X GET "https://api.gatewayz.ai/admin/pricing/scheduler/status" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  | jq '.'

# Verify logs
railway logs --environment production | grep "pricing sync scheduler"
```

---

## Post-Deployment Verification

### Immediate Checks (First 30 minutes)

- [ ] Health endpoint returns 200 OK
- [ ] Application started successfully
- [ ] No critical errors in logs
- [ ] Scheduler started (log message present)
- [ ] Admin status endpoint accessible
- [ ] Prometheus metrics endpoint working

### First Scheduled Sync (After 30 seconds)

- [ ] First sync runs automatically after 30s delay
- [ ] Sync completes successfully
- [ ] Models updated in database
- [ ] Metrics updated in Prometheus
- [ ] No errors in logs

### First 6 Hours

- [ ] Scheduled sync runs every 6 hours
- [ ] Each sync completes successfully
- [ ] Duration is consistent (10-60s)
- [ ] No memory leaks
- [ ] CPU usage normal

### First 24 Hours

- [ ] 4 scheduled syncs completed (every 6 hours)
- [ ] All syncs successful
- [ ] No degradation in performance
- [ ] Metrics accurate
- [ ] No user-reported issues

---

## Rollback Plan

If issues arise, follow this rollback procedure:

### Option 1: Disable Scheduler Only

Keep new code, just disable the scheduler:

```bash
# Disable scheduler via environment variable
railway variables set PRICING_SYNC_ENABLED=false

# Redeploy (or restart)
railway redeploy --environment production

# Verify scheduler is disabled
railway logs --environment production | grep "Pricing sync scheduler disabled"
```

### Option 2: Rollback to Previous Deployment

Revert to version before Phase 2.5:

```bash
# Find previous deployment
railway deployments list --environment production

# Rollback to specific deployment
railway rollback <deployment-id> --environment production

# Verify rollback
curl https://api.gatewayz.ai/health
```

### Option 3: Revert Code (Emergency)

If severe issues, revert commits:

```bash
# Revert Phase 2.5/3 commits (keep Phase 4 tests)
git revert 002304b0  # Phase 3
git revert 6075d285  # Phase 2.5

# Push revert
git push origin main

# Deploy reverted version
railway up --environment production
```

---

## Monitoring & Alerts

### Grafana Dashboards

Create dashboards to monitor:

**Scheduler Health Dashboard**:
- Sync success rate
- Sync duration over time
- Last sync timestamp per provider
- Models updated per sync
- Error count

**Example Prometheus Queries**:

```promql
# Sync success rate (last hour)
rate(pricing_scheduled_sync_runs_total{status="success"}[1h])
/ rate(pricing_scheduled_sync_runs_total[1h])

# Average sync duration
rate(pricing_scheduled_sync_duration_seconds_sum[1h])
/ rate(pricing_scheduled_sync_duration_seconds_count[1h])

# Time since last sync
time() - pricing_last_sync_timestamp{provider="openrouter"}

# Models synced rate
sum by (provider) (rate(pricing_models_synced_total[1h]))
```

### Alerts to Configure

**Critical Alerts** (PagerDuty/Slack):

1. **Scheduler Stopped**
   ```promql
   # No sync in last 8 hours (6h interval + 2h buffer)
   time() - pricing_last_sync_timestamp > 28800
   ```

2. **High Error Rate**
   ```promql
   # Error rate > 50% over 1 hour
   rate(pricing_scheduled_sync_runs_total{status="failed"}[1h])
   / rate(pricing_scheduled_sync_runs_total[1h]) > 0.5
   ```

**Warning Alerts** (Slack only):

3. **Slow Sync Duration**
   ```promql
   # Average duration > 60 seconds
   rate(pricing_scheduled_sync_duration_seconds_sum[1h])
   / rate(pricing_scheduled_sync_duration_seconds_count[1h]) > 60
   ```

4. **No Syncs Running**
   ```promql
   # No sync runs in last 8 hours
   increase(pricing_scheduled_sync_runs_total[8h]) == 0
   ```

---

## Troubleshooting

### Issue: Scheduler Not Starting

**Symptoms**: No "Pricing sync scheduler started" log message

**Diagnosis**:
```bash
# Check environment variable
railway variables | grep PRICING_SYNC_ENABLED

# Check logs for errors
railway logs | grep -i "error.*scheduler"
```

**Solutions**:
1. Verify `PRICING_SYNC_ENABLED=true`
2. Check for import errors in logs
3. Verify all Phase 2.5 code deployed correctly
4. Restart application: `railway redeploy`

### Issue: Sync Failing

**Symptoms**: `pricing_scheduled_sync_runs_total{status="failed"}` increasing

**Diagnosis**:
```bash
# Check logs for error details
railway logs | grep "Scheduled pricing sync failed"

# Check admin status endpoint
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status
```

**Solutions**:
1. Check provider API keys are valid
2. Verify database connectivity
3. Check provider API rate limits
4. Review error messages in logs
5. Check Sentry for detailed error traces

### Issue: Slow Sync Performance

**Symptoms**: Syncs taking > 60 seconds

**Diagnosis**:
```bash
# Check metrics
curl https://api.gatewayz.ai/metrics | grep pricing_scheduled_sync_duration

# Check database performance
# (requires database access)
```

**Solutions**:
1. Reduce number of providers
2. Increase sync interval
3. Optimize database queries
4. Check network latency to providers
5. Scale up application resources

### Issue: Admin Endpoints Not Working

**Symptoms**: 404 or 500 errors on admin endpoints

**Diagnosis**:
```bash
# Test status endpoint
curl -v https://api.gatewayz.ai/admin/pricing/scheduler/status

# Check if endpoints registered
railway logs | grep "admin/pricing/scheduler"
```

**Solutions**:
1. Verify Phase 3 code deployed
2. Check admin authentication configuration
3. Verify API key is correct
4. Check application startup logs
5. Restart application

---

## Success Criteria

### Staging Success

- ✅ Scheduler runs automatically every 3 hours
- ✅ All syncs complete successfully
- ✅ Admin endpoints accessible
- ✅ No critical errors for 48 hours
- ✅ Metrics collecting correctly

### Production Success

- ✅ Scheduler runs automatically every 6 hours
- ✅ All syncs complete successfully
- ✅ Pricing data stays current
- ✅ No user-facing issues
- ✅ Performance metrics normal
- ✅ Monitoring and alerts working

---

## Timeline

### Recommended Deployment Schedule

**Day 1 (Monday)**:
- Morning: Deploy to staging
- Afternoon: Verify initial sync
- Evening: Monitor metrics

**Days 2-3 (Tuesday-Wednesday)**:
- Monitor staging continuously
- Test admin endpoints periodically
- Verify multiple sync cycles

**Day 4 (Thursday)**:
- Morning: Review staging metrics
- Afternoon: Deploy to production (if staging stable)
- Evening: Monitor production closely

**Days 5-7 (Friday-Sunday)**:
- Continuous monitoring
- Respond to any issues
- Document any learnings

---

## Communication Plan

### Before Deployment

**Notify Team**:
- Engineering team: New features being deployed
- DevOps team: Monitor deployment and alerts
- Admin users: New admin endpoints available

**Email Template**:
```
Subject: Deployment: Automated Pricing Sync Scheduler

Team,

We're deploying the automated pricing sync scheduler (Phase 2.5/3) to production.

What's New:
- Automated pricing updates every 6 hours
- Admin endpoints for monitoring and control
- Prometheus metrics for observability

Timeline:
- Staging: [Date]
- Production: [Date] (after 48h staging verification)

Impact:
- Zero downtime deployment
- No breaking changes
- Pricing stays automatically current

Admin Endpoints (requires admin API key):
- GET /admin/pricing/scheduler/status - View scheduler state
- POST /admin/pricing/scheduler/trigger - Manual sync trigger

Questions? Contact: [Your Name]
```

### During Deployment

**Status Updates** (Slack):
- Deployment started
- Deployment complete
- Verification in progress
- All checks passing / Issues found

### After Deployment

**Success Announcement**:
```
🎉 Automated Pricing Sync Deployed Successfully!

✅ Scheduler running every 6 hours
✅ Admin endpoints operational
✅ Metrics collecting
✅ No issues detected

Dashboard: [Grafana Link]
Documentation: [Wiki Link]
```

---

## Appendix

### A. Environment Variable Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PRICING_SYNC_ENABLED` | boolean | `true` | Enable/disable scheduler |
| `PRICING_SYNC_INTERVAL_HOURS` | integer | `6` | Sync frequency (hours) |
| `PRICING_SYNC_PROVIDERS` | comma-separated | `openrouter,featherless,nearai,alibaba-cloud` | Providers to sync |

### B. API Endpoint Reference

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/admin/pricing/scheduler/status` | GET | Admin | Get scheduler status |
| `/admin/pricing/scheduler/trigger` | POST | Admin | Trigger manual sync |
| `/metrics` | GET | None | Prometheus metrics |
| `/health` | GET | None | Health check |

### C. Metric Reference

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `pricing_scheduled_sync_runs_total` | Counter | `status` | Total sync runs |
| `pricing_scheduled_sync_duration_seconds` | Histogram | - | Sync duration |
| `pricing_last_sync_timestamp` | Gauge | `provider` | Last sync time |
| `pricing_models_synced_total` | Counter | `provider, status` | Models synced |

### D. Log Messages Reference

**Scheduler Lifecycle**:
- `🔄 Starting pricing sync scheduler (interval: 6h)...`
- `✅ Pricing sync scheduler started`
- `📅 Pricing sync scheduler started (interval: Xh = Ys)`
- `Pricing sync scheduler stopped`

**Scheduled Syncs**:
- `🔄 Starting scheduled pricing sync...`
- `✅ Scheduled pricing sync completed successfully`
- `❌ Scheduled pricing sync failed`

**Manual Syncs**:
- `Manual pricing sync triggered by admin: user@email.com`
- `Manual pricing sync completed: X models updated in Y.Zs`

---

## Sign-Off

**Deployment Readiness**: ✅ **READY**

**Checklist Complete**:
- ✅ All code merged and tested
- ✅ Environment variables documented
- ✅ Deployment steps documented
- ✅ Verification procedures defined
- ✅ Rollback plan established
- ✅ Monitoring and alerts planned
- ✅ Communication plan ready

**Ready for**: Staging deployment

**Author**: Claude Code
**Date**: January 26, 2026
**Phase**: 5 (Deployment & Rollout)

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
