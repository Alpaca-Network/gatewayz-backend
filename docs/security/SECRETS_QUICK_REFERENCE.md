# 🔑 GitHub Secrets Quick Reference

## Required Secrets for Migration Deployment

### Authentication (Required)
```
SUPABASE_ACCESS_TOKEN
```
**Get it**: Supabase Dashboard → Profile → Account Settings → Access Tokens → Generate

---

### Production Secrets (for `main` branch)
```
SUPABASE_URL                   (e.g., https://xxx.supabase.co)
SUPABASE_SERVICE_ROLE_KEY      (⚠️ SECRET - starts with eyJhbGci...)
SUPABASE_PROJECT_REF           (e.g., ynleroehyrmaafkgjgmr)
SUPABASE_DB_PASSWORD           (your database password)
```
**Get them**: Your Production Project → Settings → API / Database

---

### Staging Secrets (optional, for `staging` branch)
```
SUPABASE_STAGING_URL
SUPABASE_STAGING_SERVICE_ROLE_KEY
SUPABASE_STAGING_PROJECT_REF
SUPABASE_STAGING_DB_PASSWORD
```
**Get them**: Your Staging Project → Settings → API / Database

---

## 🚀 Quick Setup

### Option 1: Interactive Script (Recommended)
```bash
./scripts/setup-github-secrets.sh
```

### Option 2: Manual (GitHub CLI)
```bash
gh secret set SUPABASE_ACCESS_TOKEN
gh secret set SUPABASE_URL
gh secret set SUPABASE_SERVICE_ROLE_KEY
gh secret set SUPABASE_PROJECT_REF
gh secret set SUPABASE_DB_PASSWORD
```

### Option 3: GitHub Web UI
GitHub Repo → Settings → Secrets and variables → Actions → New repository secret

---

## ✅ Verify Setup

```bash
# List all secrets
gh secret list

# Test with dry run
gh workflow run supabase-migrations.yml \
  --field environment=staging \
  --field dry_run=true
```

---

## 📖 Full Documentation

See `docs/GITHUB_SECRETS_SETUP.md` for complete guide with screenshots and troubleshooting.
