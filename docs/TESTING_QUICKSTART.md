# Testing Environment Quick Start

This is a condensed guide to get your staging environment up and running quickly. For detailed information, see [TESTING_ENVIRONMENT.md](./TESTING_ENVIRONMENT.md).

## Prerequisites

- [ ] Railway account and CLI installed (`npm install -g @railway/cli`)
- [ ] Supabase account
- [ ] Railway project already set up for production
- [ ] ~15 minutes of setup time

## Quick Setup (5 Steps)

### 1. Create Staging Supabase Project (3 min)

```bash
# Go to https://app.supabase.com/
# Click "New project"
# Name: gatewayz-staging
# Choose same region as production
# Wait for provisioning

# Get credentials from Settings > API:
# - SUPABASE_URL
# - SUPABASE_KEY (anon key)
```

### 2. Run Database Migrations (2 min)

```bash
# Set staging credentials
export SUPABASE_URL="https://your-staging-project.supabase.co"
export SUPABASE_KEY="your-staging-anon-key"

# Link and push migrations
cd supabase
supabase link --project-ref YOUR_STAGING_PROJECT_REF
supabase db push
```

### 3. Configure Railway Staging Environment (5 min)

**Option A: Automated Script (Recommended)**

```bash
./scripts/setup-staging-environment.sh
```

**Option B: Manual Setup**

```bash
# Login and link
railway login
railway link

# Switch to staging
railway environment --name staging

# Set critical variables
railway variables set APP_ENV=staging
railway variables set SUPABASE_URL="https://your-staging.supabase.co"
railway variables set SUPABASE_KEY="your-staging-key"
railway variables set SENTRY_ENVIRONMENT=staging

# Copy other variables from production (provider keys, etc.)
# Update these to use test mode:
railway variables set STRIPE_SECRET_KEY="sk_test_..."  # Test mode!
railway variables set STRIPE_WEBHOOK_SECRET="whsec_test_..."
```

### 4. Seed Test Data (1 min)

```bash
# Set environment
export APP_ENV=staging
export SUPABASE_URL="https://your-staging.supabase.co"
export SUPABASE_KEY="your-staging-service-key"

# Run seed script
python scripts/database/seed_test_data.py
```

This creates test users, API keys, plans, and coupons.

### 5. Deploy to Staging (2 min)

```bash
# Deploy
railway environment --name staging
railway up

# Verify
railway logs
curl https://your-staging-domain/health
```

## Test Your Staging Environment

```bash
# Set staging URL
export STAGING_URL="https://your-staging-domain"

# Health check
curl $STAGING_URL/health

# Test model catalog
curl $STAGING_URL/v1/models

# Test chat with test API key
curl -X POST $STAGING_URL/v1/chat/completions \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello from staging!"}],
    "max_tokens": 20
  }'
```

## CI/CD Setup (Enable Staged Deployments)

### Add GitHub Secrets

Go to: **Settings > Secrets and variables > Actions > New repository secret**

Add these secrets:
- `RAILWAY_STAGING_DOMAIN`: Your staging domain (e.g., `staging.gatewayz.ai`)
- `RAILWAY_STAGING_TOKEN`: Railway API token (if separate from production)

### Enable the Staged Deployment Workflow

The workflow is already created at `.github/workflows/deploy-staged.yml`.

To use it instead of the regular deploy workflow:

```bash
# Option 1: Rename workflows (use staged as default)
mv .github/workflows/deploy.yml .github/workflows/deploy-direct.yml.disabled
mv .github/workflows/deploy-staged.yml .github/workflows/deploy.yml

# Option 2: Keep both (trigger staged manually)
# Go to Actions tab > Staged Deployment > Run workflow
```

### Deployment Flow

```
Push to main
    ↓
CI Pipeline (tests, lint, build)
    ↓
Deploy to Staging ✓
    ↓
Run Integration Tests on Staging ✓
    ↓
Deploy to Production ✓
    ↓
Verify Production ✓
```

## Test API Keys

Use these in your staging tests:

| Plan | API Key |
|------|---------|
| Free | `gw_test_free_key_12345` |
| Starter | `gw_test_starter_key_12345` |
| Pro | `gw_test_pro_key_12345` |
| Enterprise | `gw_test_enterprise_key_12345` |

## Test Coupons

| Code | Discount |
|------|----------|
| TEST10 | 10% off |
| TEST50 | 50% off |
| TESTFREE | 100% off |

## Development Workflow

```bash
# 1. Create feature branch
git checkout -b feature/my-feature

# 2. Make changes and commit
git add .
git commit -m "Add new feature"

# 3. Push and create PR
git push origin feature/my-feature
gh pr create --base main --title "My Feature"

# 4. Merge PR → Triggers CI

# 5. CI passes → Auto-deploys to staging

# 6. Staging tests pass → Auto-deploys to production

# 7. Verify production deployment
curl https://api.gatewayz.ai/health
```

## Common Commands

```bash
# Check Railway staging logs
railway logs --environment staging

# Check Railway staging variables
railway variables --environment staging

# Re-seed test data
APP_ENV=staging python scripts/database/seed_test_data.py

# Switch between environments
railway environment --name staging
railway environment --name production

# Deploy manually
railway up --environment staging
railway up --environment production
```

## Troubleshooting

### Staging deployment fails

```bash
# Check logs
railway logs --environment staging

# Verify environment variables
railway variables --environment staging

# Check Supabase connection
curl https://your-staging.supabase.co/rest/v1/
```

### Test API keys don't work

```bash
# Re-run seed script
APP_ENV=staging python scripts/database/seed_test_data.py

# Verify keys in database
# Go to Supabase dashboard > Table Editor > api_keys
```

### Database migrations not applied

```bash
# Check migration status
supabase migration list

# Re-apply migrations
supabase db push
```

## Next Steps

- ✅ Set up automatic deployments on PR merge
- ✅ Configure monitoring alerts for staging
- ✅ Set up weekly data refresh for staging
- ✅ Add more integration tests
- ✅ Document environment-specific behaviors

## Resources

- **Full Documentation**: [docs/TESTING_ENVIRONMENT.md](./TESTING_ENVIRONMENT.md)
- **Railway Dashboard**: https://railway.app/
- **Supabase Dashboard**: https://app.supabase.com/
- **GitHub Actions**: [Actions tab](../../actions)

## Support

If you encounter issues:
1. Check Railway logs: `railway logs --environment staging`
2. Check Supabase logs: Supabase Dashboard > Logs
3. Review GitHub Actions: Repository > Actions tab
4. Check health endpoint: `curl https://your-staging-domain/health`

---

**Setup Time**: ~15 minutes
**Maintenance**: Minimal (weekly data refresh recommended)
**Value**: Test deployments safely before production! 🚀
