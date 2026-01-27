# Runbook: Pricing Sync Slow Performance

**Alert**: `PricingSyncSlowDuration`
**Severity**: Warning
**Team**: Platform
**Component**: pricing_sync

---

## Symptoms

- Alert triggered: "Pricing sync taking longer than expected"
- Average sync duration > 60 seconds over 1 hour
- `pricing_scheduled_sync_duration_seconds` metric elevated
- Syncs completing but slowly
- No failures, just slow performance

---

## Impact

**Business Impact**:
- Minimal immediate impact (syncs still completing)
- Potential for future degradation
- Increased resource consumption
- Higher infrastructure costs

**Technical Impact**:
- Longer sync cycles
- Increased CPU/memory usage
- Higher database load
- Potential timeout risks if continues to degrade

**Severity Justification**:
- Warning (not critical) because syncs are still succeeding
- Indicates performance degradation trend
- Requires investigation but not immediate action

---

## Diagnosis

### Step 1: Check Current Sync Duration

```bash
# Check recent sync durations
railway logs --environment production | grep "Scheduled pricing sync completed" | tail -10

# Example output:
# ✅ Scheduled pricing sync completed successfully (duration: 45.2s, updated: 450)
# ✅ Scheduled pricing sync completed successfully (duration: 68.3s, updated: 450)
```

**Normal**: 10-30 seconds
**Slow**: 30-60 seconds
**Critical**: >60 seconds

### Step 2: Check Prometheus Metrics

```bash
# Check average sync duration
curl https://api.gatewayz.ai/metrics | grep pricing_scheduled_sync_duration

# Check per-provider timing if available
curl https://api.gatewayz.ai/metrics | grep pricing_provider_request_duration
```

### Step 3: Check System Resources

```bash
# Check CPU usage
curl https://api.gatewayz.ai/metrics | grep process_cpu_seconds_total

# Check memory usage
curl https://api.gatewayz.ai/metrics | grep process_resident_memory_bytes

# Check database connection pool
curl https://api.gatewayz.ai/metrics | grep db_connection_pool
```

### Step 4: Identify Bottleneck

**Possible bottlenecks**:
1. **Provider API response times** - Slow provider responses
2. **Database performance** - Slow insert/update queries
3. **Network latency** - Poor connectivity to providers or database
4. **Resource contention** - High CPU/memory usage
5. **Large data volume** - More models than expected

### Step 5: Check Provider Response Times

```bash
# Test provider API response times manually
time curl -H "Authorization: Bearer $OPENROUTER_KEY" \
  https://openrouter.ai/api/v1/models > /dev/null

time curl -H "Authorization: Bearer $FEATHERLESS_KEY" \
  https://api.featherless.ai/v1/models > /dev/null

# Repeat for all providers
```

**Normal**: < 2 seconds per provider
**Slow**: 2-5 seconds per provider
**Critical**: > 5 seconds per provider

### Step 6: Check Database Performance

```bash
# Check database query times
psql $SUPABASE_URL -c "
  SELECT query, calls, total_time, mean_time, max_time
  FROM pg_stat_statements
  WHERE query LIKE '%model_pricing%'
  ORDER BY mean_time DESC
  LIMIT 10;
"

# Check for long-running queries
psql $SUPABASE_URL -c "
  SELECT pid, now() - query_start AS duration, query
  FROM pg_stat_activity
  WHERE state = 'active'
  AND query NOT LIKE '%pg_stat_activity%'
  ORDER BY duration DESC;
"
```

---

## Resolution

### Resolution Path 1: Slow Provider Responses

**If**: Provider API response times are elevated

**Action**:
```bash
# Option A: Remove slowest provider temporarily
railway variables set PRICING_SYNC_PROVIDERS=openrouter,featherless --environment production

# Option B: Increase sync interval to reduce frequency
railway variables set PRICING_SYNC_INTERVAL_HOURS=12 --environment production

# Redeploy
railway redeploy --environment production
```

**Verification**:
- Monitor next sync duration
- Check if performance improves
- Verify all required providers still syncing

---

### Resolution Path 2: Database Query Optimization

**If**: Database queries are slow

**Action**:
```bash
# Check for missing indexes
psql $SUPABASE_URL -c "
  SELECT schemaname, tablename, attname, n_distinct, correlation
  FROM pg_stats
  WHERE tablename = 'model_pricing'
  AND (n_distinct < -0.5 OR correlation < 0.5);
"

# Check table statistics
psql $SUPABASE_URL -c "
  ANALYZE model_pricing;
"

# Consider adding indexes (requires migration)
# Example:
# CREATE INDEX IF NOT EXISTS idx_model_pricing_provider
#   ON model_pricing(provider_id);
```

**Verification**:
- Query times improve
- Sync duration decreases
- No impact on application performance

---

### Resolution Path 3: Network Latency

**If**: Network connectivity is poor

**Action**:
```bash
# Test network latency to providers
railway run --environment production -- \
  sh -c "time curl -o /dev/null -s https://openrouter.ai"

# Test database connectivity
railway run --environment production -- \
  sh -c "time psql $SUPABASE_URL -c 'SELECT 1'"

# If network issues persistent:
# 1. Contact hosting provider (Railway)
# 2. Consider region changes
# 3. Check for routing issues
```

