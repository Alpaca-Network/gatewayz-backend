# GitHub Secrets Setup for Supabase Migrations

This guide shows you how to configure GitHub Secrets for automated Supabase database migrations via GitHub Actions.

## 📋 Required Secrets

The GitHub Actions workflow needs these secrets to apply migrations automatically:

### 🔐 Authentication Secret
- `SUPABASE_ACCESS_TOKEN` - Your personal Supabase access token

### 🏭 Production Secrets (for `main` branch)
- `SUPABASE_URL` - Production Supabase API URL
- `SUPABASE_SERVICE_ROLE_KEY` - Production service role key (⚠️ keep secret!)
- `SUPABASE_PROJECT_REF` - Production project reference ID
- `SUPABASE_DB_PASSWORD` - Production database password

### 🧪 Staging Secrets (for `staging` branch, optional)
- `SUPABASE_STAGING_URL` - Staging Supabase API URL
- `SUPABASE_STAGING_SERVICE_ROLE_KEY` - Staging service role key
- `SUPABASE_STAGING_PROJECT_REF` - Staging project reference ID
- `SUPABASE_STAGING_DB_PASSWORD` - Staging database password

---

## 🔍 How to Get Each Secret Value

### 1. SUPABASE_ACCESS_TOKEN

**Purpose**: Authenticates the Supabase CLI in GitHub Actions.

