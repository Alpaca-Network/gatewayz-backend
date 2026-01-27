# Runbook: Pricing Sync High Error Rate

**Alert**: `PricingSyncHighErrorRate`
**Severity**: Critical
**Team**: Platform
**Component**: pricing_sync

---

## Symptoms

- Alert triggered: "High error rate in pricing sync scheduler"
- Error rate > 50% over 1 hour
- `pricing_scheduled_sync_runs_total{status="failed"}` increasing
- Multiple failed sync attempts in logs
- Sentry showing pricing sync errors

---

## Impact

**Business Impact**:
- Pricing data not being updated
- Potential billing inaccuracies
- Stale pricing information for customers
- Provider pricing changes not reflected

**Technical Impact**:
- Automated sync system degraded
- Manual intervention required
- Increased error logging and alerting noise
- Potential database or provider API issues

**Severity Justification**:
- Critical because majority of syncs are failing
- Pricing data will become increasingly stale
- Indicates systemic issue, not transient failure

---

## Diagnosis

### Step 1: Check Recent Errors

```bash
# Check for recent error messages
railway logs --environment production | grep "Scheduled pricing sync failed" | tail -20

# Check for error patterns
railway logs --environment production | grep -i "error.*pricing" -A 5 | tail -50
```

**Look for**:
- Provider API errors (401, 403, 429, 500)
- Database connection errors
- Timeout errors
- Parsing/transformation errors

### Step 2: Check Sentry

```bash
# Open Sentry dashboard
open https://sentry.io/organizations/gatewayz/issues/

# Look for:
# - pricing_sync_scheduler errors
# - run_scheduled_sync errors
# - Provider client errors
```

**Common error patterns**:
- `HTTPError: 401 Unauthorized` - Invalid API keys
- `HTTPError: 429 Too Many Requests` - Rate limiting
- `TimeoutError` - Slow provider responses
- `JSONDecodeError` - Invalid API responses
- `DatabaseError` - Database issues

### Step 3: Check Provider API Status

```bash
# Check provider status pages:
open https://status.openrouter.ai
open https://status.featherless.ai
# etc.

# Test provider APIs manually
curl -H "Authorization: Bearer $OPENROUTER_KEY" \
  https://openrouter.ai/api/v1/models

curl -H "Authorization: Bearer $FEATHERLESS_KEY" \
  https://api.featherless.ai/v1/models
```

**Check for**:
- Provider outages or degraded performance
- API changes or deprecations
- Rate limit exhaustion
- Invalid authentication

### Step 4: Check Database Health

```bash
# Check database connectivity
psql $SUPABASE_URL -c "SELECT 1"

# Check for locks or slow queries
psql $SUPABASE_URL -c "
  SELECT pid, usename, state, query, query_start
  FROM pg_stat_activity
  WHERE query LIKE '%model_pricing%'
  AND state != 'idle'
  ORDER BY query_start;
"

# Check table health
psql $SUPABASE_URL -c "
  SELECT schemaname, tablename, n_live_tup, n_dead_tup
  FROM pg_stat_user_tables
  WHERE tablename = 'model_pricing';
"
```

### Step 5: Check Error Rate Metrics

```bash
# Check error rate over time
curl https://api.gatewayz.ai/metrics | grep pricing_scheduled_sync_runs_total

# Calculate error rate
# errors / total = error_rate
```

---

## Resolution

### Resolution Path 1: Provider API Key Issues

**If**: Logs show 401 Unauthorized errors

**Action**:
```bash
# Check which provider is failing
railway logs | grep "401.*Unauthorized" -B 2

# Verify API keys
railway variables --environment production | grep -E "(OPENROUTER|FEATHERLESS|NEARAI|ALIBABA).*KEY"

# Test API key directly
curl -H "Authorization: Bearer $PROVIDER_KEY" \
  https://provider-api-url/models

# If key is invalid, update it
railway variables set PROVIDER_API_KEY=<new-key> --environment production
railway redeploy --environment production
```

**Verification**:
- Wait for next scheduled sync
- Check logs for successful sync
- Verify error rate decreases

---

### Resolution Path 2: Rate Limiting

**If**: Logs show 429 Too Many Requests errors

**Action**:
```bash
# Identify which provider is rate limiting
railway logs | grep "429.*Too Many Requests" -B 2

# Option A: Reduce sync frequency
railway variables set PRICING_SYNC_INTERVAL_HOURS=12 --environment production

# Option B: Remove problematic provider temporarily
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless --environment production

# Redeploy
railway redeploy --environment production
```

**Verification**:
- Monitor next sync attempt
- Check provider stays within rate limits
- Consider permanent interval adjustment

---

### Resolution Path 3: Provider API Outage

**If**: Provider status page shows outage

**Action**:
```bash
# Remove affected provider from sync list temporarily
current_providers=$(railway variables | grep PRICING_SYNC_PROVIDERS | cut -d= -f2)
echo "Current providers: $current_providers"

# Remove problematic provider (example: removing nearai)
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless,alibaba-cloud --environment production

# Redeploy
railway redeploy --environment production
```

