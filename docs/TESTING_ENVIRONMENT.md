# Testing Environment Setup Guide

This guide explains how to set up and use the staging/testing environment on Railway with a separate testing database.

## Overview

The testing environment allows you to:
- Test deployments before they reach production
- Verify database migrations in a safe environment
- Test with realistic data without affecting production
- Validate API changes with separate API keys
- Use Stripe test mode for payment testing

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  GitHub Repository                       │
│                                                          │
│  Branches:                                              │
│  • develop  → Auto-deploy to staging                   │
│  • staging  → Auto-deploy to staging                   │
│  • main     → Auto-deploy to production (after CI)    │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              CI Pipeline (GitHub Actions)                │
│                                                          │
│  1. Lint & Code Quality                                 │
│  2. Security Scan                                        │
│  3. Run Tests (4 shards)                                │
│  4. Build Verification                                   │
│  5. Deployment Check                                     │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────┐          ┌──────────────────────┐
│   Railway Staging    │          │  Railway Production  │
│                      │          │                      │
│  Environment:        │          │  Environment:        │
│  • APP_ENV=staging   │          │  • APP_ENV=production│
│  • Staging Supabase  │          │  • Prod Supabase     │
│  • Stripe Test Mode  │          │  • Stripe Live Mode  │
│  • Separate Redis    │          │  • Prod Redis        │
│  • Test API Keys     │          │  • Real API Keys     │
└──────────────────────┘          └──────────────────────┘
```

## Step 1: Create Staging Supabase Project

### 1.1 Create New Supabase Project

1. Go to [Supabase Dashboard](https://app.supabase.com/)
2. Click "New project"
3. Name it: `gatewayz-staging` (or similar)
4. Choose same region as production for consistency
5. Set strong database password
6. Wait for project to provision (~2 minutes)

### 1.2 Get Staging Database Credentials

Once created, get these values from **Settings > API**:
- `SUPABASE_URL`: Your staging project URL
- `SUPABASE_KEY`: Your staging anon/public key
- `SUPABASE_SERVICE_KEY`: Your staging service role key (for migrations)

### 1.3 Run Database Migrations

Apply all migrations to the staging database:

```bash
# Set staging database credentials
export SUPABASE_URL="https://your-staging-project.supabase.co"
export SUPABASE_KEY="your-staging-anon-key"

# Link to staging project
cd supabase
supabase link --project-ref your-staging-project-ref

# Apply all migrations
supabase db push

# Verify tables were created
supabase db remote exec "SELECT tablename FROM pg_tables WHERE schemaname = 'public';"
```

### 1.4 Seed Test Data

Run the seed script to populate test users, API keys, plans, etc:

```bash
# Set environment to staging
export APP_ENV=staging
export SUPABASE_URL="https://your-staging-project.supabase.co"
export SUPABASE_KEY="your-staging-service-key"

# Run seed script
python scripts/database/seed_test_data.py
```

This creates:
- Test users with different plans (free, starter, pro, enterprise)
- Test API keys for each user
- Sample subscription plans
- Test coupons
- Sample transaction history

## Step 2: Configure Railway Staging Environment

### 2.1 Create Railway Environment

Railway already has staging environment configured in `railway.json`. Configure it via Railway CLI or Dashboard.

**Via Railway CLI:**

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to project
railway link

# Switch to staging environment
railway environment --name staging

# Set environment variables
railway variables set APP_ENV=staging
railway variables set SUPABASE_URL="https://your-staging-project.supabase.co"
railway variables set SUPABASE_KEY="your-staging-anon-key"
```

**Via Railway Dashboard:**

1. Go to your Railway project
2. Click on your service
3. Go to **Variables** tab
4. Select **staging** environment from dropdown
5. Add/update variables (see full list below)

### 2.2 Required Environment Variables for Staging

Copy all environment variables from production, but replace these with staging-specific values:

```bash
# Environment
APP_ENV=staging

# Supabase (Staging)
SUPABASE_URL=https://your-staging-project.supabase.co
SUPABASE_KEY=your_staging_anon_key

# Stripe (Test Mode)
STRIPE_SECRET_KEY=sk_test_...  # Your Stripe test key
STRIPE_WEBHOOK_SECRET=whsec_...  # Webhook secret for test endpoint
STRIPE_PUBLISHABLE_KEY=pk_test_...  # For frontend

# Redis (Staging - can be shared or separate)
REDIS_URL=redis://your-staging-redis:6379
# OR use Railway's Redis addon (recommended - separate instance)

# Email (Test Mode - optional)
RESEND_API_KEY=re_...  # Can use same key but test domain
# OR use a test email service

# Analytics (Staging)
STATSIG_SDK_KEY=secret-...  # Staging environment key
POSTHOG_KEY=phc_...  # Staging project key

# Sentry (Staging)
SENTRY_DSN=https://...  # Same DSN, but SENTRY_ENVIRONMENT will differentiate
SENTRY_ENVIRONMENT=staging  # Important!

# Provider Keys (Can use same keys as production OR separate test keys)
OPENROUTER_API_KEY=sk-...
PORTKEY_API_KEY=...
# ... (copy other provider keys)

# Security
ENCRYPTION_KEY=...  # Should be different from production
JWT_SECRET=...  # Should be different from production
```

