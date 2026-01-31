# Model ID Deprecation Plan

## Verification Results ✅

The SQL verification confirmed:
- `model_id` and `model_name` are functionally equivalent
- No multi-provider grouping discrepancies
- Safe to migrate from `model_id` to `model_name`

## Migration Strategy

### Phase 1: Code Migration (Update all references)
Replace all `model_id` references with `model_name` throughout the codebase.

### Phase 2: Database Migration (Drop column)
Create migration to remove the `model_id` column from the models table.

---

## Phase 1: Code Changes Required

### Files to Update

#### 1. Database Layer (`src/db/`)
- `src/db/models_catalog_db.py` - Update all queries using `model_id`

#### 2. Services Layer (`src/services/`)
- `src/services/models.py` - Update model fetching/transformation
- `src/services/model_transformations.py` - Update model ID transformations
- `src/services/model_catalog_sync.py` - Update sync logic
- `src/services/failover_service.py` - Update failover queries
- `src/services/provider_failover.py` - Update provider failover logic
- `src/services/canonical_registry.py` - Update canonical model registry
- `src/services/multi_provider_registry.py` - Update multi-provider logic
- All provider client files - Update model ID extraction

#### 3. Routes Layer (`src/routes/`)
- `src/routes/catalog.py` - Update catalog endpoints
- `src/routes/models_catalog_management.py` - Update management endpoints
- Any other routes that reference `model_id`

#### 4. Schemas (`src/schemas/`)
- `src/schemas/models_catalog.py` - Update Pydantic schemas

#### 5. Analytics & Monitoring
- Update any GROUP BY `model_id` queries to use `model_name`
- Update any analytics that track by `model_id`

---

## Phase 2: Database Migration

### Migration SQL

```sql
-- Drop model_id column from models table
ALTER TABLE "public"."models" DROP COLUMN IF EXISTS "model_id";
```

### Migration File
Create: `supabase/migrations/[timestamp]_drop_model_id_column.sql`

---

## Testing Checklist

- [ ] All unit tests pass
- [ ] Integration tests pass
- [ ] Failover queries work correctly with `model_name`
- [ ] Multi-provider model grouping works
- [ ] Analytics dashboards still function
- [ ] API endpoints return correct data
- [ ] No references to `model_id` remain in code

---

## Rollback Plan

If issues are discovered:

1. **Before dropping column**: Simply revert code changes
2. **After dropping column**: Restore column and re-populate from `model_name`

---

## Implementation Order

1. ✅ Verify `model_id` is redundant (COMPLETED)
2. 🔄 Update all code references (IN PROGRESS)
3. ⏳ Test changes thoroughly
4. ⏳ Create database migration
5. ⏳ Deploy to production
6. ⏳ Monitor for issues
7. ⏳ Drop column after 1 week of stable operation

---

## Notes

- `model_name` is now the canonical identifier for grouping models across providers
- `provider_model_id` remains for API-specific model identifiers
- This simplifies the data model and removes redundancy
