# Canonical Models Schema Design

## Use Case

This schema is for tracking the **same model across multiple providers**.

**Example:**
- `gpt-4-turbo` is available from:
  - OpenRouter (as `openai/gpt-4-turbo`)
  - Portkey (as `gpt-4-turbo`)
  - Helicone (as `openai/gpt-4-turbo-preview`)

With canonical normalization, you can:
- Compare pricing across providers for the same model
- Show users all providers offering a specific model
- Track model metadata independently from provider-specific details

## Schema Design

```sql
-- ============================================================================
-- CANONICAL MODELS TABLE (The "real" model)
-- ============================================================================
CREATE TABLE canonical_models (
  id SERIAL PRIMARY KEY,
  canonical_name TEXT NOT NULL UNIQUE,  -- e.g., "gpt-4-turbo"
  canonical_slug TEXT NOT NULL UNIQUE,  -- e.g., "openai-gpt-4-turbo"
  family TEXT,                          -- e.g., "gpt-4"
  developer TEXT NOT NULL,              -- e.g., "openai"

  -- Model metadata (provider-independent)
  description TEXT,
  context_length INTEGER,
  modality TEXT DEFAULT 'text->text',
  architecture JSONB,

  -- Links and resources
  model_card_url TEXT,
  hugging_face_id TEXT,
  paper_url TEXT,

  -- Metadata
  release_date DATE,
  is_deprecated BOOLEAN DEFAULT false,
  deprecation_date DATE,
  metadata JSONB DEFAULT '{}'::jsonb,

  created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Indexes for canonical models
CREATE INDEX idx_canonical_models_developer ON canonical_models (developer);
CREATE INDEX idx_canonical_models_family ON canonical_models (family);
CREATE INDEX idx_canonical_models_modality ON canonical_models (modality);
CREATE INDEX idx_canonical_models_slug ON canonical_models (canonical_slug);

-- ============================================================================
-- PROVIDER MODEL INSTANCES (Provider-specific implementations)
-- ============================================================================
CREATE TABLE provider_model_instances (
  id SERIAL PRIMARY KEY,
  canonical_model_id INTEGER REFERENCES canonical_models(id) ON DELETE CASCADE,
  provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,

  -- Provider-specific identifiers
  provider_model_id TEXT NOT NULL,      -- Provider's ID for this model
  provider_model_name TEXT,             -- Provider's display name

  -- Provider-specific pricing
  pricing_prompt NUMERIC(20, 10),
  pricing_completion NUMERIC(20, 10),
  pricing_image NUMERIC(20, 10),
  pricing_request NUMERIC(20, 10),

  -- Provider-specific capabilities
  supports_streaming BOOLEAN DEFAULT false,
  supports_function_calling BOOLEAN DEFAULT false,
  supports_vision BOOLEAN DEFAULT false,

  -- Provider-specific performance
  average_response_time_ms INTEGER,
  health_status TEXT DEFAULT 'unknown' CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
  last_health_check_at TIMESTAMP WITH TIME ZONE,
  success_rate NUMERIC(5, 2),

  -- Provider-specific metadata
  is_active BOOLEAN DEFAULT true,
  per_request_limits JSONB,
  top_provider TEXT,                    -- If provider has sub-providers
  metadata JSONB DEFAULT '{}'::jsonb,

  created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

  -- Ensure unique model per provider
  CONSTRAINT unique_provider_model_instance UNIQUE (provider_id, provider_model_id)
);

-- Indexes for provider instances
CREATE INDEX idx_provider_instances_canonical ON provider_model_instances (canonical_model_id);
CREATE INDEX idx_provider_instances_provider ON provider_model_instances (provider_id);
CREATE INDEX idx_provider_instances_active ON provider_model_instances (is_active);
CREATE INDEX idx_provider_instances_health ON provider_model_instances (health_status);

-- Composite indexes for common queries
CREATE INDEX idx_provider_instances_lookup
ON provider_model_instances (canonical_model_id, provider_id, is_active);

CREATE INDEX idx_provider_instances_catalog
ON provider_model_instances (provider_id, is_active)
INCLUDE (provider_model_id, pricing_prompt, pricing_completion);

-- ============================================================================
-- VIEWS FOR BACKWARD COMPATIBILITY
-- ============================================================================
-- This view maintains compatibility with your existing `models` table structure

CREATE OR REPLACE VIEW models AS
SELECT
  pmi.id,
  pmi.provider_id,
  cm.canonical_name as model_id,           -- Use canonical name as model_id
  cm.canonical_name as model_name,
  pmi.provider_model_id,

  -- Canonical model fields
  cm.description,
  cm.context_length,
  cm.modality,
  cm.architecture,
  cm.hugging_face_id as top_provider,

  -- Provider-specific fields
  pmi.per_request_limits,
  pmi.pricing_prompt,
  pmi.pricing_completion,
  pmi.pricing_image,
  pmi.pricing_request,
  pmi.supports_streaming,
  pmi.supports_function_calling,
  pmi.supports_vision,
  pmi.average_response_time_ms,
  pmi.health_status,
  pmi.last_health_check_at,
  pmi.success_rate,
  pmi.is_active,

  -- Metadata
  pmi.metadata || cm.metadata as metadata,  -- Merge both metadata fields
  pmi.created_at,
  pmi.updated_at
FROM provider_model_instances pmi
LEFT JOIN canonical_models cm ON pmi.canonical_model_id = cm.id;

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Get all providers offering a canonical model
CREATE OR REPLACE FUNCTION get_providers_for_model(canonical_model_name TEXT)
RETURNS TABLE (
  provider_name TEXT,
  provider_slug TEXT,
  pricing_prompt NUMERIC,
  pricing_completion NUMERIC,
  is_active BOOLEAN
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    p.name,
    p.slug,
    pmi.pricing_prompt,
    pmi.pricing_completion,
    pmi.is_active
  FROM canonical_models cm
  JOIN provider_model_instances pmi ON cm.id = pmi.canonical_model_id
  JOIN providers p ON pmi.provider_id = p.id
  WHERE cm.canonical_name = canonical_model_name
    AND pmi.is_active = true
  ORDER BY pmi.pricing_prompt ASC NULLS LAST;
END;
$$ LANGUAGE plpgsql;

-- Find cheapest provider for a model
CREATE OR REPLACE FUNCTION get_cheapest_provider_for_model(canonical_model_name TEXT)
RETURNS TABLE (
  provider_name TEXT,
  provider_slug TEXT,
  total_cost NUMERIC
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    p.name,
    p.slug,
    (pmi.pricing_prompt + pmi.pricing_completion) as total_cost
  FROM canonical_models cm
  JOIN provider_model_instances pmi ON cm.id = pmi.canonical_model_id
  JOIN providers p ON pmi.provider_id = p.id
  WHERE cm.canonical_name = canonical_model_name
    AND pmi.is_active = true
    AND pmi.pricing_prompt IS NOT NULL
    AND pmi.pricing_completion IS NOT NULL
  ORDER BY (pmi.pricing_prompt + pmi.pricing_completion) ASC
  LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- Get all instances of a canonical model
CREATE OR REPLACE FUNCTION get_model_instances(canonical_model_name TEXT)
RETURNS SETOF provider_model_instances AS $$
BEGIN
  RETURN QUERY
  SELECT pmi.*
  FROM canonical_models cm
  JOIN provider_model_instances pmi ON cm.id = pmi.canonical_model_id
  WHERE cm.canonical_name = canonical_model_name;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- MIGRATION FROM CURRENT SCHEMA
-- ============================================================================

-- Step 1: Extract unique canonical models from existing data
INSERT INTO canonical_models (canonical_name, canonical_slug, developer, description, context_length, modality, architecture)
SELECT DISTINCT ON (model_id)
  model_id as canonical_name,
  lower(regexp_replace(model_id, '[^a-zA-Z0-9]+', '-', 'g')) as canonical_slug,
  split_part(model_id, '/', 1) as developer,
  description,
  context_length,
  modality,
  architecture
FROM models
ORDER BY model_id, created_at DESC;

-- Step 2: Migrate existing models to provider_model_instances
INSERT INTO provider_model_instances (
  canonical_model_id,
  provider_id,
  provider_model_id,
  provider_model_name,
  pricing_prompt,
  pricing_completion,
  pricing_image,
  pricing_request,
  supports_streaming,
  supports_function_calling,
  supports_vision,
  average_response_time_ms,
  health_status,
  last_health_check_at,
  success_rate,
  is_active,
  per_request_limits,
  metadata,
  created_at,
  updated_at
)
SELECT
  cm.id as canonical_model_id,
  m.provider_id,
  m.provider_model_id,
  m.model_name as provider_model_name,
  m.pricing_prompt,
  m.pricing_completion,
  m.pricing_image,
  m.pricing_request,
  m.supports_streaming,
  m.supports_function_calling,
  m.supports_vision,
  m.average_response_time_ms,
  m.health_status,
  m.last_health_check_at,
  m.success_rate,
  m.is_active,
  m.per_request_limits,
  m.metadata,
  m.created_at,
  m.updated_at
FROM models m
JOIN canonical_models cm ON m.model_id = cm.canonical_name;

-- Step 3: Rename old table for backup
ALTER TABLE models RENAME TO models_old_backup;

-- The view `models` now acts as the new models table

-- ============================================================================
-- EXAMPLE QUERIES
-- ============================================================================

-- Get all providers offering GPT-4 Turbo
SELECT * FROM get_providers_for_model('openai/gpt-4-turbo');

-- Find cheapest provider for Claude 3
SELECT * FROM get_cheapest_provider_for_model('anthropic/claude-3-opus');

-- Compare pricing across providers
SELECT
  cm.canonical_name,
  p.name as provider,
  pmi.pricing_prompt,
  pmi.pricing_completion,
  (pmi.pricing_prompt + pmi.pricing_completion) as total_cost
FROM canonical_models cm
JOIN provider_model_instances pmi ON cm.id = pmi.canonical_model_id
JOIN providers p ON pmi.provider_id = p.id
WHERE cm.canonical_name = 'openai/gpt-4-turbo'
  AND pmi.is_active = true
ORDER BY total_cost ASC;

-- Get all GPT-4 family models
SELECT * FROM canonical_models WHERE family = 'gpt-4' ORDER BY canonical_name;

-- Get model with all provider instances
SELECT
  cm.canonical_name,
  cm.developer,
  json_agg(
    json_build_object(
      'provider', p.name,
      'provider_model_id', pmi.provider_model_id,
      'pricing_prompt', pmi.pricing_prompt,
      'pricing_completion', pmi.pricing_completion,
      'is_active', pmi.is_active
    )
  ) as instances
FROM canonical_models cm
LEFT JOIN provider_model_instances pmi ON cm.id = pmi.canonical_model_id
LEFT JOIN providers p ON pmi.provider_id = p.id
WHERE cm.canonical_name = 'openai/gpt-4-turbo'
GROUP BY cm.id, cm.canonical_name, cm.developer;
```