### 2.3 Set Up Staging Redis (Optional but Recommended)

**Option A: Separate Redis Instance**

```bash
# Via Railway CLI
railway service add redis

# Link to staging environment
railway environment --name staging
railway service link redis
```

**Option B: Shared Redis with Different Keys**

If using the same Redis instance, ensure your code prefixes keys with environment:

```python
# In src/config/redis_config.py
def get_redis_key(key: str) -> str:
    env = os.getenv("APP_ENV", "development")
    return f"{env}:{key}"
```

## Step 3: Update CI/CD Workflow

### 3.1 Enhanced Deploy Workflow

The existing `.github/workflows/deploy.yml` already supports staging. Let's enhance it to require staging success before production:

Create `.github/workflows/deploy-staged.yml`:

```yaml
name: Staged Deployment (Staging → Production)

on:
  push:
    branches:
      - main
    paths:
      - "src/**"
      - "requirements.txt"
      - "pyproject.toml"
      - "railway.json"
  workflow_dispatch:

jobs:
  # Step 1: Deploy to Staging
  deploy-staging:
    name: Deploy to Staging
    uses: ./.github/workflows/deploy.yml
    with:
      environment: staging
    secrets: inherit

  # Step 2: Run Integration Tests on Staging
  test-staging:
    name: Test Staging Deployment
    runs-on: ubuntu-latest
    needs: deploy-staging

    steps:
      - uses: actions/checkout@v4

      - name: Wait for staging deployment to stabilize
        run: sleep 30

      - name: Run integration tests against staging
        env:
          API_BASE_URL: ${{ secrets.RAILWAY_STAGING_DOMAIN }}
          TEST_API_KEY: gw_test_pro_key_12345
        run: |
          # Health check
          curl -f https://$API_BASE_URL/health || exit 1

          # Run integration tests
          python scripts/integration-tests/test_api_live.sh staging

      - name: Verify critical endpoints
        env:
          API_BASE_URL: ${{ secrets.RAILWAY_STAGING_DOMAIN }}
        run: |
          # Test catalog
          curl -f https://$API_BASE_URL/v1/models || exit 1

          # Test chat (with test key)
          curl -f -X POST https://$API_BASE_URL/v1/chat/completions \
            -H "Authorization: Bearer gw_test_pro_key_12345" \
            -H "Content-Type: application/json" \
            -d '{"model":"gpt-3.5-turbo","messages":[{"role":"user","content":"test"}]}' \
            || exit 1

  # Step 3: Deploy to Production (only if staging tests pass)
  deploy-production:
    name: Deploy to Production
    needs: [deploy-staging, test-staging]
    uses: ./.github/workflows/deploy.yml
    with:
      environment: production
    secrets: inherit

  # Step 4: Verify Production
  verify-production:
    name: Verify Production Deployment
    runs-on: ubuntu-latest
    needs: deploy-production

    steps:
      - uses: actions/checkout@v4

      - name: Wait for production deployment
        run: sleep 30

      - name: Health check
        env:
          API_BASE_URL: ${{ secrets.RAILWAY_DOMAIN }}
        run: |
          curl -f https://$API_BASE_URL/health || exit 1

      - name: Smoke tests
        run: |
          # Basic smoke tests on production
          curl -f https://$API_BASE_URL/v1/models || exit 1
```

### 3.2 Update GitHub Secrets

Add these secrets to your GitHub repository:

```
Settings > Secrets and variables > Actions > New repository secret
```

**Required Secrets:**
- `RAILWAY_STAGING_DOMAIN`: Your staging domain (e.g., `staging.gatewayz.ai`)
- `RAILWAY_STAGING_TOKEN`: Railway token for staging environment
- `SUPABASE_URL`: Staging Supabase URL
- `SUPABASE_KEY`: Staging Supabase key

## Step 4: Testing Workflow

### 4.1 Development Flow

```bash
# 1. Create feature branch
git checkout -b feature/new-feature

# 2. Make changes
# ... edit code ...

# 3. Test locally
pytest tests/
python src/main.py  # Test server locally

# 4. Push to staging branch
git push origin feature/new-feature

# 5. Create PR to staging branch
gh pr create --base staging --title "New Feature"

# 6. Merge to staging → Auto-deploys to staging environment
gh pr merge

# 7. Test on staging
curl https://staging.gatewayz.ai/health
# Run integration tests
# Manual QA testing

# 8. If staging looks good, merge staging → main
git checkout main
git merge staging
git push origin main

# 9. CI runs, deploys to staging first, then production
```

### 4.2 Manual Testing on Staging

Use test API keys provided by the seed script:

```bash
# Set base URL
export API_URL="https://staging.gatewayz.ai"

# Test with different plan levels
# Free tier
curl -X POST $API_URL/v1/chat/completions \
  -H "Authorization: Bearer gw_test_free_key_12345" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Pro tier
curl -X POST $API_URL/v1/chat/completions \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Check user credits
curl $API_URL/v1/users/me \
  -H "Authorization: Bearer gw_test_pro_key_12345"

# Test coupon
curl -X POST $API_URL/v1/coupons/validate \
  -H "Content-Type: application/json" \
  -d '{"code": "TEST10"}'
```

