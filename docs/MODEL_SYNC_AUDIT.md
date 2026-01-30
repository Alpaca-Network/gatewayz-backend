# Model Sync System Audit Report

**Generated:** 2026-01-28
**Purpose:** Audit model sync system for standardization and cleanup capabilities

---

## Executive Summary

This audit examined three key areas:
1. **Model Name Formatting** - How model names are stored and displayed
2. **Table Flush Capabilities** - Ability to reset/repopulate tables
3. **System Unification** - Consistency between DB population and cache systems

---

## 1. Model Name Formatting Audit

### Current Implementation

**Finding:** Model names are currently stored cleanly without company prefixes or type suffixes.

#### Evidence from Code Review:

**Good Examples (Current State):**
```python
# Cerebras
"name": "Llama 3.1 70B"
"name": "Qwen 3 32B"

# Nebius
"name": "DeepSeek R1 0528"
"name": "Llama 3.3 70B Instruct"

# Cloudflare Workers AI
"name": "GPT-OSS 120B"
"name": "Llama 4 Scout 17B"
```

**Generation Pattern:**
```python
# Most providers use this pattern:
display_name = (
    model.get("display_name")
    or model.get("name")
    or model_id.replace("-", " ").replace("_", " ").title()
)
```

### Data Structure

Models are stored with proper separation:
- `model_id`: Full identifier (e.g., `"openai/gpt-4"`, `"meta-llama/Llama-3.3-70B"`)
- `model_name`: Display name (e.g., `"GPT-4"`, `"Llama 3.3 70B"`)
- `provider_id`: Foreign key to providers table
- `metadata`: Additional info (JSON)

### Recommendation

✅ **Current format is correct** - No changes needed to name formatting logic.

If database contains malformed names (e.g., "Company : Model (Type)"), this is legacy data that should be cleaned up via a full resync.

---

## 2. Table Flush Capabilities Audit

### Current State

**Finding:** No endpoints exist to flush/reset tables.

### Required Capabilities

1. **Flush Models Table**
   - Endpoint: `DELETE /admin/model-sync/flush-models`
   - Action: Truncate models table
   - Safety: Requires confirmation parameter
   - Use case: Clean up before full resync

2. **Flush Providers Table (with CASCADE)**
   - Endpoint: `DELETE /admin/model-sync/flush-providers`
   - Action: Truncate providers table (cascades to models)
   - Safety: Requires double confirmation
   - Use case: Complete database reset

3. **Flush and Resync (Atomic Operation)**
   - Endpoint: `POST /admin/model-sync/reset-and-resync`
   - Action: Flush + repopulate in single operation
   - Safety: Transaction-based, rollback on failure
   - Use case: Production catalog refresh

### Database Schema Reference

```sql
-- From supabase/migrations/20251230000001_restore_dropped_tables.sql

CREATE TABLE IF NOT EXISTS "public"."providers" (
    "id" SERIAL PRIMARY KEY,
    "name" TEXT NOT NULL,
    "slug" TEXT NOT NULL UNIQUE,
    -- ... other fields
);

CREATE TABLE IF NOT EXISTS "public"."models" (
    "id" SERIAL PRIMARY KEY,
    "provider_id" INTEGER REFERENCES "public"."providers"("id") ON DELETE CASCADE,
    "model_id" TEXT NOT NULL,
    "model_name" TEXT NOT NULL,
    -- ... other fields
);
```

**Note:** CASCADE is already configured - deleting a provider automatically deletes its models.

---

## 3. System Unification Audit

### Current Architecture

**Finding:** Two parallel systems exist for model data:

#### System 1: Database Population (`models` table)
- **Location:** `src/services/model_catalog_sync.py`
- **Trigger:** `/admin/model-sync/full` endpoint
- **Process:**
  1. Fetches models from provider APIs
  2. Transforms to database schema
  3. Bulk upserts to `models` table
- **Functions:** `sync_provider_models()`, `transform_normalized_model_to_db_schema()`

#### System 2: Cache Population (Redis/in-memory)
- **Location:** `src/services/models.py`
- **Trigger:** API requests, startup
- **Process:**
  1. Fetches from database OR provider APIs
  2. Caches in Redis/memory
  3. Serves to frontend
- **Functions:** `fetch_models_from_*()`, `get_models_from_cache()`

### Unification Issues

#### Issue #1: Dual Fetch Logic
- Database sync uses `fetch_models_from_*()` functions
- Cache system ALSO uses `fetch_models_from_*()` functions
- **Problem:** Same functions used for two purposes

#### Issue #2: Transformation Divergence
- Database transform: `transform_normalized_model_to_db_schema()`
- Cache transform: Direct usage of normalized models
- **Problem:** Different schemas/fields may be exposed

