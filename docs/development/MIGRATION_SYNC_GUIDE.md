# Migration Sync Guide

## Problem: GitHub and Supabase Out of Sync

You have migration files in GitHub that Supabase doesn't know about, causing errors like:

```
Error: Migration xxx is not applied on remote database
Error: Cannot push migrations - remote database is ahead/behind
```

---

## 🎯 Quick Solutions

### Solution 1: Mark Migrations as Applied (Safest)

**Use when:** Your database already has these changes (you applied them manually or via another method)

```bash
# Run the helper script
./fix-migration-sync.sh

# Choose option 1
# Enter migration timestamp or 'all'
```

**Or manually:**
```bash
# Mark a specific migration as applied
supabase migration repair --status applied 20251105000000

# Mark all migrations as applied
for file in supabase/migrations/*.sql; do
    timestamp=$(basename "$file" .sql | cut -d'_' -f1)
    supabase migration repair --status applied "$timestamp"
done
```

---

### Solution 2: Check Migration Status First

**See what Supabase thinks:**

```bash
# List all migrations and their status
supabase migration list

# You'll see something like:
# 20251105000000_migration_name.sql | Applied
# 20251106000000_new_migration.sql  | Not applied ← This is the problem
```

---

### Solution 3: Apply Missing Migrations

**Use when:** The migrations haven't been applied yet and you want to apply them

```bash
# Push all pending migrations
supabase db push

# If that fails, try:
supabase db push --password <your-db-password>
```

---

## 🔍 Diagnosis Steps

### Step 1: Check What You Have Locally

```bash
# List local migration files
ls -1 supabase/migrations/

# Output example:
# 20251011_fix_permissions.sql
# 20251105000000_add_missing_rate_limit_tables.sql
# 20251121000000_add_providers_and_models_tables.sql
```

### Step 2: Check What Supabase Knows

```bash
# Check remote migration status
supabase migration list

# Output shows which migrations are applied vs not applied
```

### Step 3: Identify the Gap

Compare the two lists. Migrations in GitHub but not in Supabase = the problem.

---

## 🛠️ Detailed Solutions

### Option A: Migrations Already Applied (Most Common)

**Scenario:** You or someone else applied migrations directly, or they were applied before tracking started.

**Fix:**
```bash
# Check which migrations need marking
supabase migration list

# Mark specific migration as applied
supabase migration repair --status applied 20251105000000

# Or mark all as applied
./fix-migration-sync.sh
# Choose option 1 → Enter 'all'
```

**Result:** Supabase now knows these migrations exist, no changes to database.

---

### Option B: Migrations Need to Be Applied

**Scenario:** These migrations have never been applied to your database.

**Fix:**
```bash
# Method 1: Push migrations
supabase db push

# Method 2: Push with explicit password
supabase db push --password <your-db-password>

# Method 3: Via CI/CD (recommended for production)
# Merge to main → GitHub Actions applies them
```

**Result:** Migrations are executed, database is updated.

---

### Option C: Complex - Some Applied, Some Not

**Scenario:** Mixed state - some migrations applied, others not.

**Fix:**
```bash
# 1. Check status of each
supabase migration list

# 2. Mark already-applied ones
supabase migration repair --status applied 20251105000000
supabase migration repair --status applied 20251106000000

# 3. Apply the rest
supabase db push
```

---

### Option D: Nuclear Option - Reset Everything

**⚠️ DESTRUCTIVE - Only use in development!**

**Scenario:** Everything is messed up, you want to start fresh.

**Fix:**
```bash
# This DROPS your database and reapplies all migrations
supabase db reset

# Or via script
./fix-migration-sync.sh
# Choose option 4 → Type 'YES'
```

**⚠️ WARNING:** This deletes all data!

---

## 📋 Step-by-Step: Safe Sync Process

### 1. Backup First (Production Only)

```bash
# Export current schema
supabase db dump -f backup.sql

# Or backup from Supabase Dashboard
# Dashboard > Database > Backups > Create Backup
```

### 2. Check Current State

```bash
# Local migrations
ls -1 supabase/migrations/

# Remote status
supabase migration list
```

### 3. Determine Which Migrations to Handle

Create a list:
```
Migrations in GitHub but not tracked in Supabase:
- 20251105000000_add_missing_rate_limit_tables.sql
- 20251121000000_add_providers_and_models_tables.sql
```

### 4. Decide: Applied or Not?

