# Staging Environment - Complete Setup Guide

Everything you need to set up and secure your Railway staging environment.

## 📚 Documentation Overview

Your staging environment setup includes:

### 1. **Complete Setup Guide**
[docs/RAILWAY_STAGING_SETUP.md](./docs/RAILWAY_STAGING_SETUP.md)

**Step-by-step guide covering:**
- Creating staging Supabase database
- Setting up Railway staging environment
- Configuring all environment variables
- Running database migrations
- Seeding test data
- Deploying to staging
- Verifying everything works

**Time required:** 30-45 minutes (first time)

---

### 2. **Security Guide**
[docs/STAGING_API_SECURITY.md](./docs/STAGING_API_SECURITY.md)

**How to prevent unauthorized access:**
- IP whitelisting (Railway feature)
- Custom authentication tokens
- Basic authentication
- CORS/domain restrictions
- Rate limiting
- Combined security strategies

**Choose your security level and implement it.**

---

### 3. **Quick Enable Security**
[docs/ENABLE_STAGING_SECURITY.md](./docs/ENABLE_STAGING_SECURITY.md)

**Fast implementation (5 minutes):**
- Generate security token
- Set in Railway
- Add middleware to code
- Deploy and test

---

### 4. **Deployment Workflow**
[docs/DEPLOYMENT_WORKFLOW.md](./docs/DEPLOYMENT_WORKFLOW.md)

**Your new workflow:**
```
Push to main → CI ✓ → Auto-deploy to STAGING → You test → Manual deploy to PRODUCTION
```

---

## 🚀 Quick Start (30 Minutes)

Follow these steps to get staging running:

### Part 1: Create Staging Database (5 min)

1. Go to https://app.supabase.com/
2. Create new project: `gatewayz-staging`
3. Save credentials (URL, API keys)

### Part 2: Set Up Railway Environment (10 min)

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to project
railway link

# Run automated setup
./scripts/setup-staging-environment.sh
```

This will guide you through setting all required environment variables.

### Part 3: Database Setup (5 min)

```bash
# Link to staging Supabase
cd supabase
supabase link --project-ref YOUR_STAGING_REF

# Run migrations
supabase db push

# Seed test data
cd ..
APP_ENV=staging python scripts/database/seed_test_data.py
```

### Part 4: Deploy to Staging (5 min)

```bash
# Deploy
railway environment switch staging
railway up

# Get staging URL
railway domain

# Test it works
curl https://your-staging-domain/health
```

### Part 5: Enable Security (5 min)

```bash
# Generate token
python3 -c "import secrets; print('staging_' + secrets.token_urlsafe(32))"

# Set in Railway
railway variables set STAGING_ACCESS_TOKEN="your-generated-token"

# Add middleware to src/main.py (see ENABLE_STAGING_SECURITY.md)
# Commit and push
git add src/main.py src/middleware/
git commit -m "Add staging security"
git push origin main
```

### Part 6: Configure GitHub (2 min)

1. Go to GitHub → Settings → Secrets
2. Add: `RAILWAY_STAGING_DOMAIN` = your staging domain

✅ **Done!** Staging is set up and secured.

---

## 📦 What Was Created

### Scripts
- ✅ `scripts/setup-staging-environment.sh` - Interactive Railway setup
- ✅ `scripts/database/seed_test_data.py` - Seed test data

### Middleware
- ✅ `src/middleware/staging_security.py` - Security middleware

### Workflows
- ✅ `.github/workflows/deploy.yml` - Auto-deploy to staging
- ✅ `.github/workflows/deploy-production.yml` - Manual production deploy

### Documentation
- ✅ `docs/RAILWAY_STAGING_SETUP.md` - Complete setup guide
- ✅ `docs/STAGING_API_SECURITY.md` - Security implementation
- ✅ `docs/ENABLE_STAGING_SECURITY.md` - Quick security setup
- ✅ `docs/DEPLOYMENT_WORKFLOW.md` - Deployment process
- ✅ `docs/TESTING_ENVIRONMENT.md` - Detailed environment guide
- ✅ `docs/TESTING_QUICKSTART.md` - Quick 15-min setup
- ✅ `DEPLOYMENT.md` - Quick reference

---

## 🔄 Your New Workflow

### Daily Development Flow

```bash
# 1. Develop locally
git checkout -b feature/my-feature
# ... make changes ...

# 2. Commit and push
git commit -m "Add new feature"
git push origin feature/my-feature

# 3. Create PR and merge to main
gh pr merge

# 4. Wait for auto-deploy to staging (~3 min)
# Check GitHub Actions for notification

# 5. Test on staging
curl https://staging.domain/health
# Test your changes...

