# Health Caching Deployment Guide

## Pre-Deployment Checklist

### Environment Setup
- [x] Redis configured and running
- [x] REDIS_URL set in environment
- [x] Redis connection pooling enabled
- [x] API keys configured for admin endpoints

### Code Review
- [x] Health cache service implemented
- [x] Cache invalidation service implemented
- [x] Admin cache endpoints created
- [x] Health endpoints updated with caching
- [x] Routes registered in main app
- [x] Documentation complete

### Testing
- [ ] Unit tests for cache service
- [ ] Integration tests for endpoints
- [ ] Load tests with 100+ concurrent users
- [ ] Cache hit rate verification
- [ ] Compression ratio verification

## Deployment Steps

### Step 1: Pre-Deployment Testing (Staging)

```bash
# 1. Deploy to staging environment
git push staging main

# 2. Wait for deployment to complete
# Monitor logs for any errors

# 3. Test cache functionality
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://staging-api.gatewayz.ai/health/system

# 4. Check cache status
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://staging-api.gatewayz.ai/admin/cache/health/status

# 5. Verify compression
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://staging-api.gatewayz.ai/admin/cache/health/stats
```

### Step 2: Performance Verification

```bash
# 1. Run load test
ab -n 1000 -c 100 \
  -H "Authorization: Bearer YOUR_API_KEY" \
  https://staging-api.gatewayz.ai/health/system

# 2. Monitor response times
# Expected: < 50ms for cache hits

# 3. Check cache hit rate
# Expected: > 85%

# 4. Verify bandwidth reduction
# Expected: 84% reduction from original
```

### Step 3: Production Deployment

```bash
# 1. Create deployment branch
git checkout -b deploy/health-caching

# 2. Tag release
git tag -a v2.1.0-health-caching -m "Health monitoring caching"

# 3. Push to production
git push origin deploy/health-caching
git push origin v2.1.0-health-caching

# 4. Monitor deployment
# Watch logs for any errors

# 5. Verify endpoints
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/health/system
```

### Step 4: Post-Deployment Verification

```bash
# 1. Test all health endpoints
curl https://api.gatewayz.ai/health/system
curl https://api.gatewayz.ai/health/providers
curl https://api.gatewayz.ai/health/models
curl https://api.gatewayz.ai/health/dashboard
curl https://api.gatewayz.ai/health/summary

# 2. Check admin endpoints
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/admin/cache/health/status

# 3. Monitor metrics
# Check cache hit rates
# Check response times
# Check error rates

# 4. Verify Redis
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/admin/cache/redis/info
```

## Monitoring During Deployment

### Key Metrics to Watch

1. **Cache Hit Rate**
   - Expected: 85-95%
   - Alert if: < 70%
   - Check: `/admin/cache/health/stats`

2. **Response Time**
   - Expected: 5-10ms (cache hit)
   - Alert if: > 100ms
   - Check: Application metrics

3. **Error Rate**
   - Expected: < 0.1%
   - Alert if: > 1%
   - Check: Error logs

4. **Redis Memory**
   - Expected: < 100MB
   - Alert if: > 500MB
   - Check: `/admin/cache/redis/info`

5. **Compression Ratio**
   - Expected: 80-85%
   - Alert if: < 70%
   - Check: `/admin/cache/health/compression-stats`

### Monitoring Commands

```bash
# Watch cache statistics
watch -n 5 'curl -s -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/health/stats | jq'

# Watch Redis info
watch -n 5 'curl -s -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/redis/info | jq'

# Watch cache status
watch -n 5 'curl -s -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/health/status | jq'
```

## Rollback Plan

### If Issues Occur

```bash
# 1. Disable caching (temporary)
# Set environment variable
HEALTH_CACHE_ENABLED=false

# 2. Clear cache
curl -X POST -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/admin/cache/redis/clear

# 3. Revert deployment
git revert HEAD
git push origin main

# 4. Monitor recovery
# Check error rates
# Check response times
# Verify endpoints working
```

### Partial Rollback

```bash
# If only specific endpoint has issues:

# 1. Invalidate that endpoint's cache
curl -X DELETE -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/admin/cache/health/dashboard

# 2. Force refresh
curl -X POST -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/admin/cache/health/refresh

# 3. Monitor endpoint
# Check response times
# Check error rates
```

## Post-Deployment Tasks

