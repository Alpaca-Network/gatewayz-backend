# Pricing Scheduler Deployment Checklist

Quick reference checklist for deploying Phase 2.5 automated pricing scheduler.

---

## Pre-Deployment

- [ ] All Phase 2.5/3/4 commits merged to staging
- [ ] All tests passing (`pytest tests/services/test_pricing_sync_scheduler.py tests/routes/test_admin.py`)
- [ ] Documentation complete
- [ ] Team notified of deployment

---

## Environment Variables

### Staging
- [ ] `PRICING_SYNC_ENABLED=true`
- [ ] `PRICING_SYNC_INTERVAL_HOURS=3`
- [ ] `PRICING_SYNC_PROVIDERS=openrouter,featherless`

### Production
- [ ] `PRICING_SYNC_ENABLED=true`
- [ ] `PRICING_SYNC_INTERVAL_HOURS=6`
- [ ] `PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud`

---

## Staging Deployment

- [ ] Merge to staging branch
- [ ] Configure environment variables
- [ ] Deploy to staging: `railway up --environment staging`
- [ ] Verify health endpoint: `curl https://gatewayz-staging.up.railway.app/health`
- [ ] Check scheduler started: `railway logs | grep "Pricing sync scheduler started"`
- [ ] Test status endpoint: `GET /admin/pricing/scheduler/status`
- [ ] Wait for first sync (30 seconds)
- [ ] Verify sync completed successfully
- [ ] Monitor for 24-48 hours

---

## Staging Verification (24-48 hours)

- [ ] Scheduled syncs running every 3 hours
- [ ] All syncs completing successfully
- [ ] No critical errors in logs
- [ ] Admin endpoints working
- [ ] Metrics being collected
- [ ] Performance metrics normal

---

## Production Deployment

- [ ] Staging stable for 24-48 hours
- [ ] Merge to main branch
- [ ] Configure production environment variables
- [ ] Deploy to production: `railway up --environment production`
- [ ] Verify health endpoint: `curl https://api.gatewayz.ai/health`
- [ ] Check scheduler started
- [ ] Test status endpoint
- [ ] Wait for first sync (30 seconds)
- [ ] Verify sync completed successfully

---

## Post-Deployment Monitoring

### First 30 Minutes
- [ ] No critical errors
- [ ] Application healthy
- [ ] Scheduler running
- [ ] First sync completed

### First 6 Hours
- [ ] One scheduled sync completed
- [ ] Sync successful
- [ ] Metrics accurate
- [ ] No performance issues

### First 24 Hours
- [ ] 4 scheduled syncs completed (every 6h)
- [ ] All syncs successful
- [ ] No user-reported issues
- [ ] Monitoring alerts configured

---

## Rollback Triggers

Rollback immediately if:
- [ ] Scheduler causing application crashes
- [ ] Database performance severely degraded
- [ ] Critical errors in logs
- [ ] User-facing functionality broken
- [ ] Memory/CPU usage abnormal

---

## Success Criteria

- [ ] Scheduler runs automatically
- [ ] All syncs complete successfully
- [ ] Admin endpoints accessible
- [ ] No critical errors
- [ ] Metrics collecting
- [ ] Team satisfied with deployment

---

## Quick Commands

```bash
# Check scheduler status
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status | jq '.'

# Trigger manual sync
curl -X POST -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/trigger | jq '.'

# Check metrics
curl https://api.gatewayz.ai/metrics | grep pricing

# Monitor logs
railway logs --follow | grep pricing

# Disable scheduler (if needed)
railway variables set PRICING_SYNC_ENABLED=false && railway redeploy
```

---

**For detailed instructions, see**: [PHASE_5_DEPLOYMENT_GUIDE.md](./PHASE_5_DEPLOYMENT_GUIDE.md)