# 6. Deploy to production (when ready)
# Go to GitHub Actions → "Deploy to Production (Manual)"
# Type: deploy-to-production
# Click "Run workflow"
```

---

## 🧪 Test Data

After seeding, you'll have:

### Test Users & API Keys

| Plan | Email | API Key | Credits |
|------|-------|---------|---------|
| Free | test-free@gatewayz.ai | `gw_test_free_key_12345` | 100 |
| Pro | test-pro@gatewayz.ai | `gw_test_pro_key_12345` | 10,000 |
| Enterprise | test-enterprise@gatewayz.ai | `gw_test_enterprise_key_12345` | 100,000 |

### Test Coupons

| Code | Discount |
|------|----------|
| TEST10 | 10% off |
| TEST50 | 50% off |
| TESTFREE | 100% off |

### Stripe Test Cards

| Card Number | Result |
|-------------|--------|
| 4242 4242 4242 4242 | Success |
| 4000 0000 0000 0002 | Decline |

---

## 🔒 Security Recommendations

### Recommended Security Stack

**Level 1**: Choose one
- IP Whitelisting (if team has static IPs)
- Custom Auth Token (if team is remote)

**Level 2**: Add these
- Stricter rate limiting
- CORS restrictions

**Level 3**: Monitor
- Check logs for unauthorized access
- Set up Sentry alerts

### Implementation

See [docs/ENABLE_STAGING_SECURITY.md](./docs/ENABLE_STAGING_SECURITY.md) for:
- 5-minute setup guide
- Token generation
- Railway configuration
- Middleware integration
- Testing steps

---

## 📊 Environment Comparison

| Feature | Staging | Production |
|---------|---------|------------|
| **Database** | Separate Supabase | Production Supabase |
| **API Keys** | Test keys (gw_test_*) | Real customer keys |
| **Stripe** | Test mode | Live mode |
| **Redis** | Separate/prefixed | Production |
| **Domain** | staging.gatewayz.ai | api.gatewayz.ai |
| **Deploy** | Automatic on push | Manual via Actions |
| **Rate Limits** | Stricter | Normal |
| **Security** | Token/IP whitelist | Production auth |

---

## ⚡ Quick Commands

```bash
# Switch environments
railway environment switch staging
railway environment switch production

# View logs
railway logs --environment staging

# View variables
railway variables

# Deploy manually
railway up

# Re-seed test data
APP_ENV=staging python scripts/database/seed_test_data.py

# Test staging
curl https://staging-domain/health
curl -H "X-Staging-Access-Token: token" https://staging-domain/v1/models

# Check security status
railway logs | grep "Staging security"
```

---

## 🎯 Next Steps

1. [ ] Follow [RAILWAY_STAGING_SETUP.md](./docs/RAILWAY_STAGING_SETUP.md) to set up staging
2. [ ] Enable security with [ENABLE_STAGING_SECURITY.md](./docs/ENABLE_STAGING_SECURITY.md)
3. [ ] Test the deployment workflow
4. [ ] Share staging URL and access token with team
5. [ ] Set up monitoring alerts
6. [ ] Schedule weekly test data refresh

---

## ❓ Common Questions

**Q: Do I need a separate Supabase project?**
A: Yes, staging should have its own database with test data.

**Q: Can I use production API keys in staging?**
A: Yes, but it's better to use separate test keys if providers offer them.

**Q: How do I deploy to production?**
A: Go to GitHub Actions → "Deploy to Production (Manual)" → Type "deploy-to-production" → Run

**Q: How do I secure staging?**
A: Set `STAGING_ACCESS_TOKEN` in Railway and add the middleware. See [ENABLE_STAGING_SECURITY.md](./docs/ENABLE_STAGING_SECURITY.md)

**Q: What if staging deployment fails?**
A: Check `railway logs --environment staging` and verify environment variables are set correctly.

**Q: How often should I refresh test data?**
A: Weekly or after major database changes. Run: `APP_ENV=staging python scripts/database/seed_test_data.py`

---

## 📞 Need Help?

1. **Check documentation** (links above)
2. **Check logs**: `railway logs --environment staging`
3. **Check health**: `curl https://your-domain/health`
4. **Railway docs**: https://docs.railway.app/
5. **Supabase docs**: https://supabase.com/docs

---

## ✅ Checklist

### Setup
- [ ] Staging Supabase database created
- [ ] Railway staging environment configured
- [ ] Environment variables set
- [ ] Database migrations applied
- [ ] Test data seeded
- [ ] Staging deployed and tested
- [ ] Security enabled
- [ ] GitHub secrets configured

### Testing
- [ ] Staging health check works
- [ ] Test API keys work
- [ ] Auto-deploy to staging works
- [ ] Manual production deploy works
- [ ] Security blocks unauthorized access

### Team
- [ ] Team has staging URL
- [ ] Team has access tokens (if using)
- [ ] Team knows the workflow
- [ ] Documentation shared

---

**Congratulations!** 🎉

Your staging environment is ready. Now you can test confidently before deploying to production!

**Remember**:
- Always test in staging first
- Never push directly to production
- Keep test data fresh
- Rotate security credentials regularly

**Happy testing!** 🚀
