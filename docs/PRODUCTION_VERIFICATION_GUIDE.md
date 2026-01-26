# Production Readiness Verification Guide

**Issue**: #960
**Purpose**: Verify that production environment is correctly configured and ready for pricing scheduler deployment
**Mode**: Read-only verification (no changes to production)

## Overview

This guide provides step-by-step instructions for verifying production readiness before deploying the pricing scheduler. All checks are read-only and will not trigger syncs or make changes to production.

## Prerequisites

Before starting verification:

- [ ] Access to production environment (read-only)
- [ ] Production admin API key
- [ ] Supabase production database access
- [ ] Railway CLI access to production environment
- [ ] Staging verification complete (24-48h runtime)

## Quick Start

### Automated Verification

Run the automated verification script:

```bash
# Option 1: With admin key as environment variable
export PROD_ADMIN_KEY="your_production_admin_key"
python3 scripts/verify_production_readiness.py

# Option 2: With admin key as argument
python3 scripts/verify_production_readiness.py --admin-key "your_production_admin_key"

# Option 3: Without admin key (limited checks)
python3 scripts/verify_production_readiness.py
```

The script will generate a JSON report with all verification results.

### Getting Production Admin Key

If you don't have the production admin key:

```bash
# Set production Supabase credentials
export SUPABASE_URL="https://your-production-instance.supabase.co"
export SUPABASE_KEY="your_production_service_role_key"

# Run the script to get admin key
python3 scripts/get_production_admin_key.py

# This will save the key to .admin_key_production
export PROD_ADMIN_KEY=$(cat .admin_key_production)
```

## Manual Verification Steps

### Step 1: Verify Environment Variables

Check production environment configuration:

```bash
# Using Railway CLI
railway variables --environment production | grep PRICING_SYNC
```

**Expected Configuration**:
```
PRICING_SYNC_ENABLED=true (or false, ready to enable)
PRICING_SYNC_INTERVAL_HOURS=6
PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud
```

**Verification Checklist**:
- [ ] `PRICING_SYNC_ENABLED` exists
- [ ] `PRICING_SYNC_INTERVAL_HOURS=6` (NOT 3 - production uses 6 hours)
- [ ] `PRICING_SYNC_PROVIDERS` includes all 4 providers
- [ ] Variables are set but scheduler not yet enabled (or ready to enable)

### Step 2: Verify Database Migration

Check production database for required tables:

```sql
-- Execute in production Supabase SQL editor
SELECT tablename, schemaname
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('model_pricing_history', 'pricing_sync_log')
ORDER BY tablename;
```

**Expected Results**:
```
tablename                | schemaname
-------------------------+------------
model_pricing_history    | public
pricing_sync_log         | public
```

**Verification Checklist**:
- [ ] `model_pricing_history` table exists
- [ ] `pricing_sync_log` table exists
- [ ] Both tables in `public` schema
- [ ] Tables have correct schema (check migration file)

**Additional Schema Checks**:

```sql
-- Verify model_pricing_history schema
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'model_pricing_history'
ORDER BY ordinal_position;

-- Verify pricing_sync_log schema
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'pricing_sync_log'
ORDER BY ordinal_position;
```

### Step 3: Check Production Health

Verify production application is healthy:

```bash
curl https://api.gatewayz.ai/health
```

**Expected Response**:
```json
{
  "status": "healthy",
  "timestamp": "2026-01-26T20:00:00.000000+00:00",
  "database": "connected"
}
```

**Verification Checklist**:
- [ ] Returns 200 OK
- [ ] Status is "healthy"
- [ ] Database is "connected"
- [ ] No errors in response

### Step 4: Verify Admin Endpoints

Check admin status endpoint:

```bash
# Set admin key
export PROD_ADMIN_KEY="your_production_admin_key"

# Check scheduler status
curl -X GET \
  -H "Authorization: Bearer $PROD_ADMIN_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status
```

