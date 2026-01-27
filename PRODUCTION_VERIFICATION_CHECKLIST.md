# Production Verification Checklist - Issue #960

Quick reference checklist for production readiness verification.

## 🚀 Quick Start

```bash
# Get production admin key
export SUPABASE_URL="https://your-production-instance.supabase.co"
export SUPABASE_KEY="your_production_service_role_key"
python3 scripts/get_production_admin_key.py

# Run verification
export PROD_ADMIN_KEY=$(cat .admin_key_production)
python3 scripts/verify_production_readiness.py
```

## ✅ Verification Steps

### 1. Environment Variables
```bash
railway variables --environment production | grep PRICING_SYNC
```
- [ ] `PRICING_SYNC_ENABLED` exists
- [ ] `PRICING_SYNC_INTERVAL_HOURS=6` (NOT 3)
- [ ] `PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud`

### 2. Database Schema
```sql
SELECT tablename FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('model_pricing_history', 'pricing_sync_log');
```
- [ ] `model_pricing_history` table exists
- [ ] `pricing_sync_log` table exists

### 3. Production Health
```bash
curl https://api.gatewayz.ai/health
```
- [ ] Returns 200 OK
- [ ] Status: "healthy"
- [ ] Database: "connected"

### 4. Admin Endpoints
```bash
curl -H "Authorization: Bearer $PROD_ADMIN_KEY" \
  https://api.gatewayz.ai/admin/pricing/scheduler/status
```
- [ ] Returns 200 or 404 (both acceptable)
- [ ] Authentication working

### 5. Metrics Endpoint
```bash
curl https://api.gatewayz.ai/metrics | grep pricing_
```
- [ ] Endpoint accessible
- [ ] Pricing metrics present

### 6. Monitoring Infrastructure
- [ ] Prometheus scraping production
- [ ] Grafana dashboards ready
- [ ] Alerts configured
- [ ] Notifications set up

### 7. Deployment Checklist
- [ ] Code merged to main
- [ ] All tests passing
- [ ] Staging verified (24-48h)
- [ ] Documentation complete
- [ ] Team notified
- [ ] Rollback plan ready

### 8. Configuration Validation
- [ ] Production interval: 6 hours
- [ ] Production providers: 4 (all enabled)
- [ ] Monitoring: Required and configured
- [ ] Logging: INFO level

## 📋 Pre-Deployment

### Code
- [ ] Phase 2.5 code in main
- [ ] Phase 3 code in main
- [ ] Dependencies updated

### Database
- [ ] Migration applied
- [ ] Tables verified
- [ ] RLS policies active

### Monitoring
- [ ] Prometheus ready
- [ ] Grafana ready
- [ ] Alerts ready
- [ ] Runbooks ready

### Communication
- [ ] Team notified
- [ ] Window scheduled
- [ ] On-call updated
- [ ] Rollback communicated

## ✍️ Approvals

- [ ] Engineering Lead: ________________
- [ ] DevOps Lead: ________________
- [ ] Product Owner: ________________

**Deployment Window**: ________________

## 📊 Verification Results

```bash
# View automated verification report
cat production_verification_report_*.json | jq '.summary'
```

Expected:
- Passed: 5/5 automated checks
- Manual: 2 verified
- Ready: true

## 🚨 Deployment Blockers

**DO NOT DEPLOY IF**:
- ❌ Any automated check fails
- ❌ Database migration not applied
- ❌ Environment variables incorrect
- ❌ Monitoring not configured
- ❌ Staging unstable
- ❌ No rollback plan

## 📚 References

- Full Guide: `docs/PRODUCTION_VERIFICATION_GUIDE.md`
- Deployment: `docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- Rollback: `docs/ROLLBACK_PROCEDURE.md`

---

**Issue**: #960
**Status**: ⬜ Not Started | 🔄 In Progress | ✅ Complete
**Date**: ________________
