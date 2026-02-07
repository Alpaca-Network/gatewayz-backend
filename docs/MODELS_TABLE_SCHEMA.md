# Models Table Schema - Current State

**Last Updated:** 2026-02-07
**Migration Reference:** `20260131000002_drop_model_id_column.sql`

## Current Schema

The `models` table structure after recent migrations:

### Primary Identifiers
- **`id`** (SERIAL PRIMARY KEY) - Database auto-increment primary key
- **`provider_id`** (INTEGER NOT NULL) - Foreign key to `providers(id)`
- **`model_name`** (TEXT NOT NULL) - **Common display name** (NOT unique)
  - Human-readable name like "GPT-4", "Claude 3 Opus", "Llama 3.1 70B"
  - **Can be duplicate across providers** (e.g., multiple providers may offer "GPT-4")
  - Used for display purposes, NOT for uniqueness
- **`provider_model_id`** (TEXT NOT NULL) - **Provider-specific unique identifier**
  - This is what the provider's API expects (e.g., "openai/gpt-4", "gemini-1.5-pro-preview")
  - **MUST be unique per provider** (enforced by unique constraint on `provider_id, provider_model_id`)
  - Used for API calls to the provider

### ❌ Deprecated/Removed Fields

These fields have been removed and should NOT be used:

- **`model_id`** (TEXT) - **DROPPED** in migration `20260131000002`
  - Was redundant with `model_name`
  - Replaced by: Use `model_name` for canonical identification
  - Any code using `model_id` will fail with: `record "new" has no field "model_id"`

- **`architecture`** (TEXT) - **DROPPED** in migration `20260131000005`
  - Moved to `metadata.architecture` (JSONB)
  - More flexible storage in metadata

- **Pricing columns** - **DROPPED** in migration `20260121000003`
  - `pricing_prompt`, `pricing_completion`, `pricing_image`, `pricing_request`
  - Moved to separate `model_pricing` table
  - Stored temporarily in `metadata.pricing_raw` during sync

### Current Fields

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `id` | SERIAL | NO | Primary key |
| `provider_id` | INTEGER | NO | FK to providers table |
| `model_name` | TEXT | NO | Canonical model identifier |
| `provider_model_id` | TEXT | NO | Provider-specific identifier |
| `description` | TEXT | YES | Model description |
| `context_length` | INTEGER | YES | Maximum context window size |
| `modality` | TEXT | YES | Model modality (default: 'text->text') |
| `top_provider` | TEXT | YES | Recommended provider for this model |
| `per_request_limits` | JSONB | YES | Request rate limits |
| `supports_streaming` | BOOLEAN | YES | Streaming support flag |
| `supports_function_calling` | BOOLEAN | YES | Function calling support |
| `supports_vision` | BOOLEAN | YES | Vision/image input support |
| `average_response_time_ms` | INTEGER | YES | Average response time |
| `health_status` | TEXT | YES | Health status (healthy/degraded/down/unknown) |
| `last_health_check_at` | TIMESTAMPTZ | YES | Last health check timestamp |
| `success_rate` | NUMERIC(5,2) | YES | Success rate percentage |
| `is_active` | BOOLEAN | YES | Active status (default: true) |
| `metadata` | JSONB | YES | Additional metadata (default: {}) |
| `created_at` | TIMESTAMPTZ | YES | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | YES | Last update timestamp |

### Unique Constraints

- **`unique_provider_model`**: UNIQUE(`provider_id`, `provider_model_id`)
  - Ensures each provider can only have one entry per provider_model_id
  - Used in UPSERT operations with `on_conflict="provider_id,provider_model_id"`

### Indexes

- `idx_models_provider_id` - On `provider_id`
- `idx_models_provider_model_id` - On `provider_model_id`
- `idx_models_is_active` - On `is_active`
- `idx_models_health_status` - On `health_status`
- `idx_models_modality` - On `modality`
- `idx_models_provider_active` - Composite on (`provider_id`, `is_active`)

## Code Usage Guidelines

### ✅ Correct Usage

```python
# Building model data for insert/upsert
model_data = {
    "provider_id": provider_id,
    "model_name": "GPT-4",  # Display name (can be duplicate across providers)
    "provider_model_id": "openai/gpt-4",  # UNIQUE per provider (what the API expects)
    "description": "GPT-4 model",
    "context_length": 8192,
    "modality": "text->text",
    "supports_streaming": True,
    "is_active": True,
    "metadata": {
        "architecture": {...},
        "pricing_raw": {...}
    }
}
```

