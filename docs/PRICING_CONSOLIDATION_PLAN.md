# Model Pricing Consolidation Plan

**Status**: Draft
**Created**: 2026-01-19
**Goal**: Consolidate all model pricing data into the `model_pricing` table as the single source of truth

---

## Executive Summary

Currently, pricing data is stored in **two locations**:
1. **`models` table** - Legacy pricing columns (`pricing_prompt`, `pricing_completion`, `pricing_image`, `pricing_request`)
2. **`model_pricing` table** - New normalized pricing table with classification support

Additionally, the `pricing_tiers` table exists but is **completely unused**.

This plan consolidates all pricing to the `model_pricing` table, providing:
- ✅ Single source of truth
- ✅ Easier pricing updates without touching model data
- ✅ Pricing classification (paid/free/deprecated/missing)
- ✅ Better tracking with `pricing_source` and `last_updated`
- ✅ Cleaner separation of concerns

---

## Current State Assessment

### Tables Identified

| Table | Status | Purpose | Rows | Used In Code |
|-------|--------|---------|------|--------------|
| `models` (pricing columns) | 🟡 Legacy | Stores pricing on model records | ? | 7 files |
| `model_pricing` | 🟢 Active | Normalized pricing table | ? | 4 files |
| `pricing_tiers` | 🔴 Unused | Generic tier pricing | 0 | 0 files |

### Code Files Using Legacy Pricing Columns

1. **`src/schemas/models_catalog.py`** - Pydantic schemas (lines 26-29, 63-66)
2. **`src/services/model_catalog_sync.py`** - Model sync service (lines 266-269)
3. **`src/services/pricing_sync_background.py`** - Pricing sync (lines 208-243)
4. **`src/services/failover_service.py`** - Failover logic (lines 134-135, 253)
5. **`src/db/failover_db.py`** - Failover DB queries (lines 67-70, 132-135, 164)
6. **`src/db/chat_completion_requests.py`** - Request tracking (lines 788-789, 974-975)
7. **`src/db/chat_completion_requests_enhanced.py`** - Enhanced tracking (lines 196, 216-217)

### Code Files Using `model_pricing` Table (New)

1. **`src/services/pricing_sync_background.py`** - Syncs from models → model_pricing
2. **`src/services/model_pricing_service.py`** - CRUD operations on model_pricing
3. **`src/services/pricing_analytics.py`** - Uses `model_usage_analytics` view
4. **`src/routes/monitoring.py`** - Admin monitoring queries

---

## Migration Strategy

### Phase 1: Data Population ✅ (Already Done)

The following migrations already exist:
- ✅ `20260119120000_create_model_pricing_table.sql` - Creates `model_pricing` table
- ✅ `20260119120001_populate_model_pricing_rpc.sql` - Populate function
- ✅ `20260119120004_add_pricing_classification.sql` - Adds pricing_type field
- ✅ `20260119120005_add_missing_pricing_type.sql` - Handles missing pricing

**Views Created:**
- `models_with_pricing` - Joins models + model_pricing
- `models_pricing_classified` - Adds classification labels
- `models_pricing_status` - Comprehensive status view

### Phase 2: Code Migration (This Plan)

**Objective**: Update all code to use `model_pricing` instead of direct `models.pricing_*` columns

#### 2.1 Update Pydantic Schemas

**File**: `src/schemas/models_catalog.py`

**Changes**:
- Create new `ModelPricing` schema for model_pricing table
- Add optional `pricing` field to `ModelResponse` that contains pricing data
- Keep legacy fields temporarily with deprecation warnings
- Update docs to indicate preferred approach

**New Schema**:
```python
class ModelPricing(BaseModel):
    """Model pricing information"""
    model_id: int
    price_per_input_token: Decimal
    price_per_output_token: Decimal
    price_per_image_token: Decimal | None = None
    price_per_request: Decimal | None = None
    pricing_source: str = "provider"
    pricing_type: str = "paid"
    last_updated: datetime

class ModelWithPricing(ModelResponse):
    """Model with normalized pricing"""
    pricing: ModelPricing | None = None
```

#### 2.2 Update Database Access Layer

**File**: `src/db/models_catalog_db.py`

