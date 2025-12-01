# Deployment Workflow

This document explains the deployment workflow for Gatewayz API with staging/test environment and manual production deployment.

## Overview

```
┌─────────────────────────────────────────────────────┐
│                 Developer Workflow                   │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────┐
        │  1. Push to main branch         │
        └─────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────┐
        │  2. CI runs (tests, lint, etc)  │
        │     - Pytest                     │
        │     - Ruff/Black                │
        │     - Security scan             │
        │     - Build verification        │
        └─────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────┐
        │  3. AUTO-DEPLOY to STAGING      │
        │     Environment: staging        │
        │     Railway: staging env        │
        └─────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────┐
        │  4. MANUAL TESTING              │
        │     - Test API endpoints        │
        │     - Verify functionality      │
        │     - Check monitoring          │
        │     - Validate changes          │
        └─────────────────────────────────┘
                          │
                ┌─────────┴─────────┐
                │                   │
                ▼                   ▼
        ┌───────────────┐   ┌───────────────┐
        │   Issues      │   │   Looks good! │
        │   Found       │   │               │
        └───────────────┘   └───────────────┘
                │                   │
                ▼                   ▼
        ┌───────────────┐   ┌───────────────────────┐
        │   Fix &       │   │  5. MANUAL DEPLOY     │
        │   Re-deploy   │   │     to PRODUCTION     │
        │   to staging  │   │                       │
        └───────────────┘   │  Via Railway Dashboard│
                            │  or GitHub Actions    │
                            └───────────────────────┘
                                        │
                                        ▼
                            ┌───────────────────────┐
                            │  6. Production Live   │
                            │     Monitor & verify  │
                            └───────────────────────┘
```

## Workflow Steps

### 1. Development & Push

```bash
# Make your changes
git checkout -b feature/my-feature
# ... make changes ...
git commit -m "Add new feature"
git push origin feature/my-feature

# Create PR and merge to main
gh pr create --base main --title "Add new feature"
gh pr merge
```

### 2. Automatic CI & Staging Deployment

When you push to `main`, the following happens **automatically**:

1. **CI Pipeline runs** (`.github/workflows/ci.yml`)
   - Code quality checks (Ruff, Black, isort)
   - Security scan (Bandit)
   - Test suite (Pytest with 4 shards)
   - Build verification

2. **If CI passes** → **Auto-deploy to STAGING** (`.github/workflows/deploy.yml`)
   - Deploys to Railway staging environment
   - Verifies health check
   - Posts notification with staging URL

**Result**: Your changes are now live in the **staging/test environment**!

### 3. Manual Testing on Staging

Test your changes on staging:

```bash
# Set staging URL
export STAGING_URL="https://your-staging-domain"

# Health check
curl $STAGING_URL/health

# Test endpoints
curl $STAGING_URL/v1/models

# Test with test API key
curl -X POST $STAGING_URL/v1/chat/completions \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Test"}]
  }'
```

**Test checklist:**
- [ ] Health endpoint responds
- [ ] API endpoints work correctly
- [ ] New features function as expected
- [ ] No errors in Railway logs
- [ ] Monitoring/Sentry shows no issues

### 4. Manual Production Deployment

Once you've verified staging works correctly, deploy to production **manually**.

#### Option A: Via GitHub Actions (Recommended)

1. Go to **Actions** tab in GitHub
2. Select **"Deploy to Production (Manual)"** workflow
3. Click **"Run workflow"**
4. Type `deploy-to-production` to confirm
5. Click **"Run workflow"**

The workflow will:
- Validate confirmation
- Deploy to production Railway environment
- Verify health check
- Run smoke tests
- Post deployment summary

#### Option B: Via Railway Dashboard

1. Go to https://railway.app/
2. Select your project
3. Switch to **production** environment
4. Click **"Deploy"** button
5. Monitor logs for deployment success

#### Option C: Via Railway CLI

```bash
# Switch to production environment
railway environment --name production

# Deploy
railway up

# Monitor logs
railway logs --follow
```

### 5. Post-Production Verification

After deploying to production:

```bash
# Health check
curl https://api.gatewayz.ai/health

# Quick smoke test
curl https://api.gatewayz.ai/v1/models

# Check Sentry
# Check Grafana/monitoring
# Review logs
```

## Key Points

### ✅ What's Automatic
- Push to main → CI runs
- CI passes → Deploy to staging
- Health checks on staging

### 🖐️ What's Manual
- **Production deployment** (you control when)
- Production verification
- Rollback decisions

### 🔒 Safety Features
- Staging tested before production
- Manual approval for production
- Health checks at every step
- Confirmation required for production deploy
- Separate databases (staging vs production)
- Separate API keys for testing