#### Issue #3: Source of Truth Unclear
```python
# Sometimes reads from DB:
def get_model_by_id_from_db(model_id: str):
    return supabase.table('models').select('*').eq('model_id', model_id).execute()

# Sometimes fetches directly from providers:
def get_model_from_provider(provider_slug: str, model_id: str):
    models = fetch_models_from_{provider_slug}()
    return [m for m in models if m['id'] == model_id]
```

### Recommended Unified Architecture

```
┌──────────────────────────────────────────────────────┐
│  Provider APIs (OpenRouter, DeepInfra, etc.)        │
└────────────────┬─────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│  Fetch Layer: fetch_models_from_*() functions       │
│  - Returns normalized model data                     │
│  - Single source per provider                        │
└────────────────┬─────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│  Database: models table (Source of Truth)            │
│  - Persists all model data                           │
│  - Updated via sync operations                       │
└────────────────┬─────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│  Cache Layer: Redis + In-Memory                      │
│  - Reads from database (NOT provider APIs)           │
│  - Fast access for API requests                      │
│  - TTL-based expiration                              │
└──────────────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│  API Responses (to frontend/users)                   │
└──────────────────────────────────────────────────────┘
```

### Key Changes Required

1. **Cache should ONLY read from database**
   - Remove direct provider API calls from cache layer
   - Use `models` table as single source of truth

2. **Separate concerns**
   - Sync operations: Provider APIs → Database
   - Cache operations: Database → Cache → API

3. **Consistent transformation**
   - Use same schema for database and cache
   - Apply transformations once during sync

---

## 4. Existing Sync Endpoints

Currently available:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/model-sync/full` | POST | Sync providers + models |
| `/admin/model-sync/providers-only` | POST | Sync only providers |
| `/admin/model-sync/all` | POST | Sync models from all providers |
| `/admin/model-sync/provider/{slug}` | POST | Sync single provider models |
| `/admin/model-sync/providers` | GET | List available providers |
| `/admin/model-sync/status` | GET | Get sync statistics |

**Missing:**
- Flush/reset endpoints
- Atomic reset-and-resync operation

---

## 5. Recommendations Summary

### Immediate Actions (High Priority)

1. ✅ **Create flush endpoints**
   - `DELETE /admin/model-sync/flush-models`
   - `DELETE /admin/model-sync/flush-providers`
   - `POST /admin/model-sync/reset-and-resync`

2. ✅ **Standardize model_name field**
   - Run full resync to clean any malformed names
   - Ensure all fetch functions follow display_name pattern

3. ✅ **Unify cache and database systems**
   - Make database the single source of truth
   - Cache should only read from database
   - Remove duplicate provider API calls

### Medium Priority

4. Add validation to prevent malformed names during sync
5. Add audit logging for flush operations
6. Add backup/restore capabilities before flush

### Low Priority

7. Performance optimization for large-scale syncs
8. Incremental sync capabilities (only changed models)
9. Model versioning/history tracking

---

## 6. Implementation Plan

### Phase 1: Flush Endpoints (Week 1)
- Create safe flush endpoints with confirmations
- Add audit logging
- Test on staging environment

### Phase 2: System Unification (Week 2-3)
- Refactor cache layer to read from database
- Remove duplicate provider API calls
- Update documentation

### Phase 3: Validation & Safety (Week 4)
- Add name format validation
- Implement backup before flush
- Add rollback capabilities

---

## 7. Risk Assessment

### High Risk
- **Data Loss:** Flush operations are destructive
  - **Mitigation:** Require confirmation, add audit logs, implement backups

### Medium Risk
- **Downtime:** Resync operations take time
  - **Mitigation:** Run during low-traffic periods, use staging first

### Low Risk
- **Inconsistency:** Cache and DB might temporarily diverge
  - **Mitigation:** Implement cache invalidation on sync

---

## Appendix A: Provider Fetch Function Locations

All fetch functions in `src/services/`:
- `models.py`: Main fetch functions (20+ providers)
- `*_client.py`: Individual provider clients (30 files)
- `model_catalog_sync.py`: Sync orchestration

## Appendix B: Database Table Stats

From last check:
- Total providers: 35
- Providers with models: 20
- Providers without models: 15
- Total models: 2,283

## Appendix C: Related Files

- `/src/services/model_catalog_sync.py` - Sync service
- `/src/routes/model_sync.py` - API endpoints
- `/src/services/provider_model_sync_service.py` - Full sync orchestration
- `/src/db/models_catalog_db.py` - Database operations
- `/supabase/migrations/20251230000001_restore_dropped_tables.sql` - Schema

---

**End of Audit Report**