**Check your database:**
```sql
-- Do these tables exist?
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('rate_limit_configs', 'providers', 'models');
```

- **If tables exist** → Mark as applied (Option A)
- **If tables don't exist** → Apply migrations (Option B)

### 5. Execute Your Choice

**If marking as applied:**
```bash
./fix-migration-sync.sh
# Option 1 → Enter 'all'
```

**If applying:**
```bash
supabase db push
```

### 6. Verify

```bash
# Check status again
supabase migration list

# All should show "Applied" ✓
```

### 7. Test

```bash
# Try pulling schema
supabase db pull

# Should work without errors ✓
```

---

## 🚨 Common Errors & Fixes

### Error: "Migration xxx is not applied on remote"

**Cause:** GitHub has migration file, Supabase doesn't know about it.

**Fix:**
```bash
supabase migration repair --status applied xxx
```

### Error: "Cannot connect to database"

**Cause:** Not linked to Supabase project.

**Fix:**
```bash
# Link to your project
supabase link --project-ref ynleroehyrmaafkgjgmr

# Or login first
supabase login
```

### Error: "Migration already applied"

**Cause:** Trying to apply migration that's already in database.

**Fix:**
```bash
# Mark it as applied
supabase migration repair --status applied xxx
```

### Error: "Conflicts detected"

**Cause:** Database schema differs from migrations.

**Fix:**
```bash
# Pull current schema to see differences
supabase db pull

# Review the generated migration
# Decide: keep it or discard it
```

---

## 🎯 Recommended Workflow

### For Development

```bash
# 1. Create migration locally
supabase migration new my_change

# 2. Edit the migration file
# 3. Apply locally
supabase db push

# 4. Test it works
# 5. Commit to GitHub
git add supabase/migrations/
git commit -m "Add migration"
git push
```

### For Production

```bash
# 1. Merge PR to main
# 2. GitHub Actions automatically applies migrations
# 3. Verify in Supabase Dashboard
```

---

## 📊 Migration States

| State | Meaning | Action Needed |
|-------|---------|---------------|
| **Applied** | ✅ Migration ran successfully | None |
| **Not Applied** | ⏳ Pending execution | Run `supabase db push` |
| **Reverted** | ⏮️ Rolled back | Can reapply with `db push` |
| **Missing** | ❌ File exists locally, not tracked remotely | Use `migration repair` |

---

## 🔧 Quick Commands Reference

```bash
# Check migration status
supabase migration list

# Mark migration as applied (doesn't run it)
supabase migration repair --status applied <timestamp>

# Mark migration as reverted (allows reapply)
supabase migration repair --status reverted <timestamp>

# Apply pending migrations
supabase db push

# Pull current schema
supabase db pull

# Reset database (DESTRUCTIVE)
supabase db reset

# Link to project
supabase link --project-ref <ref>

# Login to Supabase
supabase login
```

---

## 🎓 Understanding Migration Tracking

Supabase tracks migrations in a special table:

```sql
-- Check migration history
SELECT * FROM supabase_migrations.schema_migrations;

-- You'll see:
-- version (timestamp) | statements | name
```

When you run `supabase migration repair`, it updates this table without running the migration.

---

## ✅ Verification Checklist

After syncing:

- [ ] `supabase migration list` shows all migrations as "Applied"
- [ ] `supabase db pull` works without errors
- [ ] `supabase db push` says "No new migrations to apply"
- [ ] Your application still works correctly
- [ ] No data was lost

---

## 🆘 Still Stuck?

### Get Detailed Logs

```bash
# Run with debug output
supabase --debug migration list
supabase --debug db push
```

### Check Your Setup

```bash
# Are you linked?
supabase projects list

# Which project are you on?
cat .git/config | grep supabase
```

### Manual Database Check

```sql
-- Connect to your database and run:
SELECT version, name
FROM supabase_migrations.schema_migrations
ORDER BY version;
```

Compare with your local files in `supabase/migrations/`.

---

## 🚀 Quick Fix Script

For most cases, run:

```bash
./fix-migration-sync.sh
```

Choose option based on your situation:
- **Option 1** - Mark as applied (safest)
- **Option 2** - Mark as reverted (to reapply)
- **Option 3** - Check status first
- **Option 4** - Nuclear reset (dev only)

---

**Need help?** Check the output of:
```bash
supabase migration list
```

And tell me what you see!
