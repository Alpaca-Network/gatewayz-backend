# Deployment Guide - Quick Reference

## Workflow Summary

```
Push to main → CI ✓ → Auto-deploy to STAGING → Manual Test → Manual deploy to PRODUCTION
```

## For Developers

### 1. Deploy Your Changes to Staging (Automatic)

```bash
# Merge your PR to main
gh pr merge

# Wait 2-3 minutes, your changes are automatically deployed to staging
# Check the GitHub Actions notification for the staging URL
```

### 2. Test on Staging

```bash
# Test your changes
curl https://your-staging-domain/health
curl https://your-staging-domain/v1/models

# Use test API key
curl -X POST https://your-staging-domain/v1/chat/completions \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-3.5-turbo","messages":[{"role":"user","content":"test"}]}'
```

### 3. Deploy to Production (Manual)

**Via GitHub Actions** (Recommended):
1. Go to **Actions** tab
2. Select **"Deploy to Production (Manual)"**
3. Click **"Run workflow"**
4. Type `deploy-to-production` to confirm
5. Click **"Run workflow"**

**Via Railway Dashboard**:
1. Go to https://railway.app/
2. Select production environment
3. Click "Deploy"

**Via Railway CLI**:
```bash
railway environment --name production
railway up
```

## Test API Keys

Use these for staging tests:
- Free: `gw_test_free_key_12345`
- Pro: `gw_test_pro_key_12345`
- Enterprise: `gw_test_enterprise_key_12345`

## Quick Commands

```bash
# View staging logs
railway logs --environment staging

# View production logs
railway logs --environment production

# Rollback production (if needed)
railway rollback --environment production

# Re-seed staging test data
APP_ENV=staging python scripts/database/seed_test_data.py
```

## Setup (First Time Only)

If you haven't set up the staging environment yet:

```bash
# Quick setup (~15 minutes)
# See: docs/TESTING_QUICKSTART.md

1. Create staging Supabase project
2. Run: ./scripts/setup-staging-environment.sh
3. Run: python scripts/database/seed_test_data.py
4. Deploy to staging: railway up --environment staging
```

## Documentation

- **Quick Start**: [docs/TESTING_QUICKSTART.md](./docs/TESTING_QUICKSTART.md) - 15 min setup
- **Full Workflow**: [docs/DEPLOYMENT_WORKFLOW.md](./docs/DEPLOYMENT_WORKFLOW.md) - Complete guide
- **Environment Setup**: [docs/TESTING_ENVIRONMENT.md](./docs/TESTING_ENVIRONMENT.md) - Detailed setup

## Environment URLs

- **Staging**: https://your-staging-domain (set in `RAILWAY_STAGING_DOMAIN` secret)
- **Production**: https://api.gatewayz.ai (set in `RAILWAY_DOMAIN` secret)

## Key Points

✅ **Automatic:**
- Push to main → Deploy to staging
- CI checks run first
- Health verification

🖐️ **Manual:**
- Production deployment (you control)
- Staging testing
- Production verification

🔒 **Safety:**
- Separate databases
- Test API keys
- Confirmation required for production
- Easy rollback

## Need Help?

- Check [docs/DEPLOYMENT_WORKFLOW.md](./docs/DEPLOYMENT_WORKFLOW.md) for detailed guide
- Check Railway logs: `railway logs --environment <staging|production>`
- Check GitHub Actions: [Actions tab](../../actions)
- Check health endpoint: `curl https://your-domain/health`

---

**Remember**: Always test in staging before deploying to production! 🚀
