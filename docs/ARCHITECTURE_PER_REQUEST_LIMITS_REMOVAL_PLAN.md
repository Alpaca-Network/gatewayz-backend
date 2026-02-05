# Architecture & Per Request Limits Removal Plan

## Overview

Plan to remove redundant `architecture` and `per_request_limits` columns from the models table.

---

## Current Situation

### Architecture Column
- **Type:** TEXT (stores JSON string) or JSONB
- **Usage:** Extract modality and capabilities in `model_catalog_sync.py`
- **Current approach:** Dedicated column
- **Proposed approach:** Store in `metadata` JSONB column

### Per Request Limits Column
- **Type:** JSONB
- **Usage:** Always set to `None` in all normalization functions
- **Current approach:** Dedicated column (unused)
- **Proposed approach:** Remove entirely (not used anywhere)

---

## Verification Needed

Run this SQL script to verify:

```sql
-- Check current usage
SELECT
    COUNT(*) as total_models,
    COUNT(architecture) as non_null_architecture,
    COUNT(per_request_limits) as non_null_per_request_limits,
    COUNT(metadata->'architecture') as architecture_in_metadata
FROM models;
```

**Script location:** `scripts/check_architecture_per_request_limits.sql`

---

## Migration Strategy

### Phase 1: Architecture Column

**Step 1: Migrate data to metadata (if not already there)**
```sql
-- Copy architecture to metadata
UPDATE models
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{architecture}',
    to_jsonb(architecture::jsonb)
)
WHERE architecture IS NOT NULL
  AND (metadata->'architecture' IS NULL OR metadata->'architecture' = 'null'::jsonb);
```

**Step 2: Update code to read from metadata**
- Update `src/services/model_catalog_sync.py`:
  - Change `model.get("architecture")` → `model.get("metadata", {}).get("architecture")`

**Step 3: Drop column**
```sql
ALTER TABLE models DROP COLUMN IF EXISTS architecture CASCADE;
```

### Phase 2: Per Request Limits Column

**This is simpler since it's always NULL:**

```sql
-- Verify all are NULL
SELECT COUNT(*) FROM models WHERE per_request_limits IS NOT NULL;

-- Drop if count is 0
ALTER TABLE models DROP COLUMN IF EXISTS per_request_limits CASCADE;
```

---

## Code Changes Required

### 1. Schema Updates

**File:** `src/schemas/models_catalog.py`

Remove:
```python
architecture: str | None = Field(None, description="Model architecture")
per_request_limits: dict[str, Any] | None = Field(None, description="Per-request limits")
```

### 2. Sync Logic Updates

**File:** `src/services/model_catalog_sync.py`

**Before:**
```python
def extract_modality(model: dict[str, Any]) -> str:
    architecture = model.get("architecture")
    if isinstance(architecture, dict):
        modality = architecture.get("modality")
        if modality:
            return modality
```

**After:**
```python
def extract_modality(model: dict[str, Any]) -> str:
    # Check metadata.architecture field
    metadata = model.get("metadata", {})
    architecture = metadata.get("architecture") or model.get("architecture")
    if isinstance(architecture, dict):
        modality = architecture.get("modality")
        if modality:
            return modality
```

### 3. Remove Hardcoded Nulls

**File:** `src/services/models.py` and all provider clients

Remove all instances of:
```python
"per_request_limits": None,
```

From all normalization functions.

---

## Benefits

1. **Simplified schema** - 2 fewer columns
2. **Better data organization** - Architecture data belongs in metadata
3. **Reduced maintenance** - No need to sync two places for architecture data
4. **Cleaner code** - Remove unused `per_request_limits` assignments

---

## Risk Assessment

### Architecture Column
- **Risk:** Medium
- **Reason:** Actually used for extracting modality/capabilities
- **Mitigation:**
  1. Migrate data to metadata first
  2. Update code to check both locations (backwards compatible)
  3. Test thoroughly before dropping column

### Per Request Limits Column
- **Risk:** Low
- **Reason:** Always NULL, never used
- **Mitigation:** Simple verification query to confirm

---

## Recommended Approach

**Option A: Conservative (Recommended)**
1. First verify data with SQL script
2. Migrate architecture to metadata
3. Update code to read from both locations (fallback)
4. Deploy and test
5. After 1 week of stable operation, drop columns

**Option B: Aggressive**
1. Verify data
2. Update code directly
3. Drop columns
4. Deploy all at once

**I recommend Option A for safety.**

---

## Next Steps

1. **Run verification script** to understand current data
2. **Review results** and confirm removal is safe
3. **Choose approach** (Option A or B)
4. **Execute migration** following the chosen plan

---

## Questions to Answer

Before proceeding, verify:

- [ ] Is architecture data already in metadata JSONB?
- [ ] Are there any views/functions depending on these columns?
- [ ] Is per_request_limits truly always NULL?
- [ ] Is architecture data actually used anywhere else?

Run the verification SQL script to answer these questions!