**Changes**:
- Add function `get_model_pricing(model_id: int)` to fetch from model_pricing
- Update `get_all_models()` to join with model_pricing by default
- Update `create_model()` to optionally create pricing entry
- Add `upsert_model_pricing()` function

**File**: `src/db/failover_db.py` (lines 67-70, 132-135, 164)

**Changes**:
- Update SQL query to join with `model_pricing` table
- Replace `pricing_prompt`, `pricing_completion` with `price_per_input_token`, `price_per_output_token`
- Update field mappings in response

#### 2.3 Update Services Layer

**File**: `src/services/model_catalog_sync.py` (lines 266-269)

**Changes**:
- Instead of setting pricing on model dict, create separate pricing entry
- Call `upsert_model_pricing()` after model upsert
- Keep models table clean of pricing data

**File**: `src/services/pricing_sync_background.py` (lines 208-243)

**Changes**:
- Already reads from `models.pricing_*` and writes to `model_pricing`
- Update to only write to `model_pricing`, stop reading from models
- Use model_pricing as source for any pricing queries

**File**: `src/services/failover_service.py` (lines 134-135, 253)

**Changes**:
- Update to read pricing from `model_pricing` join
- Update response dict keys to new field names

#### 2.4 Update Chat Completion Request Tracking

**File**: `src/db/chat_completion_requests.py` (lines 788-789, 974-975)

**Changes**:
- Join with `model_pricing` instead of reading from models
- Update field names in response dict
- Ensure backwards compatibility for API responses

**File**: `src/db/chat_completion_requests_enhanced.py` (lines 196, 216-217)

**Changes**:
- Update SQL query to join `model_pricing`
- Update field access to use `price_per_input_token`, `price_per_output_token`

### Phase 3: Database Schema Migration

**New Migration**: `20260121000003_remove_pricing_columns_from_models.sql`

**Steps**:
1. **Backup Check**: Verify all pricing data exists in `model_pricing`
2. **Drop Columns**: Remove pricing columns from models table
   - `pricing_prompt`
   - `pricing_completion`
   - `pricing_image`
   - `pricing_request`
3. **Drop Table**: Remove `pricing_tiers` table (unused)
4. **Update Views**: Ensure all views use `model_pricing`

**Migration SQL**:
```sql
-- ============================================================================
-- Remove Legacy Pricing Columns from Models Table
-- ============================================================================
-- WARNING: This migration is IRREVERSIBLE without a backup
-- Ensure all pricing data is in model_pricing table before running
-- ============================================================================

-- Step 1: Verify pricing data migration
DO $$
DECLARE
    models_count INTEGER;
    pricing_count INTEGER;
BEGIN
    -- Count models with pricing
    SELECT COUNT(*) INTO models_count
    FROM models
    WHERE pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL;

    -- Count model_pricing entries
    SELECT COUNT(*) INTO pricing_count
    FROM model_pricing;

    RAISE NOTICE 'Models with pricing: %', models_count;
    RAISE NOTICE 'model_pricing entries: %', pricing_count;

    IF pricing_count < models_count THEN
        RAISE EXCEPTION 'Not all pricing data has been migrated. Aborting.';
    END IF;
END$$;

-- Step 2: Drop pricing columns from models table
ALTER TABLE models
    DROP COLUMN IF EXISTS pricing_prompt,
    DROP COLUMN IF EXISTS pricing_completion,
    DROP COLUMN IF EXISTS pricing_image,
    DROP COLUMN IF EXISTS pricing_request;

-- Step 3: Drop unused pricing_tiers table
DROP TABLE IF EXISTS pricing_tiers CASCADE;

-- Step 4: Update models_with_pricing view to be primary view
-- (Already exists and uses model_pricing)

COMMENT ON TABLE model_pricing IS
    'SINGLE SOURCE OF TRUTH for all model pricing. Updated: 2026-01-21';

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Legacy pricing columns removed from models table';
    RAISE NOTICE '✅ pricing_tiers table dropped';
    RAISE NOTICE '✅ model_pricing is now the ONLY source for pricing data';
END$$;
```

### Phase 4: Testing & Validation

#### 4.1 Unit Tests

**New Test File**: `tests/services/test_model_pricing_migration.py`

