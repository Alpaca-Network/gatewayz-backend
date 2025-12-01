# Railway Staging Environment Setup - Step by Step

This is a complete, step-by-step guide to set up a staging (test) environment in Railway for your Gatewayz backend.

**Time required:** ~30-45 minutes
**Prerequisites:** Railway account, Railway CLI installed, Supabase account

---

## Table of Contents

1. [Part 1: Create Staging Supabase Database](#part-1-create-staging-supabase-database)
2. [Part 2: Create Staging Environment in Railway](#part-2-create-staging-environment-in-railway)
3. [Part 3: Configure Environment Variables](#part-3-configure-environment-variables)
4. [Part 4: Run Database Migrations](#part-4-run-database-migrations)
5. [Part 5: Seed Test Data](#part-5-seed-test-data)
6. [Part 6: Deploy to Staging](#part-6-deploy-to-staging)
7. [Part 7: Verify Staging Works](#part-7-verify-staging-works)
8. [Part 8: Configure GitHub Secrets](#part-8-configure-github-secrets)
9. [Part 9: Secure Staging API](#part-9-secure-staging-api)
10. [Troubleshooting](#troubleshooting)

---

## Part 1: Create Staging Supabase Database

### Step 1.1: Create New Supabase Project

1. Go to https://app.supabase.com/
2. Click the **"New project"** button
3. Fill in the details:
   - **Name**: `gatewayz-staging` (or `gatewayz-test`)
   - **Database Password**: Generate a strong password (save it somewhere safe!)
   - **Region**: Choose the **same region** as your production database
   - **Pricing Plan**: Free tier is fine for staging
4. Click **"Create new project"**
5. Wait ~2 minutes for the project to be provisioned

### Step 1.2: Get Staging Database Credentials

Once the project is ready:

1. In your staging project, go to **Settings** (gear icon in sidebar)
2. Click **"API"** in the settings menu
3. **Copy and save these values** (you'll need them later):

```
Project URL: https://xxxxxxxxxxxxx.supabase.co
anon/public key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
service_role key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9... (keep secret!)
Project ID: xxxxxxxxxxxxx
```

4. Also note the **Project Reference ID** (Settings → General)

✅ **Checkpoint**: You should have:
- Staging Supabase URL
- Anon key
- Service role key (for migrations)
- Project reference ID

---

## Part 2: Create Staging Environment in Railway

### Step 2.1: Install Railway CLI

If you haven't already:

```bash
# Install Railway CLI
npm install -g @railway/cli

# Verify installation
railway --version
```

### Step 2.2: Login to Railway

```bash
railway login
```

This will open your browser. Log in and authorize the CLI.

### Step 2.3: Link to Your Project

```bash
# Navigate to your project directory
cd /path/to/gatewayz-backend

# Link to your Railway project
railway link

# You'll see a list of projects - select your gatewayz project
# Use arrow keys to select, press Enter
```

✅ **Checkpoint**: Run `railway status` - you should see your project name

### Step 2.4: Create Staging Environment

**Option A: Via Railway Dashboard** (Recommended for first time)

1. Go to https://railway.app/
2. Click on your **Gatewayz** project
3. Click on the **"Environments"** dropdown (top right, next to "Settings")
4. Click **"+ New Environment"**
5. Enter name: **`staging`**
6. Click **"Create Environment"**

**Option B: Via Railway CLI**

```bash
railway environment create staging
```

### Step 2.5: Switch to Staging Environment

```bash
railway environment switch staging

# Verify you're in staging
railway environment
# Should show: staging (with a ✓ or * next to it)
```

✅ **Checkpoint**: Run `railway environment` - should show you're in staging

---

## Part 3: Configure Environment Variables

Now you'll set all the environment variables for staging. You can do this via Railway Dashboard or CLI.

### Step 3.1: Generate Security Keys

First, generate new security keys for staging (different from production):

```bash
# Generate encryption key (32+ characters)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Copy the output

# Generate JWT secret
python3 -c "import secrets; print(secrets.token_hex(32))"
# Copy the output
```

Save these values - you'll use them in the next step.

### Step 3.2: Set Variables via Railway Dashboard

1. Go to https://railway.app/
2. Click your project
3. Make sure **"staging"** environment is selected (top right dropdown)
4. Click on your **service** (usually named "gateway-api" or similar)
5. Click the **"Variables"** tab
6. Click **"+ New Variable"**

Now add each variable below by clicking "+ New Variable" and entering the name and value:

#### Core Configuration

```bash
APP_ENV = staging
```

#### Supabase (Staging Database)

```bash
SUPABASE_URL = https://your-staging-project.supabase.co
SUPABASE_KEY = your_staging_anon_key_here
```

#### Security Keys (Use the ones you generated in Step 3.1)

```bash
ENCRYPTION_KEY = your_generated_encryption_key_here
JWT_SECRET = your_generated_jwt_secret_here
```

#### Stripe (Test Mode Keys)

Get these from https://dashboard.stripe.com/test/apikeys

```bash
STRIPE_SECRET_KEY = sk_test_...
STRIPE_WEBHOOK_SECRET = whsec_test_...
STRIPE_PUBLISHABLE_KEY = pk_test_...
```

#### Redis (Optional - can share with production)

If you want a separate Redis for staging:

```bash
REDIS_URL = redis://your-staging-redis:6379
```

Or, to share Redis with production (keys will be prefixed with "staging:"):
```bash
# Copy the same REDIS_URL from production
REDIS_URL = redis://...
```

#### Email Service

```bash
RESEND_API_KEY = re_...  # Can use same as production
ADMIN_EMAIL = your-email@example.com
```

#### Monitoring

```bash
# Sentry (same DSN, different environment)
SENTRY_DSN = https://...@sentry.io/...
SENTRY_ENVIRONMENT = staging
SENTRY_ENABLED = true

# Statsig (create separate staging project if possible)
STATSIG_SDK_KEY = secret-...

# PostHog (create separate staging project if possible)
POSTHOG_KEY = phc_...
```

#### Provider API Keys

**Option 1**: Use the same keys as production
- Go to production environment
- Copy all provider keys
- Paste them in staging

**Option 2**: Use separate test keys (if providers offer them)

```bash
OPENROUTER_API_KEY = sk-or-...
PORTKEY_API_KEY = ...
ANTHROPIC_API_KEY = sk-ant-...
DEEPINFRA_API_KEY = ...
FIREWORKS_API_KEY = ...
TOGETHER_API_KEY = ...
# ... (copy all other provider keys from production)
```

### Step 3.3: Set Variables via Railway CLI (Alternative)

Or use the CLI to set variables (faster if you have many):

```bash
# Make sure you're in staging environment
railway environment switch staging

# Set variables
railway variables set APP_ENV=staging
railway variables set SUPABASE_URL="https://your-staging.supabase.co"
railway variables set SUPABASE_KEY="your-staging-anon-key"
railway variables set ENCRYPTION_KEY="your-encryption-key"
railway variables set JWT_SECRET="your-jwt-secret"
railway variables set STRIPE_SECRET_KEY="sk_test_..."
# ... continue for all variables
```

Or use the automated script:

```bash
./scripts/setup-staging-environment.sh
```

This will interactively prompt you for all required variables.

✅ **Checkpoint**: Run `railway variables` - you should see all your staging variables

---

## Part 4: Run Database Migrations

Now apply all database migrations to your staging Supabase database.

### Step 4.1: Install Supabase CLI

If you haven't already:

```bash
# macOS
brew install supabase/tap/supabase

# Windows (PowerShell)
scoop bucket add supabase https://github.com/supabase/scoop-bucket.git
scoop install supabase

# Linux
brew install supabase/tap/supabase
```

### Step 4.2: Login to Supabase

```bash
supabase login
```

This will open your browser. Log in and authorize the CLI.

### Step 4.3: Link to Staging Project

```bash
# Navigate to supabase directory
cd supabase

# Link to your staging project
supabase link --project-ref YOUR_STAGING_PROJECT_REF

# Replace YOUR_STAGING_PROJECT_REF with the Project ID from Part 1
# Example: supabase link --project-ref abcdefghijklmno
```

You'll be prompted to enter your database password (the one you set when creating the project).

### Step 4.4: Apply Migrations

```bash
# Push all migrations to staging database
supabase db push

# This will:
# - Show you all migrations that will be applied
# - Ask for confirmation
# - Apply migrations in order
```

You should see output like:
```
Applying migration 20231001000000_initial_schema.sql...
Applying migration 20231002000000_add_users.sql...
Applying migration 20231003000000_add_api_keys.sql...
...
✓ All migrations applied successfully
```

### Step 4.5: Verify Tables Created

```bash
# List all tables in staging database
supabase db remote exec "SELECT tablename FROM pg_tables WHERE schemaname = 'public';"
```

You should see:
- users
- api_keys
- chat_history
- plans
- payments
- credit_transactions
- coupons
- referrals
- ... (all your tables)

✅ **Checkpoint**: All tables should exist in staging database

---

## Part 5: Seed Test Data

Now populate the staging database with test data.

### Step 5.1: Set Environment Variables

```bash
# Set staging credentials
export APP_ENV=staging
export SUPABASE_URL="https://your-staging.supabase.co"
export SUPABASE_KEY="your-staging-service-role-key"  # Use service role key!

# Verify they're set
echo $APP_ENV
echo $SUPABASE_URL
```

### Step 5.2: Run Seed Script

```bash
# Navigate back to project root
cd ..

# Run the seed script
python scripts/database/seed_test_data.py
```

You should see output like:
```
============================================================
🌱 SEEDING TEST DATA FOR STAGING ENVIRONMENT
============================================================

Environment: staging
Supabase URL: https://xxxxx.supabase.co

🌱 Seeding test users...
  ✅ Created user: test-free@gatewayz.ai (Plan: free, Credits: 100.0)
  ✅ Created user: test-starter@gatewayz.ai (Plan: starter, Credits: 1000.0)
  ✅ Created user: test-pro@gatewayz.ai (Plan: pro, Credits: 10000.0)
  ✅ Created user: test-enterprise@gatewayz.ai (Plan: enterprise, Credits: 100000.0)

🔑 Seeding test API keys...
  ✅ Created API key for test-free@gatewayz.ai
     Raw key: gw_test_free_key_12345
  ✅ Created API key for test-pro@gatewayz.ai
     Raw key: gw_test_pro_key_12345
  ...

💳 Seeding test plans...
  ✅ Created plan: Free ($0.0/mo)
  ✅ Created plan: Pro ($49.99/mo)
  ...

============================================================
✅ TEST DATA SEEDING COMPLETE!
============================================================

Test API Keys:
  Free:       gw_test_free_key_12345
  Starter:    gw_test_starter_key_12345
  Pro:        gw_test_pro_key_12345
  Enterprise: gw_test_enterprise_key_12345
```

**Save these test API keys!** You'll use them to test the staging API.

✅ **Checkpoint**: Test data should be created in staging database

---

## Part 6: Deploy to Staging

Now deploy your code to the staging environment.

### Step 6.1: Switch to Staging (if not already)

```bash
railway environment switch staging
```

### Step 6.2: Deploy

```bash
# Deploy current code to staging
railway up
```

This will:
1. Upload your code to Railway
2. Build the Docker container
3. Deploy to staging environment
4. Start the service

You'll see output like:
```
Building...
Deploying...
✓ Deployment successful
```

### Step 6.3: Monitor Deployment

```bash
# Watch the logs
railway logs --follow
```

Look for:
- ✅ "Application startup complete"
- ✅ "Uvicorn running on..."
- ❌ Any errors (fix them before continuing)

Press `Ctrl+C` to stop watching logs.

### Step 6.4: Get Staging Domain

```bash
# Get the staging URL
railway domain
```

You'll see something like:
```
gateway-api-staging.up.railway.app
```

**Save this domain!** This is your staging API URL.

Or, set a custom domain:

1. Go to Railway Dashboard
2. Click your service
3. Go to "Settings" tab
4. Under "Domains", click "+ Generate Domain" or "+ Custom Domain"
5. For custom domain, enter: `staging.gatewayz.ai` (or your subdomain)

✅ **Checkpoint**: You should be able to access `https://your-staging-domain.railway.app`

---

## Part 7: Verify Staging Works

Test that your staging environment is working correctly.

### Step 7.1: Health Check

```bash
# Test health endpoint
curl https://your-staging-domain.railway.app/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "2.0.3",
  "environment": "staging"
}
```

### Step 7.2: Test Model Catalog

```bash
curl https://your-staging-domain.railway.app/v1/models
```

Should return a list of available models.

### Step 7.3: Test Chat Endpoint (with test API key)

```bash
curl -X POST https://your-staging-domain.railway.app/v1/chat/completions \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello from staging!"}],
    "max_tokens": 50
  }'
```

Expected: Should return a chat completion response.

### Step 7.4: Test User Endpoint

```bash
curl https://your-staging-domain.railway.app/v1/users/me \
  -H "Authorization: Bearer gw_test_pro_key_12345"
```

Should return the test user's information.

✅ **Checkpoint**: All endpoints should work correctly

---

## Part 8: Configure GitHub Secrets

Add the staging domain to GitHub secrets so the CI/CD workflow can use it.

### Step 8.1: Get Staging Domain

You already got this in Part 6. It's something like:
- `gateway-api-staging.up.railway.app`
- Or your custom domain: `staging.gatewayz.ai`

### Step 8.2: Add GitHub Secret

1. Go to your GitHub repository
2. Click **"Settings"** (top menu)
3. Click **"Secrets and variables"** → **"Actions"** (left sidebar)
4. Click **"New repository secret"**
5. Add:
   - **Name**: `RAILWAY_STAGING_DOMAIN`
   - **Secret**: `your-staging-domain.railway.app` (without https://)
6. Click **"Add secret"**

### Step 8.3: Verify GitHub Workflow

Check that the deploy workflow uses the new secret:

```bash
# View the deploy workflow
cat .github/workflows/deploy.yml | grep RAILWAY_STAGING_DOMAIN
```

Should show the secret being used.

✅ **Checkpoint**: Secret is added and workflow references it

---

## Part 9: Secure Staging API

Now implement security measures to prevent unauthorized access to your staging API.

See the detailed [Staging API Security Guide](./STAGING_API_SECURITY.md) for implementation.

**Quick security options:**

### Option 1: IP Whitelist (Railway Feature)

1. Go to Railway Dashboard → Your Service → Settings
2. Under "Networking", add allowed IPs:
   - Your office IP
   - Your home IP
   - Team members' IPs

### Option 2: Basic Authentication Header

Add a staging access token that must be included in all requests:

```bash
# Set in Railway staging environment
railway variables set STAGING_ACCESS_TOKEN="your-secret-token-here"
```

We'll implement the middleware for this in Part 9 detailed guide.

### Option 3: Rate Limiting

Staging already has stricter rate limits by default (configured in code).

---

## Testing the Complete Workflow

Now test the entire workflow end-to-end:

### Test 1: Push to Main → Auto-deploy to Staging

```bash
# Make a small change
echo "# Test staging deployment" >> README.md
git add README.md
git commit -m "Test: verify staging auto-deploy"
git push origin main
```

**Expected**:
1. GitHub Actions starts CI workflow
2. CI passes
3. Deploy workflow starts automatically
4. Code deploys to staging
5. You get a notification with staging URL

### Test 2: Manual Production Deploy

1. Go to GitHub → **Actions** tab
2. Click **"Deploy to Production (Manual)"**
3. Click **"Run workflow"**
4. Type: `deploy-to-production`
5. Click **"Run workflow"**

**Expected**:
- Workflow asks for confirmation
- Deploys to production
- Verifies health
- Sends notification

✅ **Checkpoint**: Both workflows work correctly

---

## Summary Checklist

After completing all steps, verify:

- [x] Staging Supabase database created
- [x] Staging Railway environment created
- [x] All environment variables configured
- [x] Database migrations applied
- [x] Test data seeded
- [x] Code deployed to staging
- [x] Health checks pass
- [x] Test API keys work
- [x] GitHub secret configured
- [x] Auto-deploy to staging works
- [x] Manual production deploy works
- [x] Staging API is secured

---

## Quick Reference

### Staging Environment Details

```bash
Environment: staging
Railway CLI: railway environment switch staging
Supabase: Your staging project
Domain: your-staging-domain.railway.app
Test API Keys:
  - Free: gw_test_free_key_12345
  - Pro: gw_test_pro_key_12345
  - Enterprise: gw_test_enterprise_key_12345
```

### Common Commands

```bash
# Switch to staging
railway environment switch staging

# View staging logs
railway logs --environment staging --follow

# View staging variables
railway variables

# Deploy to staging
railway up

# Re-seed test data
APP_ENV=staging python scripts/database/seed_test_data.py

# Check staging health
curl https://your-staging-domain/health
```

---

## Troubleshooting

### Issue: Railway deployment fails

**Solution:**
```bash
# Check logs
railway logs --environment staging

# Common issues:
# 1. Missing environment variables
railway variables  # Check all required vars are set

# 2. Build errors
# Check requirements.txt is up to date

# 3. Port configuration
# Railway expects app to run on $PORT
```

### Issue: Database migrations fail

**Solution:**
```bash
# Check you're linked to correct project
supabase projects list

# Re-link if needed
supabase link --project-ref YOUR_STAGING_REF

# Check migration files
ls supabase/migrations/

# Try applying migrations one by one
supabase db push
```

### Issue: Test API keys don't work

**Solution:**
```bash
# Re-run seed script
export APP_ENV=staging
export SUPABASE_URL="https://your-staging.supabase.co"
export SUPABASE_KEY="your-staging-service-key"
python scripts/database/seed_test_data.py

# Check in Supabase dashboard
# Go to Table Editor → api_keys
# Verify test keys exist
```

### Issue: Can't access staging domain

**Solution:**
```bash
# Check domain is correct
railway domain

# Check service is running
railway status

# Check logs for errors
railway logs --environment staging

# Verify health endpoint works
curl https://your-domain/health
```

### Issue: Environment variables not set

**Solution:**
```bash
# List all variables
railway variables

# Re-run setup script
./scripts/setup-staging-environment.sh

# Or set manually
railway variables set APP_ENV=staging
railway variables set SUPABASE_URL="..."
# ... etc
```

---

## Next Steps

1. **Read the security guide**: [STAGING_API_SECURITY.md](./STAGING_API_SECURITY.md)
2. **Set up monitoring**: Configure alerts for staging
3. **Document your staging URL**: Share with your team
4. **Test regularly**: Deploy to staging before production
5. **Refresh test data**: Re-seed weekly or as needed

---

## Need Help?

- **Railway Docs**: https://docs.railway.app/
- **Supabase Docs**: https://supabase.com/docs
- **Railway Support**: https://railway.app/help
- **Check logs**: `railway logs --environment staging`
- **Check health**: `curl https://your-staging-domain/health`

---

**Congratulations!** 🎉 Your staging environment is now set up and ready to use!

**Remember**: Always test in staging before deploying to production!
