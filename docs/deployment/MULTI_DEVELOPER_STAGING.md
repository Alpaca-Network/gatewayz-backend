# Multi-Developer Staging Environments

How to set up isolated staging environments for multiple developers working on different features.

## The Problem

You have multiple developers working on different features:
- **Dev 1** working on new payment system
- **Dev 2** working on chat improvements
- **Dev 3** working on admin dashboard

Each needs their own staging environment to test without interfering with others.

---

## 🎯 Solution Options

| Approach | Best For | Cost | Complexity | Isolation |
|----------|----------|------|------------|-----------|
| **1. Railway Environments** | Small teams (2-5 devs) | 💰 Medium | ⭐⭐ Easy | ⭐⭐⭐⭐⭐ Full |
| **2. PR Deployments** | Any team size | 💰💰 Higher | ⭐⭐⭐ Medium | ⭐⭐⭐⭐⭐ Full |
| **3. Branch Deployments** | Medium teams (3-8 devs) | 💰💰 Higher | ⭐⭐⭐⭐ Complex | ⭐⭐⭐⭐ Good |
| **4. Shared Staging + Feature Flags** | Large teams (8+) | 💰 Low | ⭐⭐⭐ Medium | ⭐⭐ Limited |

Let me explain each approach:

---

## 🔵 Option 1: Multiple Railway Environments (Recommended for Small Teams)

Create separate Railway environments for each developer or feature.

### Architecture

```
Railway Project: gatewayz-backend
├── staging-main       (shared staging - latest main branch)
├── staging-dev1       (Alice's environment)
├── staging-dev2       (Bob's environment)
└── staging-dev3       (Charlie's environment)
```

Each environment has:
- ✅ Its own database (separate Supabase project)
- ✅ Its own Railway deployment
- ✅ Its own domain/URL
- ✅ Its own environment variables

### Setup Instructions

#### Step 1: Create Multiple Supabase Projects

For each developer, create a Supabase project:

```
1. Go to https://app.supabase.com/
2. Create projects:
   - gatewayz-staging-main
   - gatewayz-staging-dev1-alice
   - gatewayz-staging-dev2-bob
   - gatewayz-staging-dev3-charlie
```

#### Step 2: Create Railway Environments

**Via Railway Dashboard:**

1. Go to your Railway project
2. Click **"Environments"** (top right)
3. Click **"+ New Environment"**
4. Create:
   - `staging-main`
   - `staging-dev1`
   - `staging-dev2`
   - `staging-dev3`

**Via Railway CLI:**

```bash
# Create environments
railway environment create staging-dev1
railway environment create staging-dev2
railway environment create staging-dev3
```

#### Step 3: Configure Each Environment

For each environment, set unique variables:

```bash
# For Dev 1 (Alice)
railway environment staging-dev1

railway variables set APP_ENV=staging
railway variables set SUPABASE_URL="https://dev1-alice-project.supabase.co"
railway variables set SUPABASE_KEY="dev1-alice-key"
railway variables set STAGING_ACCESS_TOKEN="staging_dev1_alice_token"

# For Dev 2 (Bob)
railway environment staging-dev2

railway variables set APP_ENV=staging
railway variables set SUPABASE_URL="https://dev2-bob-project.supabase.co"
railway variables set SUPABASE_KEY="dev2-bob-key"
railway variables set STAGING_ACCESS_TOKEN="staging_dev2_bob_token"

# etc.
```

#### Step 4: Set Up Branch Deployments

Configure each environment to deploy from specific branches:

**Via Railway Dashboard:**

1. Go to each environment
2. Settings → Triggers
3. Set branch trigger:
   - `staging-main` → deploys from `main` branch
   - `staging-dev1` → deploys from `feature/alice-payment` branch
   - `staging-dev2` → deploys from `feature/bob-chat` branch
   - `staging-dev3` → deploys from `feature/charlie-admin` branch

**Via Railway CLI:**

```bash
# Link environments to branches
railway environment staging-dev1
railway service update --branch feature/alice-payment

railway environment staging-dev2
railway service update --branch feature/bob-chat
```

#### Step 5: Developer Workflow

**Alice's workflow:**

```bash
# 1. Create feature branch
git checkout -b feature/alice-payment

# 2. Make changes
# ... code ...

# 3. Push to trigger deployment
git push origin feature/alice-payment

# 4. Auto-deploys to staging-dev1
# Wait ~3 minutes

# 5. Test on Alice's environment
curl https://staging-dev1.railway.app/health

# 6. When ready, merge to main
git checkout main
git merge feature/alice-payment
git push origin main
# This deploys to staging-main
```