**How to get it**:
1. Go to [Supabase Dashboard](https://supabase.com/dashboard)
2. Click your profile icon (top right)
3. Select **Account Settings**
4. Navigate to **Access Tokens** tab
5. Click **Generate New Token**
6. Give it a name like `GitHub Actions CI/CD`
7. Copy the token immediately (you won't see it again!)

**Example**: `sbp_abc123def456ghi789jkl012mno345pqr678stu901vwx234yz`

---

### 2. SUPABASE_URL / SUPABASE_STAGING_URL

**Purpose**: The API endpoint for your Supabase project.

**How to get it**:
1. Open your project in [Supabase Dashboard](https://supabase.com/dashboard)
2. Go to **Settings** (gear icon in sidebar)
3. Select **API** tab
4. Copy the **Project URL**

**Example**: `https://ynleroehyrmaafkgjgmr.supabase.co`

---

### 3. SUPABASE_SERVICE_ROLE_KEY / SUPABASE_STAGING_SERVICE_ROLE_KEY

**Purpose**: Service role key with admin privileges (bypasses RLS).

⚠️ **WARNING**: This is a **secret key** - never commit it to your repository!

**How to get it**:
1. Open your project in [Supabase Dashboard](https://supabase.com/dashboard)
2. Go to **Settings** → **API** tab
3. Scroll to **Project API keys** section
4. Find a **secret** key (`sb_secret_...`, NOT the publishable/anon key). The legacy
   JWT `service_role` key is disabled.
5. Click to reveal and copy

**Example**: `sb_secret_xxxxxxxxxxxxxxxxxxxxxxxx` (placeholder; never paste a real key into docs)

---

### 4. SUPABASE_PROJECT_REF / SUPABASE_STAGING_PROJECT_REF

**Purpose**: Unique identifier for your Supabase project.

**How to get it**:
1. Open your project in [Supabase Dashboard](https://supabase.com/dashboard)
2. Go to **Settings** → **General** tab
3. Find **Reference ID** under **General settings**
4. Copy the reference ID

**Example**: `ynleroehyrmaafkgjgmr`

💡 **Tip**: You can also find this in your Supabase URL:
`https://[PROJECT_REF].supabase.co`

---

### 5. SUPABASE_DB_PASSWORD / SUPABASE_STAGING_DB_PASSWORD

**Purpose**: Database password for connecting to PostgreSQL directly.

**How to get it**:
1. Open your project in [Supabase Dashboard](https://supabase.com/dashboard)
2. Go to **Settings** → **Database** tab
3. Find **Database password** under **Connection string**
4. If you forgot it, click **Reset Database Password**
5. Copy the password

⚠️ **NOTE**: If you reset the password, update it everywhere you use it!

**Example**: `your-secure-database-password-123`

---

## 🚀 Adding Secrets to GitHub

### Method 1: Via GitHub Web Interface

1. Go to your GitHub repository
2. Click **Settings** tab
3. In the left sidebar, click **Secrets and variables** → **Actions**
4. Click **New repository secret**
5. Enter:
   - **Name**: The exact secret name (e.g., `SUPABASE_ACCESS_TOKEN`)
   - **Secret**: The value you copied from Supabase
6. Click **Add secret**
7. Repeat for all required secrets

### Method 2: Via GitHub CLI

```bash
# Install GitHub CLI if needed
brew install gh

# Login to GitHub
gh auth login

# Add secrets (run these commands from your repo directory)
gh secret set SUPABASE_ACCESS_TOKEN
# Paste your token when prompted

gh secret set SUPABASE_URL
# Paste your URL when prompted

gh secret set SUPABASE_SERVICE_ROLE_KEY
# Paste your service role key when prompted

gh secret set SUPABASE_PROJECT_REF
# Paste your project ref when prompted

gh secret set SUPABASE_DB_PASSWORD
# Paste your database password when prompted

# For staging (if needed)
gh secret set SUPABASE_STAGING_URL
gh secret set SUPABASE_STAGING_SERVICE_ROLE_KEY
gh secret set SUPABASE_STAGING_PROJECT_REF
gh secret set SUPABASE_STAGING_DB_PASSWORD
```

---

## ✅ Verification Checklist

After adding secrets, verify:

- [ ] All required secrets are added to GitHub
- [ ] Secret names match exactly (case-sensitive!)
- [ ] Service role key is the **service_role** key (NOT anon key)
- [ ] Project ref matches your Supabase project
- [ ] Database password is correct

### Quick Test

Run a manual workflow to test:

```bash
# Trigger the workflow manually
gh workflow run supabase-migrations.yml \
  --field environment=staging \
  --field dry_run=true
```

Or via GitHub UI:
1. Go to **Actions** tab
2. Select **Supabase Migrations** workflow
3. Click **Run workflow**
4. Choose environment: `staging`
5. Enable **Dry run** checkbox
6. Click **Run workflow**

---

## 🔒 Security Best Practices

### ✅ DO:
- ✅ Use different credentials for production vs staging
- ✅ Rotate access tokens periodically
- ✅ Use GitHub's environment protection rules for production
- ✅ Enable required reviewers for production deployments
- ✅ Monitor secret usage in Actions logs

### ❌ DON'T:
- ❌ Never commit secrets to your repository
- ❌ Never print secrets in logs (they're auto-redacted but still...)
- ❌ Never share service role keys publicly
- ❌ Never use production credentials in development

---

## 🌍 Environment-Specific Behavior

The workflow automatically selects secrets based on the branch:

| Branch | Environment | Secrets Used |
|--------|-------------|--------------|
| `main` | Production | `SUPABASE_*` (without STAGING prefix) |
| `staging` | Staging | `SUPABASE_STAGING_*` |
| Other branches | Staging (for PRs) | `SUPABASE_STAGING_*` |
| Manual trigger | Your choice | Based on selected environment |

---

## 🛠️ Troubleshooting

### "Secret not found" Error

**Problem**: Workflow fails with "secret not found"

**Solutions**:
1. Check secret name spelling (case-sensitive!)
2. Verify secret exists in **Settings** → **Secrets and variables** → **Actions**
3. Make sure secret is added to the **repository** (not organization or environment)

### "Authentication failed" Error

**Problem**: Supabase CLI can't authenticate

**Solutions**:
1. Verify `SUPABASE_ACCESS_TOKEN` is correct
2. Check token hasn't expired
3. Regenerate token if needed

### "Failed to link to Supabase project" Error

**Problem**: Can't connect to project

**Solutions**:
1. Verify `SUPABASE_PROJECT_REF` matches your project
2. Check `SUPABASE_DB_PASSWORD` is correct
3. Ensure project exists and is accessible

### "Permission denied" Error

**Problem**: Can't apply migrations

**Solutions**:
1. Verify you're using **service_role** key (not anon key)
2. Check database password is correct
3. Ensure project isn't paused or suspended

---

## 📝 Example Secret Values

Here's what your secrets should look like (with fake values):

```bash
# Authentication
SUPABASE_ACCESS_TOKEN=sbp_abc123def456ghi789jkl012mno345pqr678stu901vwx234yz

# Production
SUPABASE_URL=https://ynleroehyrmaafkgjgmr.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<your-production-secret-key (sb_secret_...)>
SUPABASE_PROJECT_REF=ynleroehyrmaafkgjgmr
SUPABASE_DB_PASSWORD=your-secure-db-password-prod

# Staging (if using)
SUPABASE_STAGING_URL=https://abcdefghijklmnopqrst.supabase.co
SUPABASE_STAGING_SERVICE_ROLE_KEY=<your-staging-secret-key (sb_secret_...)>
SUPABASE_STAGING_PROJECT_REF=abcdefghijklmnopqrst
SUPABASE_STAGING_DB_PASSWORD=your-secure-db-password-staging
```

---

## 🎯 Next Steps

After adding all secrets:

1. ✅ Test with a dry run (see Verification section above)
2. ✅ Create a test migration and push to `staging` branch
3. ✅ Verify workflow runs successfully
4. ✅ Check migration was applied in Supabase Dashboard
5. ✅ Merge to `main` for production deployment

---

## 📚 Related Documentation

- [GitHub Actions Workflow](.github/workflows/supabase-migrations.yml)
- [Migration Sync Guide](MIGRATION_SYNC_GUIDE.md)
- [Supabase Migrations CI Guide](SUPABASE_MIGRATIONS_CI.md)
- [Testing Workflows Locally](TESTING_WORKFLOWS_LOCALLY.md)

---

## 🆘 Need Help?

If you're stuck:

1. Check the [Troubleshooting](#-troubleshooting) section above
2. Review GitHub Actions logs for specific error messages
3. Verify all secrets are correct in Supabase Dashboard
4. Test Supabase CLI locally: `supabase login` and `supabase link`

**Quick Debug Command**:
```bash
# Test if your secrets work locally
export SUPABASE_ACCESS_TOKEN="your-token"
supabase login --token "$SUPABASE_ACCESS_TOKEN"
supabase link --project-ref "your-project-ref" --password "your-db-password"
supabase migration list
```

If this works locally, the same credentials should work in GitHub Actions!
