# Staging Deployment Guide

How to configure Railway to automatically deploy to staging when you push to main branch, with token-based security.

---

## 🎯 What You'll Achieve

```
Push to main branch
    ↓
Railway detects push
    ↓
Deploys to "staging" environment
    ↓
Staging API requires STAGING_ACCESS_TOKEN
    ↓
Only authorized users can access
```

---

## 📋 Setup Checklist

Complete these steps in order:

### ✅ Step 1: Configure Railway Staging Environment (5 min)

#### Via Railway Dashboard (Recommended)

1. **Go to Railway**
   - Open https://railway.app/
   - Select your project: `gatewayz-backend`

2. **Create/Verify Staging Environment**
   - Click **"Environments"** dropdown (top right)
   - If "staging" doesn't exist, click **"+ New Environment"**
   - Name it: `staging`
   - Click **"Create"**

3. **Configure Branch Trigger**
   - Click on your service (backend)
   - Go to **"Settings"** tab
   - Scroll to **"Deploy Triggers"** section
   - Under **"Branch Deployments"**, configure:
     ```
     Environment: staging
     Branch: main
     ☑️ Enable automatic deployments
     ```
   - Click **"Save"**

4. **Verify Configuration**
   - You should see: "Deploys from: main branch → staging environment"

#### Via Railway CLI

```bash
# Login and link project
railway login
railway link

# Create staging environment (if doesn't exist)
railway environment create staging

# Switch to staging
railway environment staging

# Set branch trigger
railway up --environment staging
```

---

### ✅ Step 2: Set Environment Variables in Staging (10 min)

#### Set the Staging Access Token

```bash
# Switch to staging environment
railway environment staging

# Set the access token (from STAGING_SECURITY_SETUP.md)
railway variables set STAGING_ACCESS_TOKEN="staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw"

# Set environment
railway variables set APP_ENV=staging

# Verify it's set
railway variables | grep STAGING_ACCESS_TOKEN
```

#### Set All Required Variables

```bash
# Database (Staging Supabase)
railway variables set SUPABASE_URL="https://your-staging-project.supabase.co"
railway variables set SUPABASE_KEY="your-staging-anon-key"

# Provider API Keys (same as production or test keys)
railway variables set OPENROUTER_API_KEY="sk-or-v1-..."
railway variables set PORTKEY_API_KEY="..."
railway variables set FEATHERLESS_API_KEY="..."
railway variables set CHUTES_API_KEY="..."
railway variables set DEEPINFRA_API_KEY="..."
railway variables set FIREWORKS_API_KEY="..."
railway variables set TOGETHER_API_KEY="..."
railway variables set HUGGINGFACE_API_KEY="..."
railway variables set XAI_API_KEY="..."
railway variables set AIMO_API_KEY="..."
railway variables set NEAR_API_KEY="..."

# Stripe (Test Mode)
railway variables set STRIPE_SECRET_KEY="sk_test_..."
railway variables set STRIPE_WEBHOOK_SECRET="whsec_..."
railway variables set STRIPE_PUBLISHABLE_KEY="pk_test_..."

# Email
railway variables set RESEND_API_KEY="re_..."

# Analytics
railway variables set STATSIG_SDK_KEY="secret-..."
railway variables set POSTHOG_KEY="phc_..."

# Sentry
railway variables set SENTRY_DSN="https://...@sentry.io/..."
railway variables set SENTRY_ENVIRONMENT=staging

# Security
railway variables set ENCRYPTION_KEY="your-staging-encryption-key"
railway variables set JWT_SECRET="your-staging-jwt-secret"
railway variables set ADMIN_API_KEY="your-admin-api-key"

# Redis (if you have a separate Redis for staging)
railway variables set REDIS_URL="redis://your-redis:6379"

# Admin
railway variables set ADMIN_EMAIL="admin@gatewayz.ai"
```

**Quick Copy Method:**

If you want to copy from production:

```bash
# Export from production
railway environment production
railway variables > production-vars.txt

# Switch to staging
railway environment staging

# Manually set variables, but change:
# - APP_ENV=staging
# - STAGING_ACCESS_TOKEN=... (new token)
# - SUPABASE_URL/KEY (staging database)
# - STRIPE keys (test mode)
# - SENTRY_ENVIRONMENT=staging
```