Test cases:
- ✅ `test_get_model_pricing()` - Fetch pricing by model_id
- ✅ `test_upsert_model_pricing()` - Insert/update pricing
- ✅ `test_model_with_pricing_join()` - Verify join works
- ✅ `test_pricing_classification()` - Verify paid/free/missing types
- ✅ `test_failover_uses_pricing_table()` - Failover service uses correct table
- ✅ `test_chat_request_costing()` - Chat requests calculate costs correctly

#### 4.2 Integration Tests

**Test File**: `tests/integration/test_pricing_consolidation.py`

Test scenarios:
- ✅ Create model → pricing entry created
- ✅ Update model → pricing not affected
- ✅ Update pricing → model not affected (separation verified)
- ✅ Delete model → pricing cascades (FK constraint)
- ✅ Query model_catalog → pricing included
- ✅ Cost calculation for chat requests

#### 4.3 Migration Verification Script

**Script**: `scripts/verify_pricing_migration.py`

```python
#!/usr/bin/env python3
"""
Verify pricing data migration is complete before removing columns
"""
from src.config.supabase_config import get_supabase_client

def verify_migration():
    client = get_supabase_client()

    # 1. Check all models with pricing have entries in model_pricing
    models_with_pricing = client.table("models").select(
        "id", count="exact"
    ).or_("pricing_prompt.not.is.null,pricing_completion.not.is.null").execute()

    pricing_entries = client.table("model_pricing").select(
        "model_id", count="exact"
    ).execute()

    print(f"Models with pricing: {models_with_pricing.count}")
    print(f"model_pricing entries: {pricing_entries.count}")

    if pricing_entries.count < models_with_pricing.count:
        print("❌ MIGRATION INCOMPLETE")
        return False

    # 2. Verify no NULL pricing for paid models
    null_pricing = client.table("model_pricing").select(
        "model_id"
    ).eq("pricing_type", "paid").eq("price_per_input_token", 0).execute()

    if null_pricing.data:
        print(f"⚠️  {len(null_pricing.data)} paid models with $0 pricing")

    # 3. Check pricing_tiers usage
    pricing_tiers = client.table("pricing_tiers").select("*").execute()
    if pricing_tiers.data:
        print(f"⚠️  pricing_tiers has {len(pricing_tiers.data)} rows (should be empty)")

    print("✅ MIGRATION VERIFICATION PASSED")
    return True

if __name__ == "__main__":
    verify_migration()
```

### Phase 5: Deployment Strategy

#### 5.1 Pre-Deployment

1. **Run verification script** (above)
2. **Backup database** (Supabase automatic backups + manual dump)
3. **Deploy code changes** (without schema migration)
4. **Monitor for 24-48 hours**
   - Check error logs
   - Verify pricing queries working
   - Ensure chat request costing accurate

#### 5.2 Deployment

1. **Apply schema migration** (`20260121000003_remove_pricing_columns_from_models.sql`)
2. **Monitor application**
   - Check Sentry for errors
   - Verify API responses
   - Check admin dashboard
3. **Run post-deployment verification**

#### 5.3 Rollback Plan

**If issues occur within 24 hours:**

1. **Re-add columns** (from backup or manual):
   ```sql
   ALTER TABLE models
       ADD COLUMN pricing_prompt NUMERIC(20, 10),
       ADD COLUMN pricing_completion NUMERIC(20, 10),
       ADD COLUMN pricing_image NUMERIC(20, 10),
       ADD COLUMN pricing_request NUMERIC(10, 6);
   ```

2. **Repopulate from model_pricing**:
   ```sql
   UPDATE models m
   SET
       pricing_prompt = mp.price_per_input_token,
       pricing_completion = mp.price_per_output_token,
       pricing_image = mp.price_per_image_token,
       pricing_request = mp.price_per_request
   FROM model_pricing mp
   WHERE m.id = mp.model_id;
   ```

3. **Revert code deployment**

---

## Benefits After Consolidation

### ✅ Improved Data Integrity
- Single source of truth - no sync issues
- Foreign key constraints ensure referential integrity
- Pricing history tracked separately from model updates

### ✅ Better Maintainability
- Update pricing without touching model records
- Easier to add new pricing features (tiered pricing, regional pricing)
- Clearer code - pricing concerns separated

### ✅ Enhanced Features
- Pricing classification (paid/free/deprecated/missing)
- Pricing source tracking (provider/manual/estimated)
- Last updated timestamps for pricing changes
- Easy to audit pricing changes

