# Runbook: Pricing Sync Scheduler Stopped

**Alert**: `PricingSyncSchedulerStopped`
**Severity**: Critical
**Team**: Platform
**Component**: pricing_sync

---

## Symptoms

- Alert triggered: "Pricing sync scheduler has stopped"
- No sync activity for 8+ hours (expected interval: 6 hours + 2 hour grace period)
- `pricing_last_sync_timestamp` metric shows stale timestamp
- Pricing data becoming out of date

---

## Impact

**Business Impact**:
- Pricing data becomes stale, affecting billing accuracy
- Customers may be charged incorrect amounts
- Model pricing may not reflect provider updates
- Revenue impact if pricing is significantly different

**Technical Impact**:
- Automated pricing updates not functioning
- Manual intervention required for pricing updates
- Increased operational overhead

**Severity Justification**:
- Critical because it directly affects billing accuracy
- Requires immediate attention to prevent revenue loss

---

## Diagnosis

### Step 1: Check Scheduler Status

```bash
# Check scheduler status via admin endpoint
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status | jq '.'

# Expected output if running:
# {
#   "success": true,
#   "scheduler": {
#     "enabled": true,
#     "running": true,
#     "interval_hours": 6,
#     "providers": ["openrouter", "featherless", "nearai", "alibaba-cloud"]
#   }
# }
```

**Check for**:
- `enabled: false` - Scheduler is disabled
- `running: false` - Scheduler stopped unexpectedly
- Missing response - Application may be down

### Step 2: Check Application Logs

```bash
# Check for scheduler startup message
railway logs --environment production | grep "Pricing sync scheduler started"

# Check for recent sync attempts
railway logs --environment production | grep "pricing sync" | tail -20

# Check for errors
railway logs --environment production | grep -i "error.*pricing" | tail -20
```

**Look for**:
- "Pricing sync scheduler started" - Scheduler initialized
- "Scheduled pricing sync completed" - Successful syncs
- "Scheduled pricing sync failed" - Failed sync attempts
- Exception traces or error messages

### Step 3: Check Environment Configuration

```bash
# Verify environment variables
railway variables --environment production | grep PRICING_SYNC

# Expected:
# PRICING_SYNC_ENABLED=true
# PRICING_SYNC_INTERVAL_HOURS=6
# PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud
```

**Check for**:
- `PRICING_SYNC_ENABLED=false` - Scheduler intentionally disabled
- Missing variables - Configuration error
- Incorrect values - Misconfiguration

### Step 4: Check Application Health

```bash
# Check application health endpoint
curl https://api.gatewayz.ai/health

# Check if application is running
railway ps --environment production
```

### Step 5: Check Prometheus Metrics

```bash
# Check last sync timestamp
curl https://api.gatewayz.ai/metrics | grep pricing_last_sync_timestamp

# Check sync run counts
curl https://api.gatewayz.ai/metrics | grep pricing_scheduled_sync_runs_total
```

---

## Resolution

### Resolution Path 1: Scheduler Disabled

**If**: `PRICING_SYNC_ENABLED=false`

**Action**:
```bash
# Re-enable scheduler
railway variables set PRICING_SYNC_ENABLED=true --environment production

# Redeploy to apply changes
railway redeploy --environment production

# Verify scheduler started
railway logs --environment production --follow | grep "Pricing sync scheduler"
```

**Verification**:
- Wait 30 seconds for initial sync
- Check logs for "Scheduled pricing sync completed"
- Verify `pricing_last_sync_timestamp` updated

---

### Resolution Path 2: Application Restart Needed

**If**: Application is running but scheduler stopped

**Action**:
```bash
# Restart application
railway redeploy --environment production

# Monitor startup
railway logs --environment production --follow
```

**Verification**:
- Look for "Pricing sync scheduler started"
- Wait for first sync (30 seconds)
- Check scheduler status endpoint

---

### Resolution Path 3: Configuration Error

**If**: Environment variables missing or incorrect

**Action**:
```bash
# Set correct configuration
railway variables set PRICING_SYNC_ENABLED=true --environment production
railway variables set PRICING_SYNC_INTERVAL_HOURS=6 --environment production
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud --environment production

# Redeploy
railway redeploy --environment production
```

**Verification**:
- Check scheduler status endpoint shows correct config
- Verify scheduler starts successfully
- Monitor first sync completion

---

### Resolution Path 4: Manual Trigger as Workaround

**If**: Immediate pricing update needed while investigating

**Action**:
```bash
# Trigger manual sync
curl -X POST \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/trigger | jq '.'

# Expected output:
# {
#   "success": true,
#   "status": "success",
#   "total_models_updated": 450,
#   "duration_seconds": 15.2,
#   "triggered_by": "admin@gatewayz.ai"
# }
```

**Note**: This is a temporary workaround, not a permanent solution

---

### Resolution Path 5: Code Issue

**If**: Scheduler code has a bug preventing execution

**Action**:
1. Check Sentry for recent exceptions
2. Review recent code deployments
3. Check for Python import errors
4. Consider rolling back to previous version

```bash
# Check recent deployments
railway logs --environment production | grep "Deployment"

# Rollback if needed (see DEPLOYMENT_GUIDE.md)
git revert <commit-hash>
git push origin main
railway up --environment production
```

---

## Prevention

1. **Monitoring**: Alert is working correctly
2. **Health Checks**: Add scheduler liveness check to health endpoint
3. **Graceful Degradation**: Manual trigger capability exists
4. **Configuration Validation**: Validate env vars at startup
5. **Redundancy**: Consider backup scheduler mechanism

---

## Escalation

**Escalate to Engineering Lead if**:
- Resolution attempts fail after 30 minutes
- Issue recurs multiple times
- Root cause is unclear
- Code changes required

**Escalate to CTO if**:
- Issue persists > 1 hour
- Significant revenue impact
- Customer complaints about incorrect billing

---

## Post-Incident

### Immediate Actions
1. Document what happened in incident log
2. Document what worked and what didn't
3. Update pricing data if significantly stale
4. Notify relevant teams

### Follow-up Actions
1. Review why scheduler stopped
2. Add additional monitoring if needed
3. Improve alerting if false positive
4. Update runbook with learnings
5. Schedule post-mortem if significant impact

---

## Related

- **Deployment Guide**: `docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- **Admin Endpoints**: `src/routes/admin.py`
- **Scheduler Code**: `src/services/pricing_sync_scheduler.py`
- **Alert Definition**: `monitoring/prometheus/pricing_sync_alerts.yml`
- **Dashboard**: Grafana "Pricing Sync Scheduler - Health"

---

## History

| Date | Incident | Resolution | Duration | Impact |
|------|----------|------------|----------|--------|
| - | - | - | - | - |

---

**Last Updated**: 2026-01-26
**Version**: 1.0
**Owner**: Platform Team
