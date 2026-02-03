# Phase 1 Deployment Guide - Backend Stability Improvements

This guide covers deploying the Phase 1 stability improvements (#1040, #1041) to staging and production environments.

## Overview

**What's Being Deployed:**
- Response caching with Redis (#1041)
- Read replica support for catalog queries (#1040)

**Expected Impact:**
- 99% faster response times (500ms-2s → 5-10ms cached)
- 98% reduction in primary database queries
- 70% reduction in connection pool usage
- Elimination of 499 timeout errors

**Risk Level:** LOW (graceful fallbacks, optional features)

---

## Pre-Deployment Checklist

### Environment Requirements

- [ ] Redis instance running and accessible
- [ ] `REDIS_URL` environment variable configured
- [ ] (Optional) Supabase read replica provisioned
- [ ] (Optional) `SUPABASE_READ_REPLICA_URL` configured
- [ ] Prometheus/Grafana configured for metrics

### Code Verification

```bash
# Verify commits are present
git log --oneline -2
# Should show:
# c0ae3f1f feat: implement read replica support for catalog queries (#1040)
# 61dccd8b feat: implement aggressive catalog response caching with Redis (#1041)

# Verify files exist
ls -la src/services/catalog_response_cache.py
ls -la src/config/supabase_config.py

# Run syntax checks
python3 -m py_compile src/services/catalog_response_cache.py
python3 -m py_compile src/config/supabase_config.py
```

---

## Staging Deployment

### Step 1: Deploy Code

#### Railway Deployment
```bash
# Push to staging branch
git checkout -b staging
git merge main
git push origin staging

# Or use Railway CLI
railway up --service gatewayz-backend-staging
```

#### Vercel Deployment
```bash
# Deploy to staging environment
vercel --env staging

# Or via Vercel UI
# - Go to Vercel dashboard
# - Select staging environment
# - Deploy main branch
```

### Step 2: Configure Environment Variables

#### Required Variables
```bash
# Redis (required for caching)
export REDIS_URL="redis://your-redis-host:6379/0"
# Or in Railway/Vercel UI:
REDIS_URL=redis://your-redis-host:6379/0
```

#### Optional Variables (Recommended)
```bash
# Read replica (optional but recommended)
export SUPABASE_READ_REPLICA_URL="https://your-replica-id.supabase.co"

# In Railway/Vercel UI:
SUPABASE_READ_REPLICA_URL=https://your-replica-id.supabase.co
```

### Step 3: Verify Deployment

#### Health Checks
```bash
# Basic health check
curl https://staging-api.gatewayz.ai/health
# Expected: {"status": "healthy"}

# Database health
curl https://staging-api.gatewayz.ai/health/database
# Expected: {"database": "connected", ...}
```

#### Feature Verification
```bash
# Test caching (first request - cache MISS)
time curl "https://staging-api.gatewayz.ai/models?limit=10"
# Expected: 200-500ms response time

# Second request (cache HIT)
time curl "https://staging-api.gatewayz.ai/models?limit=10"
# Expected: 5-20ms response time (much faster!)

# Check logs for cache hits
railway logs | grep "Cache HIT"
# Or
vercel logs | grep "Cache HIT"
```

#### Metrics Verification
```bash
# Check Prometheus metrics
curl https://staging-api.gatewayz.ai/metrics | grep catalog_cache
# Expected:
# catalog_cache_hits_total{gateway="all"} 1
# catalog_cache_misses_total{gateway="all"} 1

# Check read replica metrics (if configured)
curl https://staging-api.gatewayz.ai/metrics | grep read_replica
# Expected:
# read_replica_queries_total{table="models",status="success"} 5
```

### Step 4: Load Testing

```bash
# Install Apache Bench if needed
# macOS: brew install httpd
# Linux: sudo apt-get install apache2-utils

# Test catalog endpoint under load
ab -n 1000 -c 50 https://staging-api.gatewayz.ai/models?limit=10

# Expected results:
# - Requests per second: 100-200+ (vs 2-5 before)
# - Mean time per request: <100ms (vs 500ms+ before)
# - Failed requests: 0 (vs 5-10% before)
```

### Step 5: Monitor for 24 Hours

#### What to Watch

**Cache Hit Rate** (Target: >90%)
```bash
# Calculate hit rate
curl -s https://staging-api.gatewayz.ai/metrics | \
  grep catalog_cache | \
  awk '{print $2}' | \
  python3 -c "
import sys
hits, misses = [float(l) for l in sys.stdin]
print(f'Hit rate: {hits/(hits+misses)*100:.1f}%')
"
```

**Connection Pool Usage** (Target: <50% primary, <70% replica)
```bash
# Check connection pool stats
curl https://staging-api.gatewayz.ai/optimization-monitor
```

**Error Rates** (Target: <0.1% for 499/504)
```bash
# Check error rates
curl -s https://staging-api.gatewayz.ai/metrics | \
  grep 'http_requests_total.*499\|http_requests_total.*504'
```

**Response Times** (Target: P95 <200ms)
```bash
# Check response time histogram
curl -s https://staging-api.gatewayz.ai/metrics | \
  grep 'http_request_duration_seconds.*models'
```

---

## Production Deployment

### Prerequisites

- [ ] Staging deployment successful for 24+ hours
- [ ] Cache hit rate >90%
- [ ] No increase in error rates
- [ ] Response times improved
- [ ] Connection pool usage reduced

### Deployment Steps

#### Option 1: Gradual Rollout (Recommended)

**Step 1: Deploy code without read replica**
```bash
# Deploy to production
git checkout main
git push origin main

# Do NOT set SUPABASE_READ_REPLICA_URL yet
# This deploys caching only (lower risk)
```

**Step 2: Monitor caching for 24 hours**
- Verify cache hit rate >90%
- Confirm response times improved
- Check no increase in errors

**Step 3: Enable read replica**
```bash
# Add read replica URL to production
# In Railway/Vercel UI:
SUPABASE_READ_REPLICA_URL=https://your-prod-replica.supabase.co

# Restart service
railway restart
# Or redeploy on Vercel
```

**Step 4: Monitor for 24 hours**
- Verify primary DB load drops
- Confirm replica queries increasing
- Check no connection errors

#### Option 2: Full Deployment

```bash
# Deploy with both features enabled
git checkout main
git push origin main

# Set all environment variables
REDIS_URL=redis://...
SUPABASE_READ_REPLICA_URL=https://...

# Deploy and monitor closely
```

### Post-Deployment Validation

```bash
# Run the same tests as staging
curl https://api.gatewayz.ai/health
curl https://api.gatewayz.ai/metrics | grep catalog_cache
curl https://api.gatewayz.ai/metrics | grep read_replica

# Load test
ab -n 1000 -c 50 https://api.gatewayz.ai/models?limit=10
```

---

## Monitoring & Alerting

### Grafana Dashboard

Create a dashboard with these panels:

**Panel 1: Cache Hit Rate**
```promql
# Cache hit rate percentage
sum(rate(catalog_cache_hits_total[5m])) /
(sum(rate(catalog_cache_hits_total[5m])) + sum(rate(catalog_cache_misses_total[5m]))) * 100
```

**Panel 2: Response Time**
```promql
# P95 response time for /models endpoint
histogram_quantile(0.95,
  rate(http_request_duration_seconds_bucket{path="/models"}[5m])
)
```

**Panel 3: Connection Pool Usage**
```promql
# Primary DB pool utilization
connection_pool_utilization{client_type="primary"}
```

**Panel 4: Read Replica Usage**
```promql
# Read replica queries per second
rate(read_replica_queries_total[5m])
```

### Alerts

**High Priority Alerts:**

```yaml
# Cache hit rate too low
- alert: CacheHitRateLow
  expr: |
    sum(rate(catalog_cache_hits_total[10m])) /
    (sum(rate(catalog_cache_hits_total[10m])) + sum(rate(catalog_cache_misses_total[10m]))) < 0.8
  for: 15m
  annotations:
    summary: Cache hit rate below 80% for 15 minutes

# Connection pool saturation
- alert: ConnectionPoolSaturated
  expr: connection_pool_utilization{client_type="primary"} > 0.8
  for: 5m
  annotations:
    summary: Primary DB connection pool above 80%

# Read replica errors
- alert: ReadReplicaErrors
  expr: rate(read_replica_connection_errors_total[5m]) > 1
  for: 5m
  annotations:
    summary: Read replica experiencing connection errors
```

---

## Rollback Procedures

### Quick Rollback (Environment Variables)

If issues occur, you can disable features without code rollback:

**Disable Response Caching:**
```bash
# Option 1: Remove Redis URL (caching disabled)
# In Railway/Vercel: Delete REDIS_URL variable

# Option 2: Clear Redis cache
redis-cli FLUSHDB

# Service will fall back to normal database queries
```

**Disable Read Replica:**
```bash
# Remove read replica URL
# In Railway/Vercel: Delete SUPABASE_READ_REPLICA_URL variable

# Service will fall back to primary database
```

### Full Code Rollback

If severe issues occur:

```bash
# Revert both commits
git revert c0ae3f1f  # Read replica
git revert 61dccd8b  # Response caching
git push origin main

# Or reset to previous commit
git reset --hard <commit-before-changes>
git push --force origin main  # Use with caution!

# Redeploy
railway up
# Or
vercel --prod
```

### Rollback Verification

```bash
# Verify rollback successful
curl https://api.gatewayz.ai/health

# Check metrics no longer showing new features
curl https://api.gatewayz.ai/metrics | grep catalog_cache
# Should return nothing if caching disabled

# Monitor error rates
curl https://api.gatewayz.ai/metrics | grep http_requests_total
# Should remain stable or improve
```

---

## Troubleshooting

### Issue: Cache Hit Rate Low (<80%)

**Symptoms:**
- `catalog_cache_hits_total` not increasing
- Response times not improving

**Diagnosis:**
```bash
# Check Redis connectivity
redis-cli ping
# Should return PONG

# Check cache keys exist
redis-cli KEYS "catalog:v2:*"
# Should show cache keys

# Check TTL
redis-cli TTL "catalog:v2:all:12345678"
# Should show seconds remaining (0-300)
```

**Solutions:**
- Verify `REDIS_URL` correct
- Check Redis not overloaded
- Increase cache TTL if needed (default: 300s)

### Issue: Read Replica Not Being Used

**Symptoms:**
- `read_replica_queries_total` not increasing
- Primary pool usage still high

**Diagnosis:**
```bash
# Check logs for replica initialization
grep "read replica" logs/
# Should see: "✅ Read replica client initialized"

# Check environment variable
echo $SUPABASE_READ_REPLICA_URL
# Should be set

# Check replica connectivity
curl https://your-replica.supabase.co/rest/v1/
# Should return 200
```

**Solutions:**
- Verify `SUPABASE_READ_REPLICA_URL` correct
- Check replica is online in Supabase dashboard
- Verify API key has access to replica

### Issue: Increased Error Rates

**Symptoms:**
- 500 errors increasing
- Sentry showing new errors

**Diagnosis:**
```bash
# Check error logs
grep ERROR logs/ | tail -20

# Common errors:
# - "Cache write failed" → Redis issue (non-critical)
# - "Read replica connection failed" → Replica offline (falls back to primary)
# - "RuntimeError: Supabase unavailable" → Primary DB issue (unrelated)
```

**Solutions:**
- Cache write failures: Non-critical, investigate Redis
- Replica errors: Disable replica, investigate separately
- Primary DB errors: Unrelated to new features, investigate DB

---

## Success Criteria

### Week 1 (Staging)
- [ ] Cache hit rate >90%
- [ ] P95 response time <200ms
- [ ] Primary pool usage <50%
- [ ] Zero increase in error rates
- [ ] No 499/504 errors for 48 hours

### Week 2 (Production)
- [ ] Cache hit rate >90% for 7 days
- [ ] P95 response time <100ms
- [ ] Primary pool usage <30%
- [ ] Read replica handling 70%+ of queries
- [ ] Uptime >99.9%

### Month 1
- [ ] All metrics sustained for 30 days
- [ ] Zero emergency rollbacks
- [ ] User-reported performance improvements
- [ ] Database costs stable or reduced

---

## Post-Deployment Tasks

### Immediate (Week 1)
- [ ] Monitor metrics dashboard daily
- [ ] Review Sentry for new error patterns
- [ ] Check cache hit rates hourly
- [ ] Validate connection pool usage

### Short-term (Week 2-4)
- [ ] Write post-deployment report
- [ ] Update runbooks with new procedures
- [ ] Train team on new monitoring
- [ ] Document lessons learned

### Long-term (Month 2+)
- [ ] Optimize cache TTL based on data
- [ ] Consider adding more cache layers
- [ ] Evaluate read replica costs vs benefits
- [ ] Plan Phase 2 improvements

---

## Support & Escalation

### During Deployment Hours
- Monitor Slack #backend-stability channel
- Check Sentry dashboard every 30 minutes
- Be ready to rollback within 5 minutes

### Emergency Contacts
- Backend Lead: [Name]
- DevOps: [Name]
- On-call: Check PagerDuty

### Escalation Criteria
Rollback immediately if:
- Error rate increases >5%
- Response times >2s for >5 minutes
- Cache completely failing
- Read replica causing errors

---

## Appendix

### Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | Yes* | None | Redis connection URL for caching |
| `SUPABASE_READ_REPLICA_URL` | No | None | Read replica URL (optional) |
| `SUPABASE_URL` | Yes | None | Primary database URL |
| `SUPABASE_KEY` | Yes | None | Database API key |

*Required for caching; system works without it but no caching benefit

### Useful Commands

```bash
# Check service status
railway status
vercel inspect

# View logs in real-time
railway logs --tail
vercel logs --follow

# Check environment variables
railway variables
vercel env ls

# Restart service
railway restart
vercel rollback  # If needed

# Connect to Redis
redis-cli -u $REDIS_URL

# Check database connection
psql $DATABASE_URL -c "SELECT 1"
```

---

**Last Updated**: February 3, 2026
**Version**: 1.0
**Related Issues**: #1039, #1040, #1041, #1055