### Pros & Cons

**Pros:**
- ✅ Full isolation per developer
- ✅ Each dev controls their own environment
- ✅ Separate databases prevent conflicts
- ✅ Easy to understand

**Cons:**
- ❌ Costs multiply (1 Railway environment + 1 Supabase per dev)
- ❌ Need to manage multiple environments
- ❌ Databases can get out of sync

**Best for:** 2-5 developers, feature work taking several days

---

## 🟢 Option 2: PR-Based Deployments (Railway PR Environments)

Automatically create ephemeral environments for each Pull Request.

### Architecture

```
Pull Request #123 → Automatic deployment to:
- URL: https://gatewayz-pr-123.railway.app
- Database: Shared staging OR ephemeral database
- Duration: Exists until PR is merged/closed
```

### Setup Instructions

#### Step 1: Enable PR Deployments in Railway

**Via Railway Dashboard:**

1. Go to your Railway project
2. Settings → Environments
3. Enable **"PR Deployments"**
4. Configure:
   - Base environment: `staging`
   - Auto-delete after merge: ✅ Yes
   - Auto-delete after close: ✅ Yes
   - Timeout: 7 days

**Via Railway CLI:**

```bash
railway environment create pr-base --pr-base
```

#### Step 2: Configure PR Environment Template

Create a template environment with all necessary variables:

```bash
railway environment pr-base

# Set shared variables
railway variables set APP_ENV=staging-pr
railway variables set SUPABASE_URL="shared-staging-db.supabase.co"
railway variables set SUPABASE_KEY="shared-staging-key"
railway variables set STRIPE_SECRET_KEY="sk_test_..."
# ... all other variables
```

#### Step 3: Create GitHub Workflow for PR Deployments

Create `.github/workflows/pr-preview.yml`:

```yaml
name: PR Preview Deployment

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  deploy-preview:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Deploy to Railway PR Environment
        run: |
          # Railway automatically creates PR environment
          echo "Deploying PR #${{ github.event.pull_request.number }}"

      - name: Comment PR with Preview URL
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: `🚀 Preview deployment ready!

              **Preview URL:** https://gatewayz-pr-${{ github.event.pull_request.number }}.railway.app

              **Test it:**
              \`\`\`bash
              curl https://gatewayz-pr-${{ github.event.pull_request.number }}.railway.app/health
              \`\`\`

              This preview will be automatically deleted when the PR is merged or closed.`
            })
```

#### Step 4: Developer Workflow

```bash
# 1. Create feature branch
git checkout -b feature/new-payment

# 2. Make changes and push
git push origin feature/new-payment

# 3. Create PR
gh pr create --title "Add new payment system"

# 4. Railway automatically creates PR environment
# GitHub bot comments with preview URL

# 5. Test on PR environment
curl https://gatewayz-pr-123.railway.app/health

# 6. Merge PR → Environment automatically deleted
gh pr merge
```

### Database Strategy for PR Deployments

**Option A: Shared Staging Database**
- All PR environments use same staging database
- Cheaper, simpler
- Risk of data conflicts
- Good for: Read-only features, UI changes

**Option B: Database per PR**
- Each PR gets ephemeral database
- Full isolation
- More expensive
- Good for: Database migrations, data model changes

**Option C: Database Snapshots**
- Clone staging database for each PR
- Good isolation
- Moderate cost
- Good for: Most use cases

### Pros & Cons

**Pros:**
- ✅ Automatic - no manual environment creation
- ✅ Auto-cleanup when PR closed
- ✅ Perfect for code review
- ✅ Each PR is isolated

**Cons:**
- ❌ Costs can add up with many PRs
- ❌ Complex database management
- ❌ Requires Railway Pro plan

**Best for:** Teams with many PRs, code review process, any team size

---

## 🟡 Option 3: Branch-Based Deployments

Deploy specific branches to specific environments.

### Architecture

```
Branch                    → Environment
main                      → staging-main
feature/payment-v2        → staging-payment
feature/chat-redesign     → staging-chat
feature/admin-dashboard   → staging-admin
```

### Setup Instructions

#### Step 1: Create Named Environments

```bash
railway environment create staging-payment
railway environment create staging-chat
railway environment create staging-admin
```

#### Step 2: Configure GitHub Actions

Create `.github/workflows/deploy-branches.yml`:

```yaml
name: Branch-Based Deployments