### Day 1 (Immediate)
- [x] Verify all endpoints working
- [x] Check cache hit rates
- [x] Monitor error rates
- [x] Verify Redis connectivity
- [x] Check response times

### Day 2-7 (First Week)
- [ ] Monitor cache hit rates trend
- [ ] Verify compression ratios
- [ ] Check Redis memory usage
- [ ] Review error logs
- [ ] Collect performance metrics

### Week 2+ (Ongoing)
- [ ] Monitor long-term trends
- [ ] Adjust TTLs if needed
- [ ] Implement additional optimizations
- [ ] Document lessons learned
- [ ] Plan next improvements

## Performance Baseline

### Before Deployment
```
Response Time (p95): 250-500ms
Payload Size: 13-15 KB
Bandwidth/Request: 13-15 KB
Concurrent Users: ~100
Error Rate: 0.5-1%
```

### Expected After Deployment
```
Response Time (p95): 10-50ms (cache hits)
Payload Size: 1.4-2.4 KB
Bandwidth/Request: 1.4-2.4 KB
Concurrent Users: ~500+
Error Rate: 0.1-0.2%
```

## Troubleshooting

### Cache Not Working

```bash
# 1. Check Redis connection
curl -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/redis/info

# 2. Check cache status
curl -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/health/status

# 3. Check application logs
# Look for Redis connection errors
# Look for cache service errors

# 4. Restart Redis if needed
# Or clear cache and retry
```

### High Response Times

```bash
# 1. Check cache hit rate
curl -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/health/stats

# 2. If hit rate is low:
# - Check Redis memory
# - Check Redis performance
# - Verify network connectivity

# 3. If hit rate is high but response slow:
# - Check decompression performance
# - Check database performance
# - Check network latency
```

### High Memory Usage

```bash
# 1. Check Redis memory
curl -H "Authorization: Bearer KEY" \
  https://api.gatewayz.ai/admin/cache/redis/info

# 2. If memory high:
# - Clear cache
# - Reduce TTLs
# - Increase compression threshold

# 3. Monitor after changes
# Verify memory usage decreases
```

## Success Criteria

✅ **Deployment Successful If:**
- Cache hit rate > 85%
- Response time < 50ms (p95)
- Error rate < 0.2%
- Bandwidth reduction > 80%
- Redis memory < 100MB
- All endpoints responding
- No error spikes

⚠️ **Investigate If:**
- Cache hit rate < 70%
- Response time > 100ms
- Error rate > 1%
- Bandwidth reduction < 70%
- Redis memory > 500MB
- Any endpoint timing out

## Support & Escalation

### Issues During Deployment

1. **Redis Connection Issues**
   - Check REDIS_URL
   - Verify Redis is running
   - Check network connectivity
   - Review Redis logs

2. **High Error Rates**
   - Check application logs
   - Verify cache service
   - Check database connectivity
   - Review error patterns

3. **Performance Issues**
   - Check cache hit rates
   - Monitor Redis performance
   - Check database queries
   - Review network latency

### Contact & Escalation

- **Immediate Issues**: Check logs and admin endpoints
- **Performance Issues**: Review metrics and adjust configuration
- **Redis Issues**: Contact DevOps team
- **Application Issues**: Review code and logs

## Documentation References

- **Full Guide**: `docs/HEALTH_CACHING_OPTIMIZATION.md`
- **Quick Reference**: `docs/CACHE_QUICK_REFERENCE.md`
- **Implementation**: `docs/IMPLEMENTATION_HEALTH_CACHING.md`
- **Additional Optimizations**: `docs/ADDITIONAL_OPTIMIZATIONS.md`

## Deployment Timeline

```
T-1 day:  Final testing on staging
T-0:      Deploy to production
T+0:      Verify endpoints
T+1h:     Check metrics
T+4h:     Review performance
T+1d:     Full verification
T+7d:     Performance analysis
T+30d:    Long-term monitoring
```

## Sign-Off

- [ ] Code reviewed
- [ ] Tests passed
- [ ] Staging verified
- [ ] Monitoring configured
- [ ] Rollback plan ready
- [ ] Documentation complete
- [ ] Team notified
- [ ] Ready for production deployment

---

**Deployment Date**: _______________
**Deployed By**: _______________
**Verified By**: _______________
**Notes**: _______________