---

### ✅ Step 3: Deploy Code Changes (2 min)

The staging security middleware is already in your code (`src/main.py`), so you just need to push it:

```bash
# Make sure you're on main branch
git checkout main

# Check current status
git status

# If you have uncommitted changes (the middleware we added):
git add src/main.py .gitignore
git commit -m "Enable staging security with token authentication"

# Push to main
git push origin main
```

---

### ✅ Step 4: Verify Deployment (3 min)

#### Watch Deployment in Railway

```bash
# Via CLI
railway logs --environment staging

# Or via Dashboard
# Railway → Select staging environment → View logs
```

You should see:
```
Building...
Deploying...
🔒 Staging security middleware enabled
Staging security enabled: Access Token
✅ Application startup complete!
```

#### Get Staging URL

```bash
# Via CLI
railway environment staging
railway domain

# Example output: gatewayz-backend-staging.up.railway.app
```

Or via Dashboard:
- Railway → staging environment → Service → Settings → Domains

---

### ✅ Step 5: Test Security (2 min)

#### Test 1: Access WITHOUT Token (Should Fail)

```bash
# Replace with your actual staging domain
STAGING_URL="https://gatewayz-backend-staging.up.railway.app"

# Try to access API without token
curl -i $STAGING_URL/v1/models

# Expected response: 403 Forbidden
# {
#   "error": "Staging Access Denied",
#   "message": "Access to this staging/test environment is restricted: Missing X-Staging-Access-Token header"
# }
```

✅ **If you get 403 - Security is working!**

#### Test 2: Health Check (Should Work)

```bash
# Health check should bypass security
curl $STAGING_URL/health

# Expected response: 200 OK
# {"status": "healthy", "environment": "staging", ...}
```

✅ **If you get 200 - Health check is accessible!**

#### Test 3: Access WITH Token (Should Work)

```bash
# Use your staging access token
curl -H "X-Staging-Access-Token: staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw" \
     $STAGING_URL/v1/models

# Expected response: 200 OK
# [{"id": "gpt-4", ...}, ...]
```

✅ **If you get 200 - Token authentication is working!**

#### Test 4: Full API Request

```bash
# Test chat completion with staging token + API key
curl -X POST $STAGING_URL/v1/chat/completions \
  -H "X-Staging-Access-Token: staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw" \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello from staging!"}],
    "max_tokens": 10
  }'

# Expected response: 200 OK with chat completion
```

✅ **If you get a response - Everything is working!**

---

## 🔄 Daily Workflow

### Developer Workflow

```bash
# 1. Create feature branch
git checkout -b feature/new-feature

# 2. Make changes
# ... code ...

# 3. Test locally
python src/main.py

# 4. Commit changes
git add .
git commit -m "Add new feature"

# 5. Push to feature branch (doesn't deploy to staging)
git push origin feature/new-feature

# 6. Create PR
gh pr create --title "Add new feature"

# 7. After PR approval, merge to main
gh pr merge

# 8. Automatic deployment to staging happens!
# Wait ~3 minutes

# 9. Test on staging
curl -H "X-Staging-Access-Token: your-token" \
     https://staging-url/health

# 10. If staging looks good, manually deploy to production
# (via Railway dashboard or separate workflow)
```

---

## 🎯 Deployment Confirmation

After pushing to main, you should see:

### In Railway Dashboard

```
Environments → staging
├─ Status: Deploying... (then) Active
├─ Last Deploy: Just now (from main branch)
├─ Build Logs: Success
└─ Service Logs: "🔒 Staging security middleware enabled"
```

### In Terminal

```bash
# Check deployment status
railway environment staging
railway status

# Should show:
# Status: Active
# Last Deploy: 2 minutes ago
# Branch: main
# Environment: staging
```

### Via API

```bash
# Test that security is active
curl https://your-staging-url/v1/models
# Should return: 403 Forbidden (security working!)

curl -H "X-Staging-Access-Token: your-token" \
     https://your-staging-url/v1/models
# Should return: 200 OK with models list
```