**Expected Responses**:

Option 1: If endpoint deployed:
```json
{
  "enabled": false,
  "interval_hours": 6,
  "providers": ["openrouter", "featherless", "nearai", "alibaba-cloud"],
  "last_sync": null
}
```

Option 2: If endpoint not deployed yet:
```
404 Not Found
```

**Verification Checklist**:
- [ ] Returns 200 OK (or 404 if not deployed)
- [ ] Authentication works (no 401/403)
- [ ] Response structure correct (if 200)

### Step 5: Check Metrics Endpoint

Check if metrics endpoint works:

```bash
curl https://api.gatewayz.ai/metrics | grep pricing_ | head -5
```

**Expected Output**:
```
# HELP pricing_sync_duration_seconds Time spent in pricing sync
# TYPE pricing_sync_duration_seconds histogram
# HELP pricing_sync_total Total number of pricing syncs
# TYPE pricing_sync_total counter
```

**Verification Checklist**:
- [ ] Metrics endpoint accessible (200 OK)
- [ ] Pricing metrics present (may be 0 if not running)
- [ ] No errors in response
- [ ] Prometheus format valid

### Step 6: Verify Monitoring Infrastructure

Check monitoring systems are configured:

**Prometheus**:
- [ ] Prometheus configured to scrape production (`https://api.gatewayz.ai/metrics`)
- [ ] Scrape interval configured (recommended: 15s)
- [ ] Scrape target healthy in Prometheus UI

**Grafana**:
- [ ] Grafana dashboards can access production data source
- [ ] "Pricing Sync Dashboard" imported
- [ ] Panels showing data (or ready for data)

**Alerting**:
- [ ] Alert rules configured for production
- [ ] Notification channels set up (Slack/PagerDuty)
- [ ] Test alert sent and received
- [ ] On-call rotation configured

**Access**:
```bash
# Check Prometheus targets
curl http://your-prometheus:9090/api/v1/targets

# Check Grafana datasource
curl http://your-grafana:3000/api/datasources
```

### Step 7: Review Deployment Checklist

Verify all Phase 5 pre-deployment items:

**Code Deployment**:
- [ ] Phase 2.5 code merged to main branch
- [ ] Phase 3 code merged to main branch
- [ ] All tests passing in CI/CD
- [ ] Dependencies updated in requirements.txt

**Staging Verification**:
- [ ] Staging running successfully for 24-48h
- [ ] No errors in staging logs
- [ ] Pricing syncs completing successfully
- [ ] Performance metrics acceptable

**Database**:
- [ ] Migration applied to production
- [ ] Tables created successfully
- [ ] RLS policies active
- [ ] Indexes created
- [ ] No data corruption

**Documentation**:
- [ ] Deployment guide complete
- [ ] Runbooks created
- [ ] API documentation updated
- [ ] Team trained on new features

**Communication**:
- [ ] Team notified of deployment
- [ ] Deployment window scheduled
- [ ] On-call roster updated
- [ ] Rollback plan prepared and communicated

### Step 8: Validate Configuration Differences

Confirm configuration differences between staging and production:

| Setting | Staging | Production | Correct? |
|---------|---------|------------|----------|
| `PRICING_SYNC_INTERVAL_HOURS` | 3 hours | 6 hours | [ ] |
| `PRICING_SYNC_PROVIDERS` | 2 providers | 4 providers | [ ] |
| Monitoring | Optional | Required | [ ] |
| Alerting | Optional | Required | [ ] |
| Logging Level | DEBUG | INFO | [ ] |

**Verification**:
- [ ] Production uses 6-hour interval (NOT 3 hours)
- [ ] Production has all 4 providers enabled
- [ ] Production has monitoring enabled
- [ ] Production has alerting configured
- [ ] Production logging level appropriate

## Verification Report

After running the automated script, review the generated report:

```bash
# View the latest report
cat production_verification_report_*.json | jq
```