### 4.3 Automated Integration Tests

Run the full integration test suite:

```bash
# Against staging
export API_BASE_URL="https://staging.gatewayz.ai"
export TEST_API_KEY="gw_test_pro_key_12345"

python scripts/integration-tests/test_api_live.sh
```

## Step 5: Monitoring & Observability

### 5.1 Separate Monitoring for Staging

**Sentry:**
- Uses same DSN but different environment: `SENTRY_ENVIRONMENT=staging`
- Filters staging errors separately in Sentry dashboard

**Grafana/Prometheus:**
- Add `environment` label to all metrics
- Create separate dashboards for staging vs production

**Logs:**
- Staging logs are prefixed with `[STAGING]`
- Use Railway logs or configure Loki with environment labels

### 5.2 Health Checks

Both environments have health checks at `/health`:

```bash
# Staging
curl https://staging.gatewayz.ai/health

# Production
curl https://api.gatewayz.ai/health
```

## Step 6: Database Management

### 6.1 Migrations

Always test migrations on staging first:

```bash
# 1. Create migration locally
supabase migration new add_new_feature

# 2. Write migration SQL
# Edit supabase/migrations/XXXXXX_add_new_feature.sql

# 3. Test on local Supabase
supabase db reset

# 4. Push to staging
export SUPABASE_URL="https://your-staging-project.supabase.co"
supabase db push

# 5. Verify on staging
# Test the feature

# 6. If good, push to production
export SUPABASE_URL="https://your-prod-project.supabase.co"
supabase db push
```

### 6.2 Data Refresh

Periodically refresh staging data to match production structure (but not data):

```bash
# Backup staging
supabase db dump --data-only > staging-backup.sql

# Clear old data
supabase db reset

# Re-seed with fresh test data
python scripts/database/seed_test_data.py
```

## Step 7: Troubleshooting

### 7.1 Staging Deployment Fails

```bash
# Check Railway logs
railway logs --environment staging

# Check environment variables
railway variables --environment staging

# Verify Supabase connection
curl https://your-staging-project.supabase.co/rest/v1/
```

### 7.2 Tests Pass Locally but Fail on Staging

- Check environment-specific configs
- Verify provider API keys are set correctly
- Check rate limits (may be different on staging)
- Review Sentry errors for staging environment

### 7.3 Database Migration Issues

```bash
# Check migration status
supabase migration list

# Repair if needed
supabase migration repair --status applied XXXXXX_migration_name

# Rollback if necessary
supabase db reset  # WARNING: Drops all data
```

## Best Practices

1. **Always test on staging first**
   - Never push directly to production
   - Require staging approval before production deploy

2. **Keep staging data fresh**
   - Re-seed weekly or after major changes
   - Don't let staging accumulate stale data

3. **Use test mode for external services**
   - Stripe test mode
   - Test email addresses
   - Sandbox provider keys (if available)

4. **Monitor both environments**
   - Set up separate alerts for staging
   - Use environment labels in all monitoring tools

5. **Document staging-specific behavior**
   - Rate limits may be different
   - Some providers may use test keys
   - Payments are in test mode

6. **Clean up regularly**
   - Remove old test data
   - Clear Redis cache periodically
   - Archive old staging logs

## Reference: Test Data

### Test Users

| Email | Plan | Credits | API Key |
|-------|------|---------|---------|
| test-free@gatewayz.ai | Free | 100 | `gw_test_free_key_12345` |
| test-starter@gatewayz.ai | Starter | 1,000 | `gw_test_starter_key_12345` |
| test-pro@gatewayz.ai | Pro | 10,000 | `gw_test_pro_key_12345` |
| test-enterprise@gatewayz.ai | Enterprise | 100,000 | `gw_test_enterprise_key_12345` |

### Test Coupons

| Code | Discount | Max Uses |
|------|----------|----------|
| TEST10 | 10% | 100 |
| TEST50 | 50% | 10 |
| TESTFREE | 100% | 5 |

### Test Credit Cards (Stripe Test Mode)

| Card Number | Description |
|-------------|-------------|
| 4242 4242 4242 4242 | Success |
| 4000 0000 0000 0002 | Decline |
| 4000 0000 0000 9995 | Insufficient funds |

## Next Steps

1. ✅ Create staging Supabase project
2. ✅ Run database migrations on staging
3. ✅ Configure Railway staging environment
4. ✅ Seed test data
5. ✅ Update CI/CD workflow
6. ✅ Test a deployment to staging
7. ✅ Verify all endpoints work
8. ✅ Test production deployment process

---

**Questions or Issues?**
- Check Railway logs: `railway logs --environment staging`
- Check Supabase logs: [Supabase Dashboard](https://app.supabase.com/)
- Review GitHub Actions: [Actions tab](../../actions)
- Check health endpoint: `https://staging.gatewayz.ai/health`