## Environment Configuration

### Staging (Test) Environment
- **Database**: Separate Supabase project
- **API Keys**: Test keys (gw_test_*)
- **Stripe**: Test mode (sk_test_*)
- **Redis**: Separate or prefixed keys
- **Domain**: `staging.gatewayz.ai` (or your staging domain)
- **Purpose**: Testing before production

### Production Environment
- **Database**: Production Supabase project
- **API Keys**: Real customer keys
- **Stripe**: Live mode (sk_live_*)
- **Redis**: Production instance
- **Domain**: `api.gatewayz.ai`
- **Purpose**: Live customer traffic

## Common Scenarios

### Scenario 1: Normal Feature Deployment

```bash
1. Merge PR to main
2. Wait for staging deployment (automatic, ~2-3 min)
3. Test on staging
4. Deploy to production (manual, via Actions)
5. Verify production
```

**Timeline**: ~10-15 minutes total

### Scenario 2: Hotfix

```bash
1. Create hotfix branch from main
2. Fix the issue
3. Merge to main (fast-track approval)
4. Wait for staging deployment
5. Quick test on staging
6. Immediately deploy to production
```

**Timeline**: ~5-10 minutes

### Scenario 3: Issues Found in Staging

```bash
1. Changes deployed to staging
2. Issues found during testing
3. Create fix branch
4. Merge fix to main
5. Wait for new staging deployment
6. Re-test on staging
7. Deploy to production when ready
```

**Result**: Production never saw the bug!

### Scenario 4: Emergency Rollback

If production has issues:

**Via Railway Dashboard:**
1. Go to Railway project
2. Select production environment
3. Find previous successful deployment
4. Click "Redeploy"

**Via Railway CLI:**
```bash
railway environment --name production
railway rollback
```

## Monitoring

### Staging
- **Logs**: `railway logs --environment staging`
- **Health**: `https://staging-domain/health`
- **Sentry**: Environment filter: "staging"

### Production
- **Logs**: `railway logs --environment production`
- **Health**: `https://api.gatewayz.ai/health`
- **Sentry**: Environment filter: "production"
- **Metrics**: Grafana dashboards

## Troubleshooting

### Staging deployment fails

```bash
# Check CI logs
# Go to Actions tab → View failed workflow

# Check Railway logs
railway logs --environment staging

# Check Railway deployment status
railway status --environment staging
```

### Production deployment blocked

Make sure:
- [ ] Staging is working correctly
- [ ] CI passed on main branch
- [ ] Railway production environment is healthy
- [ ] No ongoing production incidents

### Need to skip staging

⚠️ **Not recommended**, but in emergencies:

You can deploy directly to production via Railway dashboard or CLI, but you lose the safety of staging testing.

## Best Practices

1. **Always test in staging first**
   - Never skip staging testing
   - Verify all critical paths work

2. **Deploy during low-traffic hours**
   - Plan production deployments
   - Monitor after deployment

3. **Keep staging up-to-date**
   - Deploy to staging frequently
   - Keep test data fresh

4. **Monitor after deployment**
   - Watch logs for 5-10 minutes
   - Check Sentry for errors
   - Verify key metrics

5. **Have rollback plan ready**
   - Know how to rollback quickly
   - Keep last known good deployment info

## FAQ

**Q: Can I deploy to production without staging?**
A: Technically yes via Railway dashboard, but not recommended. Staging catches issues before production.

**Q: How long does staging deployment take?**
A: Usually 2-3 minutes after CI completes.

**Q: How do I know staging is working?**
A: Check the deployment notification in GitHub, test the health endpoint, and run your tests.

**Q: What if I need to rollback production?**
A: Use Railway dashboard or CLI to rollback to previous deployment. See "Emergency Rollback" above.

**Q: Can multiple developers deploy to staging?**
A: Yes, but coordinate to avoid conflicts. Last push to main wins.

**Q: Should I delete old staging data?**
A: Yes, re-seed staging data weekly or after major changes. Run: `python scripts/database/seed_test_data.py`

## Related Documentation

- [Testing Environment Setup](./TESTING_ENVIRONMENT.md) - Full setup guide
- [Quick Start](./TESTING_QUICKSTART.md) - Fast setup (~15 min)
- [Railway Configuration](../railway.json) - Railway environment config
- [CI Pipeline](../.github/workflows/ci.yml) - CI workflow details
- [Deploy Workflow](../.github/workflows/deploy.yml) - Staging deployment
- [Production Deploy](../.github/workflows/deploy-production.yml) - Manual production deployment

---

**Summary**: Push to main → Auto-deploy to staging → Manual test → Manual deploy to production 🚀