### ✅ Performance
- Smaller models table (4 fewer columns)
- Targeted indexes on pricing queries
- Can cache pricing separately from models

---

## Timeline Estimate

| Phase | Duration | Parallel? |
|-------|----------|-----------|
| Phase 1: Data Population | ✅ Complete | - |
| Phase 2: Code Migration | 2-3 days | Can parallelize by file |
| Phase 3: Database Migration | 1 day | After Phase 2 |
| Phase 4: Testing | 1-2 days | Parallel with Phase 2 |
| Phase 5: Deployment | 1 day + monitoring | After all above |
| **Total** | **5-7 days** | With parallelization |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Pricing data loss | Low | Critical | Verification script, backups |
| Query performance degradation | Low | Medium | Indexes already in place, test queries |
| Missed code references | Medium | High | Comprehensive grep, testing |
| API response changes | Medium | High | Maintain backward compatibility |
| Deployment downtime | Low | Medium | Deploy code first, schema later |

---

## Success Criteria

- ✅ All tests passing (unit + integration)
- ✅ Verification script passes
- ✅ No pricing-related errors in Sentry (24h post-deploy)
- ✅ Chat request costing accurate (spot check 100 requests)
- ✅ Admin dashboard pricing displays correctly
- ✅ Model catalog API returns pricing
- ✅ Failover service selects providers based on pricing

---

## Appendix A: SQL Queries for Verification

### Check pricing data coverage
```sql
-- Models with pricing in models table
SELECT COUNT(*) as models_with_pricing
FROM models
WHERE pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL;

-- Entries in model_pricing
SELECT COUNT(*) as pricing_entries
FROM model_pricing;

-- Models missing from model_pricing
SELECT m.id, m.model_name, m.provider_id
FROM models m
LEFT JOIN model_pricing mp ON m.id = mp.model_id
WHERE (m.pricing_prompt IS NOT NULL OR m.pricing_completion IS NOT NULL)
  AND mp.model_id IS NULL;
```

### Check pricing type distribution
```sql
SELECT
    pricing_type,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
FROM model_pricing
GROUP BY pricing_type
ORDER BY count DESC;
```

### Verify pricing accuracy (sample)
```sql
SELECT
    m.model_name,
    m.pricing_prompt as old_prompt_price,
    mp.price_per_input_token as new_prompt_price,
    m.pricing_completion as old_completion_price,
    mp.price_per_output_token as new_completion_price
FROM models m
INNER JOIN model_pricing mp ON m.id = mp.model_id
WHERE m.pricing_prompt IS NOT NULL
LIMIT 10;
```

---

## Appendix B: Files to Modify

### High Priority (Core Functionality)
1. ✅ `src/schemas/models_catalog.py` - API schemas
2. ✅ `src/db/models_catalog_db.py` - Model CRUD
3. ✅ `src/db/failover_db.py` - Failover queries
4. ✅ `src/services/failover_service.py` - Failover logic
5. ✅ `src/db/chat_completion_requests.py` - Request tracking
6. ✅ `src/db/chat_completion_requests_enhanced.py` - Enhanced tracking

### Medium Priority (Sync & Background)
7. ✅ `src/services/model_catalog_sync.py` - Model sync
8. ✅ `src/services/pricing_sync_background.py` - Pricing sync

### Low Priority (Testing & Docs)
9. ✅ `tests/` - Add new tests
10. ✅ `docs/` - Update documentation

---

## Appendix C: Compatibility Layer (Optional)

If backward compatibility is critical, we can create a temporary compatibility layer:

**Database View** (temporary):
```sql
CREATE OR REPLACE VIEW models_legacy_pricing AS
SELECT
    m.*,
    mp.price_per_input_token as pricing_prompt,
    mp.price_per_output_token as pricing_completion,
    mp.price_per_image_token as pricing_image,
    mp.price_per_request as pricing_request
FROM models m
LEFT JOIN model_pricing mp ON m.id = mp.model_id;
```

This view can be used during transition to maintain exact same field names, then dropped after full migration.

---

**Document Status**: Ready for Review
**Next Steps**:
1. Review and approve plan
2. Begin Phase 2 (code migration)
3. Create verification script
4. Run comprehensive testing