### Field Extraction Priority (from provider API responses)

When transforming provider API responses to database schema:

```python
# Extract provider_model_id (UNIQUE identifier - what the provider API uses)
# This MUST be unique per provider and is used for API calls
provider_model_id = (
    normalized_model.get("provider_model_id")  # Explicit if set
    or normalized_model.get("id")  # Most common (OpenRouter, Anthropic, etc.)
    or normalized_model.get("slug")  # Some providers use "slug"
)

# Extract model_name (common display name - can be duplicate)
# This is just for human-readable display, NOT for uniqueness
model_name = (
    normalized_model.get("name")  # Best: explicit display name
    or normalized_model.get("model_id")  # Legacy field (being phased out)
    or normalized_model.get("id")  # Fallback: use provider ID if no display name
)
```

### Examples of Correct Mapping

| Provider | API Response `"id"` | API Response `"name"` | `provider_model_id` | `model_name` |
|----------|---------------------|----------------------|---------------------|--------------|
| OpenRouter | `"openai/gpt-4"` | `"GPT-4"` | `"openai/gpt-4"` | `"GPT-4"` |
| Google Vertex | `"gemini-1.5-pro-preview"` | `"Gemini 1.5 Pro"` | `"gemini-1.5-pro-preview"` | `"Gemini 1.5 Pro"` |
| Anthropic | `"claude-3-opus-20240229"` | `"Claude 3 Opus"` | `"claude-3-opus-20240229"` | `"Claude 3 Opus"` |
| DeepInfra | `"meta-llama/Llama-3-70b"` | `"Llama 3 70B"` | `"meta-llama/Llama-3-70b"` | `"Llama 3 70B"` |

**Important Notes:**
- Multiple providers can have the same `model_name` (e.g., "GPT-4" from OpenRouter, Helicone, etc.)
- Each `provider_model_id` must be unique within that provider (enforced by database constraint)
- When making API calls, always use `provider_model_id`, never `model_name`

### ❌ Incorrect Usage

```python
# DON'T DO THIS - model_id field was removed!
model_data = {
    "provider_id": provider_id,
    "model_id": "gpt-4",  # ❌ This field doesn't exist anymore!
    "model_name": "gpt-4",
    "provider_model_id": "openai/gpt-4"
}
```

### Safe Upsert Pattern

The database layer now automatically filters out `model_id` if present:

```python
# In src/db/models_catalog_db.py
def bulk_upsert_models(models_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Auto-removes model_id if accidentally included
    cleaned_models = []
    for model in serialized_models:
        cleaned_model = {k: v for k, v in model.items() if k != "model_id"}
        cleaned_models.append(cleaned_model)

    response = supabase.table("models").upsert(
        cleaned_models,
        on_conflict="provider_id,provider_model_id"
    ).execute()
```

## Migration History

1. **20251121000000** - Initial creation with `model_id`, `model_name`, `provider_model_id`
2. **20260131000002** - Dropped `model_id` column (redundant with `model_name`)
3. **20260131000004** - Migrated `architecture` to `metadata.architecture`
4. **20260131000005** - Dropped `architecture` column
5. **20260121000003** - Removed pricing columns (moved to `model_pricing` table)

## Troubleshooting

### Error: `record "new" has no field "model_id"`

**Cause:** Code is trying to insert/update a record with `model_id` field

**Solution:**
1. Check your code - remove any references to `model_id` field
2. Use `model_name` instead for canonical identification
3. Use `provider_model_id` for provider-specific identification
4. The database layer now auto-filters `model_id`, but fix the source

### Querying Models

```python
# Get model by canonical name
models = supabase.table("models").select("*").eq("model_name", "gpt-4").execute()

# Get model by provider-specific ID
model = supabase.table("models").select("*")\
    .eq("provider_id", provider_id)\
    .eq("provider_model_id", "openai/gpt-4")\
    .single().execute()
```

## Related Tables

- **`providers`** - Parent table (joined on `provider_id`)
- **`model_pricing`** - Pricing data (separate table as of 2026-01-21)
- **`model_health_history`** - Health check history (uses `models.id` FK)
- **`unique_models`** - Aggregated view grouping by `model_name`
- **`unique_models_provider`** - Junction table linking unique models to providers

## Summary

**Key Changes:**
- ✅ Use `model_name` for canonical identification
- ✅ Use `provider_model_id` for provider-specific identification
- ❌ Don't use `model_id` (removed)
- ❌ Don't use `architecture` column (moved to metadata)
- ❌ Don't use pricing columns (moved to separate table)
