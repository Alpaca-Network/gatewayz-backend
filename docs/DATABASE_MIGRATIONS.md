# Database Migrations Guide

## Overview

The Gatewayz backend uses **Supabase** for database management and has an **automated migration system** via GitHub Actions. This guide explains how migrations work and how to apply them.

## Automated Migration System

### How It Works

The `.github/workflows/supabase-migrations.yml` workflow **automatically applies migrations** when:

1. **Merged to `main` branch**: Migrations are applied to **production**
2. **Merged to `staging` branch**: Migrations are applied to **staging**
3. **Pull Request**: Migrations are **validated** (not applied)

### Workflow Features

✅ **Automatic Detection**: Triggers when `supabase/migrations/*.sql` files change
✅ **Validation**: Checks SQL syntax and detects destructive operations
✅ **Environment-Aware**: Applies to correct environment based on branch
✅ **Safety Checks**: Blocks dangerous operations in production
✅ **Rollback Support**: Provides rollback instructions on failure
✅ **Notifications**: Comments on PRs and provides detailed logs

### Migration File Naming

Migration files follow this pattern:
```
supabase/migrations/<timestamp>_<description>.sql
```

Example:
```
20251225000000_restore_rate_limit_configs_and_audit_logs.sql
```

The timestamp format is: `YYYYMMDDhhmmss`

---

## Current Migration Status

### Pending Migration

**Migration**: `20251225000000_restore_rate_limit_configs_and_audit_logs.sql`

**Status**: ⚠️ **Not yet applied to production**

**Purpose**: Restores two critical tables:
- `rate_limit_configs` - Per-API-key rate limit configurations
- `api_key_audit_logs` - Audit trail for API key operations

**Why It's Needed**: These tables were accidentally dropped in a previous migration and are referenced by the API key creation code.

---

## Applying Migrations

### Option 1: Automatic (Recommended)

Migrations are **automatically applied** when changes are merged to `main` or `staging`:

1. **Merge PR to main**: The workflow automatically applies to production
2. **Check workflow**: Go to [Actions tab](https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/supabase-migrations.yml)
3. **Verify success**: Look for green checkmark ✅

**For the pending migration**:
- It was added in PR #689 but may not have triggered
- Solution: Manually trigger the workflow (see Option 2) OR wait for next merge to main

### Option 2: Manual Trigger via GitHub UI

You can manually trigger the migration workflow:

1. **Go to**: [Actions → Supabase Migrations](https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/supabase-migrations.yml)

2. **Click**: "Run workflow" button

3. **Select**:
   - **Environment**: `production` or `staging`
   - **Dry run**: `false` (to actually apply)

4. **Click**: "Run workflow"

5. **Monitor**: Watch the workflow execution in real-time

### Option 3: Manual via Supabase CLI (Local)

For developers with Supabase CLI installed:

```bash
# Install Supabase CLI (if not already installed)
brew install supabase/tap/supabase  # macOS
# OR
npm install -g supabase             # npm

# Login to Supabase
supabase login

# Link to your project
supabase link --project-ref <your-project-ref>

# Check migration status
supabase migration list

# Apply pending migrations
supabase db push

# Verify
supabase migration list
```

### Option 4: Manual via Supabase Dashboard

For one-off migrations or emergencies:

1. **Go to**: [Supabase Dashboard](https://supabase.com/dashboard)
2. **Select**: Your project
3. **Navigate to**: SQL Editor
4. **Open**: `supabase/migrations/20251225000000_restore_rate_limit_configs_and_audit_logs.sql`
5. **Copy**: The entire SQL content
6. **Paste**: Into the SQL Editor
7. **Click**: "Run"
8. **Verify**: Check that tables were created

---

## Creating New Migrations

### Step 1: Generate Migration File

```bash
# Using Supabase CLI
supabase migration new <description>

# Example
supabase migration new add_user_preferences_table
```

This creates a new file: `supabase/migrations/<timestamp>_add_user_preferences_table.sql`

### Step 2: Write Migration SQL

Edit the generated file with your SQL:

```sql
-- Example migration
CREATE TABLE IF NOT EXISTS "public"."user_preferences" (
    "id" bigserial PRIMARY KEY,
    "user_id" bigint NOT NULL REFERENCES "public"."users"("id") ON DELETE CASCADE,
    "preferences" jsonb DEFAULT '{}'::jsonb,
    "created_at" timestamptz DEFAULT now(),
    "updated_at" timestamptz DEFAULT now()
);

-- Add index
CREATE INDEX IF NOT EXISTS "user_preferences_user_id_idx"
    ON "public"."user_preferences" ("user_id");

-- Enable RLS
ALTER TABLE "public"."user_preferences" ENABLE ROW LEVEL SECURITY;

-- Add RLS policies
CREATE POLICY "Users can manage their own preferences"
    ON "public"."user_preferences"
    FOR ALL
    TO authenticated
    USING (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()))
    WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));
```

### Step 3: Test Locally (Optional)

```bash
# Start local Supabase
supabase start

# Apply migration locally
supabase db push

# Test your changes
# ... run your application locally ...

# Stop local Supabase
supabase stop
```

### Step 4: Create Pull Request

```bash
git add supabase/migrations/<your-migration>.sql
git commit -m "feat(db): add user preferences table"
git push origin your-branch
```

### Step 5: Automatic Validation

The GitHub Actions workflow will:
- ✅ Validate SQL syntax
- ✅ Check for destructive operations
- ✅ Run on staging (if merged to staging first)
- ✅ Comment on PR with validation results

### Step 6: Merge to Apply

- Merge to `staging`: Applied to staging environment
- Merge to `main`: Applied to production environment

---

## Migration Best Practices

### ✅ DO:

1. **Use IF NOT EXISTS**: Makes migrations idempotent
   ```sql
   CREATE TABLE IF NOT EXISTS "public"."my_table" (...);
   ```

2. **Use IF EXISTS for drops**: Prevents errors
   ```sql
   DROP TABLE IF EXISTS "public"."old_table";
   ```

3. **Add indexes separately**: For better performance
   ```sql
   CREATE INDEX IF NOT EXISTS "idx_name" ON "table" ("column");
   ```

4. **Enable RLS**: For security
   ```sql
   ALTER TABLE "public"."my_table" ENABLE ROW LEVEL SECURITY;
   ```

5. **Test locally first**: Use `supabase start` and `supabase db push`

6. **Add comments**: Explain complex migrations
   ```sql
   -- This migration adds support for user preferences
   -- Related to: https://github.com/org/repo/issues/123
   ```

7. **One logical change per migration**: Don't mix unrelated changes

### ❌ DON'T:

1. **Don't use destructive operations without IF EXISTS**:
   ```sql
   DROP TABLE my_table;  -- BAD: Will fail if table doesn't exist
   ```

2. **Don't modify existing migrations**: Create a new migration instead

3. **Don't add data migrations in schema migrations**: Separate concerns

4. **Don't forget to grant permissions**:
   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON "public"."my_table" TO "authenticated";
   ```

5. **Don't skip migration testing**: Always test on staging first

---

## Troubleshooting

### Migration Failed in Production

1. **Check workflow logs**: [Actions tab](https://github.com/Alpaca-Network/gatewayz-backend/actions)
2. **Review error message**: Look for SQL syntax errors or constraint violations
3. **Fix locally**: Create a new migration to fix the issue
4. **Or rollback**: Use Supabase Dashboard → Database → Backups

### Migration Not Triggering

**Symptoms**: Migration file added but workflow didn't run

**Causes**:
- File not in correct path (`supabase/migrations/`)
- Committed to branch other than `main` or `staging`
- Workflow YAML has syntax error

**Solution**:
1. Verify file path: `ls supabase/migrations/`
2. Check branch: `git branch`
3. Manually trigger: Use "Run workflow" button in GitHub Actions
4. Check workflow syntax: Review `.github/workflows/supabase-migrations.yml`

### "Could not find the table" Errors

**Symptoms**: Logs show `PGRST205` errors about missing tables

**Cause**: Migration not applied yet

**Solution**: Apply the pending migration using one of the options above

### Authentication Failed

**Symptoms**: `Failed to link to Supabase project`

**Cause**: Missing or incorrect secrets

**Solution**:
1. Check GitHub repository secrets:
   - `SUPABASE_ACCESS_TOKEN`
   - `SUPABASE_PROJECT_REF`
   - `SUPABASE_DB_PASSWORD`
2. Regenerate access token in Supabase Dashboard
3. Update secrets in GitHub: Settings → Secrets and variables → Actions

---

## Migration Workflow Reference

### Environment Mapping

| Branch    | Environment | Auto-Apply |
|-----------|-------------|------------|
| `main`    | production  | ✅ Yes     |
| `staging` | staging     | ✅ Yes     |
| Other     | staging     | ❌ No (validation only) |
| PR        | staging     | ❌ No (validation only) |

### Required Secrets

#### Production
- `SUPABASE_ACCESS_TOKEN` - Supabase API access token
- `SUPABASE_PROJECT_REF` - Production project reference ID
- `SUPABASE_DB_PASSWORD` - Production database password

#### Staging
- `SUPABASE_STAGING_PROJECT_REF` - Staging project reference ID
- `SUPABASE_STAGING_DB_PASSWORD` - Staging database password

### Workflow Jobs

1. **setup-environment**: Determines target environment
2. **validate-migrations**: Checks SQL syntax and destructive operations
3. **apply-migrations**: Applies migrations to database
4. **rollback-on-failure**: Provides rollback instructions
5. **notify**: Sends status notifications

---

## Quick Reference

### Check Migration Status
```bash
# Via CLI
supabase migration list

# Via API (if available)
curl https://api.gatewayz.ai/admin/migration-status
```

### Apply Pending Migration NOW

**Fastest method**: Manual trigger via GitHub UI

1. Go to: https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/supabase-migrations.yml
2. Click "Run workflow"
3. Select environment: `production`
4. Select dry_run: `false`
5. Click "Run workflow"
6. Wait ~2-3 minutes
7. Check logs for ✅ success

---

## Related Documentation

- **Supabase CLI**: https://supabase.com/docs/guides/cli
- **Migration Guide**: https://supabase.com/docs/guides/cli/local-development
- **RLS Policies**: https://supabase.com/docs/guides/auth/row-level-security
- **Workflow File**: `.github/workflows/supabase-migrations.yml`

---

**Last Updated**: 2025-12-26
**Workflow Version**: 2.62.10
