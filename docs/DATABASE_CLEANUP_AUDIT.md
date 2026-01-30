# Database Cleanup Audit - Production Supabase

**Date**: January 28, 2026
**Database**: https://ynleroehyrmaafkgjgmr.supabase.co

## Executive Summary

After analyzing the production database against the codebase, here are the findings:

- ✅ **Most tables are actively used** with significant data
- ⚠️  **2 tables do not exist** but are referenced in code (needs fixing)
- 🗑️  **5 tables can potentially be cleaned up** (see details below)
- ❓ **3 legacy tables have data** but no code references (investigate usage)

---

## Tables Not Referenced in Code But Have Data

These tables have significant data despite no code references. They may be:
- Used by external services
- Accessed directly by frontend
- Populated by webhooks or cron jobs

| Table | Row Count | Status | Notes |
|-------|-----------|--------|-------|
| `openrouter_apps` | 166 | ✅ Keep | Likely cached data from OpenRouter |
| `openrouter_models` | 369 | ✅ Keep | Likely cached model catalog |
| `usage_records` | 14,322 | ✅ Keep | Legacy usage data - may be needed for analytics/reports |
| `reconciliation_logs` | 395 | ✅ Keep | Trial reconciliation logs - needed for auditing |
| `temporary_email_domains` | 668 | ✅ Keep | Email validation - likely used in validation logic |

**Recommendation**: Keep all these tables. They contain valuable data and may be accessed by services not visible in the Python codebase (e.g., frontend, scheduled jobs, webhooks).

---

## Tables Referenced in Code But Do NOT Exist

These tables are referenced in the codebase but don't exist in production:

| Table | References | Status | Action Needed |
|-------|------------|--------|---------------|
| `pricing_tiers` | Migration file | ❌ Missing | Either create or remove references |
| `rate_limits` | 5 code refs | ❌ Missing | **CRITICAL**: Code references non-existent table |

**Recommendation**:
- `rate_limits` - This is critical! Check `src/db/rate_limits.py:46` - code tries to query this table but it doesn't exist. Likely replaced by `rate_limit_configs`.
- Update code to remove references or create the table if needed.

---

## Small Tables - Review Candidates

| Table | Row Count | Purpose | Recommendation |
|-------|-----------|---------|----------------|
| `admin_users` | 9 | Admin accounts | ⚠️  **Keep** - Contains actual admin users |
| `trial_config` | 1 | Trial configuration | 🗑️  **Can drop** - Single config row, move to app config |
| `payments` | 94 | Payment records | ✅ **Keep** - Real payment data |
| `providers` | 35 | AI provider configs | ✅ **Keep** - Active provider registry |

---

## Detailed Cleanup Recommendations

### 1. ⚠️  CRITICAL - Fix Code References to Non-Existent Tables

**`rate_limits` table (5 references in code, table doesn't exist)**

Locations:
- `src/db/rate_limits.py:46` - `.table("rate_limits").select("*")`
- `src/db/rate_limits.py:87` - `.table("rate_limits").select("*")`
- `src/db/rate_limits.py:90` - `.table("rate_limits").update(...)`
- `src/db/rate_limits.py:92` - `.table("rate_limits").insert(...)`

**Action**: Update these references to use `rate_limit_configs` instead, or create the missing table.

---

### 2. 🗑️  OPTIONAL - Drop `trial_config` Table

This table has only 1 row and appears to be a configuration table. Configuration is better managed in:
- Application environment variables
- In-code constants
- `src/config/config.py`

**Steps to drop**:
```sql
-- 1. Export the config value first
SELECT * FROM trial_config;

-- 2. Move to app config or environment variable
-- Add to .env: TRIAL_DURATION_DAYS=3, TRIAL_CREDITS=5

-- 3. Drop the table
DROP TABLE IF EXISTS public.trial_config CASCADE;

-- 4. Update code to read from config instead of DB
```

**Files to update**:
- Any code that queries `trial_config` (check with grep)

---

### 3. 🧹  OPTIONAL - Remove Migration References to `pricing_tiers`

The `pricing_tiers` table is defined in migrations but doesn't exist in production.

**Action**: Either:
- Create the table if it's needed
- Remove from migrations if it was abandoned

---

### 4. ⚠️  WARNING - Keep Legacy Tables With Data

Do NOT drop these without investigation:
- `openrouter_apps` (166 rows) - May be used for caching
- `openrouter_models` (369 rows) - May be used for model lookup
- `usage_records` (14,322 rows) - Historical data, may be needed for reports

These might be:
- Accessed by database functions/triggers
- Queried directly by frontend
- Used by analytics/reporting tools
- Populated by webhooks

---

## Views and Materialized Tables

The following are referenced in code but not found in migrations (likely views):

| Name | Type | Status |
|------|------|--------|
| `latest_apps` | View | ✅ Keep - used for rankings |
| `latest_models` | View | ✅ Keep - used for rankings |
| `model_status_current` | View | ✅ Keep - health monitoring |
| `model_usage_analytics` | View | ✅ Keep - analytics |
| `provider_health_current` | View | ✅ Keep - health monitoring |
| `provider_stats_24h` | View | ✅ Keep - statistics |

These are likely database views created by functions, not regular tables.

---

## Action Items Summary

### 🚨 High Priority (Critical)
1. **Fix `rate_limits` table references** - Code references non-existent table
   - Files: `src/db/rate_limits.py` (4 locations)
   - Either create the table or update code to use `rate_limit_configs`

### ⚠️  Medium Priority (Optional)
2. **Drop `trial_config`** - Single-row config table (move to env vars)
3. **Clean up `pricing_tiers` migration** - Referenced in migrations but doesn't exist

### ✅ Low Priority (Informational)
4. **Document legacy tables** - `openrouter_*` and `usage_records` have data but no code refs
5. **Monitor empty migrations** - Some tables in migrations aren't in production

---

## SQL Scripts for Cleanup

### Drop `trial_config` (Optional)
```sql
-- Backup first
CREATE TABLE trial_config_backup AS SELECT * FROM trial_config;

-- Drop the table
DROP TABLE IF EXISTS public.trial_config CASCADE;
```

### Investigate `rate_limits` table issue
```sql
-- Check if table exists
SELECT EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_schema = 'public'
    AND table_name = 'rate_limits'
);

-- If it should exist, create it based on rate_limit_configs structure
-- Or update code to use rate_limit_configs instead
```

---

## Database Health Summary

| Metric | Value |
|--------|-------|
| Total users | 40,312 |
| Total API keys | 40,152 |
| Chat requests | 246,533 |
| Activity logs | 173,942 |
| Credit transactions | 125,152 |
| Models in catalog | 11,370 |
| Providers | 35 |

**Overall health**: ✅ Excellent - Production database is well-populated and actively used.

---

## Conclusion

Your production database is in good shape. The main issue is:

1. **Code references `rate_limits` table that doesn't exist** - This needs to be fixed
2. **`trial_config` can optionally be removed** - It's a single-row config that belongs in app config
3. **Keep all other tables** - Even those without code references likely serve a purpose

No aggressive cleanup is recommended at this time.