**Report Structure**:
```json
{
  "verification_timestamp": "2026-01-26T20:00:00Z",
  "production_url": "https://api.gatewayz.ai",
  "summary": {
    "total_automated_checks": 5,
    "passed": 5,
    "failed": 0,
    "manual_checks": 2
  },
  "checks": [...],
  "ready_for_deployment": true
}
```

## Pass Criteria

All checks must pass before deployment:

### Automated Checks (Must Pass)
- [x] Production health endpoint returns 200 OK
- [x] Database connection healthy
- [x] Metrics endpoint accessible
- [x] Admin authentication working
- [x] No critical errors

### Manual Checks (Must Verify)
- [ ] Database migration applied successfully
- [ ] Environment variables configured correctly
- [ ] Monitoring infrastructure ready
- [ ] Deployment checklist complete
- [ ] All approvals obtained

### Deployment Blockers

**Do NOT deploy if**:
- Any automated check fails
- Database migration not applied
- Environment variables incorrect
- Monitoring not configured
- Staging not stable for 24-48h
- Rollback plan not prepared

## Deployment Authorization

Once all checks pass, obtain approvals:

**Required Approvals**:
- [ ] Engineering Lead: ___________________
- [ ] DevOps Lead: ___________________
- [ ] Product Owner: ___________________

**Deployment Window**:
- Scheduled Date: ___________________
- Scheduled Time: ___________________
- Duration: ___________________

**Rollback Plan**:
- [ ] Rollback procedure documented
- [ ] Rollback tested in staging
- [ ] Team trained on rollback
- [ ] Rollback decision criteria defined

## Troubleshooting

### Script Fails with "No admin key"

**Solution**:
```bash
# Get production admin key first
python3 scripts/get_production_admin_key.py

# Then run verification
export PROD_ADMIN_KEY=$(cat .admin_key_production)
python3 scripts/verify_production_readiness.py
```

### Database Tables Not Found

**Solution**:
1. Check if migration was applied:
   ```sql
   SELECT * FROM schema_migrations ORDER BY version DESC LIMIT 5;
   ```
2. If migration missing, apply it:
   ```bash
   supabase db push --environment production
   ```

### Metrics Endpoint Not Accessible

**Possible Causes**:
- Prometheus middleware not enabled
- Route not registered
- CORS blocking requests

**Solution**:
Check application logs and verify `/metrics` route is registered.

### Admin Endpoint Returns 403

**Possible Causes**:
- Invalid admin key
- Key not associated with admin user
- Authentication middleware issue

**Solution**:
1. Verify admin key is correct
2. Check user role in database
3. Review authentication logs

## Next Steps

After verification passes:

1. **Schedule Deployment**
   - Choose low-traffic window
   - Notify team
   - Prepare monitoring

2. **Execute Deployment**
   - Follow deployment guide
   - Enable scheduler gradually
   - Monitor closely

3. **Post-Deployment Verification**
   - Verify first sync completes
   - Check logs for errors
   - Monitor metrics
   - Validate data quality

4. **Ongoing Monitoring**
   - Watch Grafana dashboards
   - Review alert notifications
   - Check sync success rate
   - Monitor performance

## Reference Documents

- **Deployment Guide**: `docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- **Deployment Checklist**: `docs/DEPLOYMENT_CHECKLIST.md`
- **Manual Testing Guide**: `docs/MANUAL_TESTING_GUIDE.md` (Part 10)
- **Rollback Procedure**: `docs/ROLLBACK_PROCEDURE.md`
- **Runbook**: `docs/PRICING_SCHEDULER_RUNBOOK.md`

## Support

If issues arise during verification:

1. Check troubleshooting section above
2. Review application logs
3. Consult deployment guide
4. Contact team lead
5. Escalate to DevOps if needed

---

**Last Updated**: 2026-01-26
**Issue**: #960
**Status**: Ready for execution