---

## 🔧 Troubleshooting

### Problem: Push to main doesn't deploy to staging

**Check 1:** Verify branch trigger
```bash
# Railway Dashboard → Service → Settings → Deploy Triggers
# Should show: "main" branch → "staging" environment
```

**Check 2:** Check Railway webhook
```bash
# GitHub → Settings → Webhooks
# Should have Railway webhook active
```

**Check 3:** Manual deploy
```bash
railway environment staging
railway up
```

---

### Problem: Deployment succeeds but security not working

**Check 1:** Verify STAGING_ACCESS_TOKEN is set
```bash
railway environment staging
railway variables | grep STAGING_ACCESS_TOKEN

# Should output: STAGING_ACCESS_TOKEN=staging_...
```

**Check 2:** Verify APP_ENV is staging
```bash
railway variables | grep APP_ENV

# Should output: APP_ENV=staging
```

**Check 3:** Check logs for middleware
```bash
railway logs --environment staging | grep -i "staging security"

# Should see: "🔒 Staging security middleware enabled"
# Should see: "Staging security enabled: Access Token"
```

**Check 4:** Verify code was deployed
```bash
railway logs --environment staging | grep -i "commit"

# Should show your latest commit hash
```

---

### Problem: Security is too strict (can't test easily)

**Temporary Solution:** Disable security for testing

```bash
# Remove token temporarily
railway environment staging
railway variables set STAGING_ACCESS_TOKEN=""

# Re-enable after testing
railway variables set STAGING_ACCESS_TOKEN="staging_DrN4Pw87..."
```

---

## 📊 Current Setup Summary

| Component | Configuration | Status |
|-----------|---------------|--------|
| **Environment** | Railway `staging` | ✅ Configured |
| **Branch Trigger** | `main` branch | ✅ Auto-deploy |
| **Security** | Token-based auth | ✅ Enabled |
| **Token** | `STAGING_ACCESS_TOKEN` | ✅ Set in Railway |
| **Code** | Middleware in `src/main.py` | ✅ Deployed |
| **Database** | Staging Supabase | ✅ Connected |

---

## 🎉 Success Checklist

After following all steps, verify:

- [ ] Railway staging environment exists
- [ ] Branch trigger: `main` → `staging` configured
- [ ] `STAGING_ACCESS_TOKEN` set in Railway
- [ ] `APP_ENV=staging` set in Railway
- [ ] All environment variables copied to staging
- [ ] Code with middleware pushed to main
- [ ] Deployment successful (check logs)
- [ ] Security working (403 without token)
- [ ] Health check accessible (200)
- [ ] API accessible with token (200)

---

## 📖 Related Documentation

- **Security Setup:** `STAGING_SECURITY_SETUP.md` - Your token and credentials
- **Test Script:** `scripts/test-staging-security.sh` - Automated testing
- **Multi-Developer:** `docs/deployment/MULTI_DEVELOPER_STAGING.md` - Multiple environments
- **Platform Comparison:** `docs/deployment/VERCEL_VS_RAILWAY_ANALYSIS.md` - Why Railway

---

## 💡 Pro Tips

1. **Bookmark your staging URL**
   ```bash
   # Save to environment variable
   export STAGING_URL="https://gatewayz-backend-staging.up.railway.app"
   ```

2. **Create an alias for testing**
   ```bash
   # Add to ~/.bashrc or ~/.zshrc
   alias test-staging='curl -H "X-Staging-Access-Token: your-token" $STAGING_URL/health'
   ```

3. **Use Postman/Insomnia**
   - Create a "Staging" environment
   - Set base URL: `{{staging_url}}`
   - Set header: `X-Staging-Access-Token: {{staging_token}}`

4. **Monitor staging**
   ```bash
   # Watch logs in real-time
   railway logs --environment staging --follow
   ```

---

## 🎯 You're All Set!

Now when you:
1. Push to `main` branch
2. Railway automatically deploys to `staging` environment
3. Staging requires `X-Staging-Access-Token` header to access
4. Only authorized team members can use the API

**Next step:** Test it by pushing a small change to main! 🚀