on:
  push:
    branches:
      - main
      - 'feature/**'

jobs:
  determine-environment:
    runs-on: ubuntu-latest
    outputs:
      environment: ${{ steps.set-env.outputs.environment }}
    steps:
      - id: set-env
        run: |
          BRANCH=${GITHUB_REF#refs/heads/}

          if [[ "$BRANCH" == "main" ]]; then
            echo "environment=staging-main" >> $GITHUB_OUTPUT
          elif [[ "$BRANCH" == "feature/payment"* ]]; then
            echo "environment=staging-payment" >> $GITHUB_OUTPUT
          elif [[ "$BRANCH" == "feature/chat"* ]]; then
            echo "environment=staging-chat" >> $GITHUB_OUTPUT
          elif [[ "$BRANCH" == "feature/admin"* ]]; then
            echo "environment=staging-admin" >> $GITHUB_OUTPUT
          else
            echo "environment=staging-dev" >> $GITHUB_OUTPUT
          fi

  deploy:
    needs: determine-environment
    runs-on: ubuntu-latest
    environment: ${{ needs.determine-environment.outputs.environment }}
    steps:
      - uses: actions/checkout@v4

      - name: Deploy to Railway
        run: |
          # Deploy to determined environment
          echo "Deploying to ${{ needs.determine-environment.outputs.environment }}"
```

### Pros & Cons

**Pros:**
- ✅ Named environments (easier to remember)
- ✅ Long-lived feature environments
- ✅ Good for large features

**Cons:**
- ❌ Manual environment management
- ❌ Need to clean up old environments
- ❌ Branch naming conventions required

**Best for:** Long-running feature branches, medium teams

---

## 🟣 Option 4: Shared Staging + Feature Flags

Use a single staging environment with feature flags to control what each developer sees.

### Architecture

```
Single Staging Environment
├── Feature Flags (Statsig/LaunchDarkly)
│   ├── payment-v2 → enabled for alice@team.com
│   ├── chat-redesign → enabled for bob@team.com
│   └── admin-dashboard → enabled for charlie@team.com
```

### Setup Instructions

You already have Statsig integrated. Use it for feature flags:

```python
# In your code
from src.services.statsig_service import statsig_service

# Check if feature is enabled for user
if statsig_service.check_gate(user_id, "payment-v2"):
    # Use new payment system
    new_payment_flow()
else:
    # Use old payment system
    old_payment_flow()
```

### Developer Workflow

```bash
# 1. Wrap new feature in feature flag
if statsig_service.check_gate(user_id, "alice-payment-v2"):
    # Alice's new code
    ...

# 2. Deploy to shared staging
git push origin main

# 3. Enable feature flag for yourself
# In Statsig dashboard: Enable "alice-payment-v2" for alice@team.com

# 4. Test your feature
# Other developers won't see it
```

### Pros & Cons

**Pros:**
- ✅ Lowest cost (single environment)
- ✅ Simple infrastructure
- ✅ Good for gradual rollouts
- ✅ Production-ready pattern

**Cons:**
- ❌ All devs share same database
- ❌ Risk of conflicts
- ❌ Requires discipline with feature flags
- ❌ Limited isolation

**Best for:** Large teams, mature codebases, gradual rollouts

---

## 📊 Comparison Summary

### For 2-5 Developers
**Recommended:** Option 1 (Multiple Railway Environments)
- Cost: ~$50-100/month
- Effort: Low
- Isolation: Excellent

### For 5-15 Developers
**Recommended:** Option 2 (PR Deployments)
- Cost: ~$100-300/month
- Effort: Medium (one-time setup)
- Isolation: Excellent

### For 15+ Developers
**Recommended:** Option 4 (Feature Flags)
- Cost: ~$50/month + Statsig
- Effort: Medium (ongoing)
- Isolation: Limited but manageable

---

## 🎯 Recommended Setup for Your Team

Based on your question, I recommend **Option 1 + Option 2 Combined**:

### Hybrid Approach

```
Railway Project
├── staging-main       (shared, deploys from main)
├── staging-dev1       (Alice's long-running feature)
├── staging-dev2       (Bob's long-running feature)
└── PR Deployments     (for quick reviews)
    ├── pr-123        (ephemeral)
    └── pr-124        (ephemeral)
```

**Use Railway Environments for:**
- Long-running features (1+ weeks)
- Database schema changes
- Major refactors

**Use PR Deployments for:**
- Quick features (< 1 week)
- Bug fixes
- Code reviews

### Cost Estimate

For a 5-person team:
- Shared staging: $20/month (Railway)
- 2 developer environments: $40/month
- 5 active PRs average: $50/month
- **Total:** ~$110/month

---

## 🛠️ Implementation Guide

### Phase 1: Set Up Shared Staging (Week 1)

```bash
# 1. Create main staging environment
railway environment create staging-main

# 2. Configure it
railway environment staging-main
railway variables set APP_ENV=staging
# ... set all variables

# 3. Deploy
git push origin main
```

### Phase 2: Add Developer Environments (Week 2)

```bash
# 1. Create Supabase projects for each dev
# gatewayz-staging-dev1
# gatewayz-staging-dev2

# 2. Create Railway environments
railway environment create staging-dev1
railway environment create staging-dev2

# 3. Configure each
railway environment staging-dev1
railway variables set SUPABASE_URL="dev1-url"
# ... etc
```

### Phase 3: Enable PR Deployments (Week 3)

```bash
# 1. Enable in Railway
railway environment create pr-base --pr-base

# 2. Add GitHub workflow
# Copy pr-preview.yml from above

# 3. Test with a PR
gh pr create --title "Test PR deployment"
```

---

## 📋 Environment Naming Convention

```
staging-main          # Main staging (latest from main branch)
staging-dev1-alice    # Alice's personal environment
staging-dev2-bob      # Bob's personal environment
staging-payments      # Payments team environment
staging-chat          # Chat team environment
pr-123                # Ephemeral PR #123
pr-456                # Ephemeral PR #456
```

---

## 🔒 Security for Multiple Environments

Each environment should have its own token:

```bash
# Generate tokens for each environment
python3 -c "import secrets; print('staging_main_' + secrets.token_urlsafe(32))"
python3 -c "import secrets; print('staging_dev1_' + secrets.token_urlsafe(32))"
python3 -c "import secrets; print('staging_dev2_' + secrets.token_urlsafe(32))"

# Set in Railway
railway environment staging-main
railway variables set STAGING_ACCESS_TOKEN="staging_main_abc123..."

railway environment staging-dev1
railway variables set STAGING_ACCESS_TOKEN="staging_dev1_def456..."
```

---

## 📖 Developer Documentation

Create a team wiki page with:

```markdown
# Staging Environments

## Available Environments

| Environment | URL | Database | Purpose | Owner |
|-------------|-----|----------|---------|-------|
| staging-main | staging.gatewayz.ai | Shared staging | Latest main | Team |
| staging-dev1-alice | dev1.gatewayz.ai | Alice's DB | Payment v2 | Alice |
| staging-dev2-bob | dev2.gatewayz.ai | Bob's DB | Chat redesign | Bob |

## Access Tokens

Stored in team 1Password vault: "Staging Environment Tokens"

## How to Get Your Own Environment

1. Ask DevOps to create environment: `staging-dev#-yourname`
2. Create your Supabase project
3. Configure environment variables
4. Push your feature branch
```

---

## 💡 Best Practices

1. **Limit long-lived environments** to 3-5 max
2. **Use PR deployments** for short-lived features
3. **Clean up** old environments weekly
4. **Sync databases** regularly from production (sanitized)
5. **Monitor costs** - Railway dashboard shows spending per environment
6. **Document** which environment is for what feature

---

## ❓ FAQ

**Q: How much does this cost?**
A: ~$20/month per environment. For 5 devs: ~$100-150/month.

**Q: Can devs share a database?**
A: Not recommended. Database conflicts are common.

**Q: What about production?**
A: Production stays separate. Only staging is split.

**Q: How long to set up?**
A: 1-2 hours for first environment, 30 min for each additional.

**Q: Do we need separate Supabase for each?**
A: Recommended for full isolation. Can share if careful.

---

## 🚀 Next Steps

1. Decide which option fits your team
2. Create first developer environment
3. Test the workflow
4. Add more environments as needed
5. Document for your team

**Want me to set this up for you?** Let me know:
- How many developers?
- Average feature duration?
- Current Railway plan?

I can create the environments and workflows for you!