**Verification**:
- Network latency returns to normal
- Sync performance improves
- No packet loss or timeouts

---

### Resolution Path 4: Resource Contention

**If**: CPU or memory usage is high during syncs

**Action**:
```bash
# Check resource usage during sync
curl https://api.gatewayz.ai/metrics | grep -E "(cpu|memory)"

# Option A: Scale up application
railway scale --memory 2GB --environment production

# Option B: Reduce concurrent operations
# (Requires code change to add rate limiting in sync logic)

# Option C: Reduce sync frequency
railway variables set PRICING_SYNC_INTERVAL_HOURS=12 --environment production
railway redeploy --environment production
```

**Verification**:
- Resource usage normalizes
- Sync performance improves
- No other application impact

---

### Resolution Path 5: Data Volume Growth

**If**: Number of models has significantly increased

**Action**:
```bash
# Check model count
psql $SUPABASE_URL -c "
  SELECT provider_id, COUNT(*) as model_count
  FROM model_pricing
  GROUP BY provider_id
  ORDER BY model_count DESC;
"

# If model count is very high (>10,000):
# Option A: Add pagination to sync logic
# Option B: Sync providers sequentially instead of concurrently
# Option C: Filter out deprecated/unused models
# (All require code changes)

# Temporary: Increase interval
railway variables set PRICING_SYNC_INTERVAL_HOURS=12 --environment production
railway redeploy --environment production
```

**Verification**:
- Sync completes successfully
- Duration within acceptable range
- All necessary models updated

---

### Resolution Path 6: Code Optimization

**If**: Profiling shows inefficient code

**Action**:
```bash
# Add profiling to sync code (requires code change)
# Profile areas to optimize:
# 1. Model transformation logic
# 2. Database batch insert size
# 3. HTTP connection pooling
# 4. Concurrent vs sequential provider fetching

# Example optimization opportunities:
# - Increase batch size for DB inserts
# - Reuse HTTP connections
# - Cache provider API responses
# - Parallelize provider fetching more efficiently
```

**Verification**:
- Code changes deployed
- Sync duration improves
- No regressions in functionality

---

## Acceptable Performance Thresholds

| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| **Avg Sync Duration** | < 30s | 30-60s | > 60s |
| **p95 Sync Duration** | < 45s | 45-90s | > 90s |
| **Provider API Response** | < 2s | 2-5s | > 5s |
| **Database Query Time** | < 100ms | 100-500ms | > 500ms |
| **Models Updated** | 400-600 | 200-400 | < 200 |

---

## Optimization Ideas

### Short-term Optimizations
1. Remove slowest provider temporarily
2. Increase sync interval
3. Scale up application resources
4. Optimize database queries

### Long-term Optimizations
1. Implement provider response caching
2. Add database query result caching
3. Batch database operations more efficiently
4. Add connection pooling for provider APIs
5. Implement progressive sync (only changed models)
6. Add provider health checks before syncing
7. Parallelize provider fetching more efficiently

---

## Prevention

1. **Baseline Monitoring**: Track normal sync durations
2. **Capacity Planning**: Monitor data volume growth
3. **Performance Testing**: Load test sync logic
4. **Query Optimization**: Regular database query reviews
5. **Provider SLAs**: Understand provider performance guarantees
6. **Resource Monitoring**: Track CPU/memory trends

---

## Escalation

**Escalate to Engineering Lead if**:
- Sync duration continues to increase
- Performance degradation impacts other systems
- Optimization requires significant code changes
- Issue persists > 24 hours

**Escalate to Database Team if**:
- Database queries consistently slow
- Index optimization needed
- Database tuning required

**Escalate to Infrastructure if**:
- Network latency issues
- Resource scaling needed
- Hosting provider issues

---

## Post-Incident

### Immediate Actions
1. Document what caused the slowdown
2. Document which optimization worked
3. Monitor performance after changes
4. Update performance baselines

### Follow-up Actions
1. Implement long-term optimizations
2. Add additional performance monitoring
3. Review capacity planning
4. Update sync logic if needed
5. Document performance tuning guide

### Metrics to Track
- Sync duration over time
- Provider response time trends
- Database query performance
- Resource usage correlation
- Model count growth rate

---

## Related

- **Scheduler Code**: `src/services/pricing_sync_scheduler.py`
- **Sync Service**: `src/services/pricing_sync_service.py`
- **Provider Clients**: `src/services/*_client.py`
- **Alert Definition**: `monitoring/prometheus/pricing_sync_alerts.yml`
- **Dashboard**: Grafana "Pricing Sync System Impact"
- **Other Runbooks**:
  - High Error Rate
  - Scheduler Stopped
  - Database Update Failures

---

## Performance Analysis Checklist

- [ ] Check recent sync durations
- [ ] Check Prometheus metrics
- [ ] Test provider API response times
- [ ] Check database query performance
- [ ] Check system resource usage (CPU/memory)
- [ ] Check network latency
- [ ] Check model count growth
- [ ] Review recent code changes
- [ ] Check for database locks/contention
- [ ] Review error logs for warnings

---

## History

| Date | Issue | Root Cause | Optimization | Result | Duration After |
|------|-------|------------|--------------|--------|----------------|
| - | - | - | - | - | - |

---

**Last Updated**: 2026-01-26
**Version**: 1.0
**Owner**: Platform Team