## When to Use This Schema

✅ **Use canonical normalization if:**
- You need to compare the same model across providers
- You want to show users "Model X is available from Y providers"
- You need to track model metadata independently from providers
- You want pricing comparison features
- You have many duplicate models across providers

❌ **Don't use it if:**
- You only query one provider at a time
- Models from different providers are actually different (even with same name)
- You don't need cross-provider analytics
- The complexity isn't worth it for your use case

## Performance Considerations

**Pros:**
- Faster queries for "show all providers for model X"
- Better data integrity (single source of truth for model metadata)
- Easier to maintain canonical model information

**Cons:**
- One extra JOIN for most queries (canonical_models → provider_instances)
- More complex upsert logic
- Migration complexity

**Optimization:**
- The view `models` provides backward compatibility
- Covering indexes minimize JOIN overhead
- Denormalize frequently accessed fields if needed

## Alternative: Add Canonical ID to Existing Schema

If full normalization is too complex, just add a canonical_model_id column:

```sql
ALTER TABLE models ADD COLUMN canonical_model_id TEXT;

-- Populate with normalized model names
UPDATE models SET canonical_model_id =
  regexp_replace(
    lower(split_part(model_id, ':', 1)),  -- Take first part before version
    '[^a-z0-9/]', '-', 'g'
  );

-- Index for grouping
CREATE INDEX idx_models_canonical ON models (canonical_model_id);

-- Query: Get all providers offering a model
SELECT
  m.canonical_model_id,
  p.name as provider,
  m.pricing_prompt,
  m.pricing_completion
FROM models m
JOIN providers p ON m.provider_id = p.id
WHERE m.canonical_model_id = 'openai/gpt-4'
  AND m.is_active = true
ORDER BY (m.pricing_prompt + m.pricing_completion) ASC;
```

This gives you 80% of the benefits with 20% of the complexity.