**Verification**:
- Error rate should decrease immediately
- Successful syncs for remaining providers
- Re-add provider when outage resolved

---

### Resolution Path 4: Database Issues

**If**: Database connection or query errors

**Action**:
```bash
# Check database connection pool
curl https://api.gatewayz.ai/metrics | grep db_connection_pool

# Check for database locks
psql $SUPABASE_URL -c "
  SELECT blocked_locks.pid AS blocked_pid,
         blocking_locks.pid AS blocking_pid,
         blocked_activity.query AS blocked_query,
         blocking_activity.query AS blocking_query
  FROM pg_locks blocked_locks
  JOIN pg_stat_activity blocked_activity ON blocked_locks.pid = blocked_activity.pid
  JOIN pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
  JOIN pg_stat_activity blocking_activity ON blocking_locks.pid = blocking_activity.pid
  WHERE NOT blocked_locks.granted;
"

# If severe database issues, consider:
# 1. Restart database connection pool
# 2. Contact Supabase support
# 3. Temporarily disable scheduler
railway variables set PRICING_SYNC_ENABLED=false --environment production
railway redeploy --environment production
```

**Verification**:
- Database queries succeed
- No lock contention
- Sync operations complete successfully

---

### Resolution Path 5: Code Bug

**If**: Consistent errors across all providers

**Action**:
```bash
# Check recent deployments
railway logs | grep "Deployment" | tail -5

# Check Sentry for stack traces
# Review error patterns for code issues

# If code bug identified, rollback:
git log --oneline -5
git revert <problematic-commit>
git push origin main
railway up --environment production
```

**Verification**:
- Error rate returns to normal
- Successful syncs resume
- No regressions in functionality

---

### Resolution Path 6: Transient Network Issues

**If**: Timeout errors or connection failures

**Action**:
```bash
# Check network connectivity from application
railway run --environment production -- curl -I https://openrouter.ai

# Check DNS resolution
railway run --environment production -- nslookup openrouter.ai

# Increase timeout values if needed (requires code change)
# Monitor for improvement over time
# Transient issues should resolve automatically
```

**Verification**:
- Network connectivity restored
- Sync operations succeed
- Error rate normalizes

---

## Temporary Workaround

While investigating, ensure pricing stays current:

```bash
# Trigger manual syncs periodically
watch -n 3600 'curl -X POST \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/trigger'
```

---

## Prevention

1. **Provider Redundancy**: Multiple providers reduce single point of failure
2. **Rate Limit Buffers**: Stay well below provider rate limits
3. **Retry Logic**: Implement exponential backoff for transient failures
4. **Provider Health Checks**: Pre-validate provider availability
5. **Circuit Breakers**: Skip failing providers temporarily
6. **Error Budget**: Accept some level of transient failures

---

## Escalation

**Escalate to Engineering Lead if**:
- Error rate persists > 30 minutes after initial mitigation
- Multiple providers affected simultaneously
- Database issues detected
- Code changes required

**Escalate to Provider Support if**:
- Provider API consistently failing
- Unexpected rate limiting
- API behavior changed without notice

**Escalate to CTO if**:
- Issue persists > 2 hours
- All resolution paths exhausted
- Significant business impact

---

## Post-Incident

### Immediate Actions
1. Document which providers/errors caused the issue
2. Document successful resolution path
3. Verify pricing data accuracy
4. Clear stale alerts

### Follow-up Actions
1. Review error patterns for trends
2. Improve error handling if needed
3. Adjust rate limits or intervals
4. Update provider API key rotation schedule
5. Schedule post-mortem if >1 hour incident

### Metrics to Track
- Time to detection (how long until alert fired)
- Time to resolution (how long to fix)
- Number of failed syncs
- Impact on pricing accuracy

---

## Related

- **Scheduler Code**: `src/services/pricing_sync_scheduler.py`
- **Provider Clients**: `src/services/*_client.py`
- **Alert Definition**: `monitoring/prometheus/pricing_sync_alerts.yml`
- **Dashboard**: Grafana "Pricing Sync Scheduler - Health"
- **Other Runbooks**:
  - Scheduler Stopped
  - Slow Sync Performance
  - Database Update Failures

---

## Common Error Messages

| Error Message | Cause | Resolution |
|---------------|-------|------------|
| `401 Unauthorized` | Invalid API key | Update provider API key |
| `429 Too Many Requests` | Rate limit exceeded | Reduce frequency or remove provider |
| `TimeoutError: ...` | Slow provider response | Increase timeout or remove provider |
| `ConnectionError: ...` | Network issue | Check connectivity, wait for resolution |
| `JSONDecodeError: ...` | Invalid API response | Check provider API changes |
| `IntegrityError: ...` | Database constraint | Check data model consistency |

---

## History

| Date | Incident | Root Cause | Resolution | Duration | Error Rate |
|------|----------|------------|------------|----------|------------|
| - | - | - | - | - | - |

---

**Last Updated**: 2026-01-26
**Version**: 1.0
**Owner**: Platform Team
