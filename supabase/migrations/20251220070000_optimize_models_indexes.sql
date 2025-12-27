-- Migration: Optimize models table indexes for better query performance
-- Created: 2025-12-20
-- Description: Add covering indexes and partial indexes to speed up common queries
--              DO NOT split the table - that would hurt performance!

-- ============================================================================
-- DROP REDUNDANT INDEXES (if they exist)
-- ============================================================================
-- The basic single-column indexes might be redundant if we have better composite ones
-- Only drop if you're sure they're not being used heavily (check with analyze script first)

-- ============================================================================
-- PARTIAL INDEXES FOR ACTIVE MODELS
-- ============================================================================
-- Most queries filter for is_active = true, so create partial indexes
-- These are smaller and faster than full indexes

-- Partial index for active models ordered by name (common listing query)
CREATE INDEX IF NOT EXISTS idx_models_active_name
ON models (model_name)
WHERE is_active = true;

-- Partial index for active models by provider (common filtering)
CREATE INDEX IF NOT EXISTS idx_models_active_provider
ON models (provider_id, model_name)
WHERE is_active = true;

-- Partial index for active models by modality (for filtering by type)
CREATE INDEX IF NOT EXISTS idx_models_active_modality
ON models (modality, model_name)
WHERE is_active = true;

-- ============================================================================
-- COVERING INDEXES (INCLUDE columns to avoid table lookups)
-- ============================================================================
-- These indexes include frequently accessed columns to satisfy queries without
-- hitting the main table (index-only scans)

-- For catalog listing with pricing info (most common API query)
-- Covers: provider filter + active filter + common fields
CREATE INDEX IF NOT EXISTS idx_models_catalog_covering
ON models (provider_id, is_active, model_name)
INCLUDE (model_id, provider_model_id, pricing_prompt, pricing_completion, context_length, modality);

-- For search queries that need quick model lookups
CREATE INDEX IF NOT EXISTS idx_models_search_covering
ON models (model_id)
INCLUDE (provider_id, model_name, is_active, pricing_prompt, pricing_completion);

-- ============================================================================
-- INDEXES FOR SEARCH PATTERNS
-- ============================================================================
-- Pattern matching on model_id (for API routes like /models/{provider}/{model})

-- Trigram index for LIKE/ILIKE queries on model_id (better than standard B-tree)
-- Requires pg_trgm extension
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Trigram index for fast fuzzy search on model_id
CREATE INDEX IF NOT EXISTS idx_models_model_id_trgm
ON models USING gin (model_id gin_trgm_ops);

-- Trigram index for model_name search
CREATE INDEX IF NOT EXISTS idx_models_model_name_trgm
ON models USING gin (model_name gin_trgm_ops);

-- ============================================================================
-- INDEXES FOR SORTING
-- ============================================================================
-- Common sort patterns from the API

-- For sorting by pricing (cheapest models)
CREATE INDEX IF NOT EXISTS idx_models_by_price
ON models (pricing_prompt, pricing_completion)
WHERE is_active = true AND pricing_prompt IS NOT NULL;

-- For sorting by context length (largest context windows)
CREATE INDEX IF NOT EXISTS idx_models_by_context
ON models (context_length DESC)
WHERE is_active = true AND context_length IS NOT NULL;

-- ============================================================================
-- COMPOSITE INDEX FOR JOINS
-- ============================================================================
-- Optimize the common join with providers table
-- The FK index on provider_id should already exist, but ensure it does
CREATE INDEX IF NOT EXISTS idx_models_provider_join
ON models (provider_id, is_active);

-- ============================================================================
-- FULL-TEXT SEARCH (OPTIONAL - if you do description searches)
-- ============================================================================
-- Only create this if you frequently search in descriptions
-- Otherwise it wastes space

-- Add tsvector column for full-text search
ALTER TABLE models ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Create GIN index for full-text search
CREATE INDEX IF NOT EXISTS idx_models_search_vector
ON models USING gin(search_vector);

-- Function to update search vector
CREATE OR REPLACE FUNCTION models_search_vector_update()
RETURNS trigger AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('english', COALESCE(NEW.model_name, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.model_id, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'C');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to maintain search vector
DROP TRIGGER IF EXISTS models_search_vector_trigger ON models;
CREATE TRIGGER models_search_vector_trigger
BEFORE INSERT OR UPDATE ON models
FOR EACH ROW
EXECUTE FUNCTION models_search_vector_update();

-- Populate search vector for existing rows
UPDATE models SET search_vector =
  setweight(to_tsvector('english', COALESCE(model_name, '')), 'A') ||
  setweight(to_tsvector('english', COALESCE(model_id, '')), 'B') ||
  setweight(to_tsvector('english', COALESCE(description, '')), 'C')
WHERE search_vector IS NULL;

-- ============================================================================
-- STATISTICS UPDATE
-- ============================================================================
-- Update table statistics for better query planning
ANALYZE models;

-- ============================================================================
-- USAGE NOTES
-- ============================================================================
-- After applying this migration:
--
-- 1. Monitor index usage with:
--    SELECT * FROM pg_stat_user_indexes WHERE tablename = 'models';
--
-- 2. Drop unused indexes after 1 week:
--    SELECT indexname, idx_scan FROM pg_stat_user_indexes
--    WHERE tablename = 'models' AND idx_scan = 0;
--
-- 3. For text search, use:
--    SELECT * FROM models WHERE search_vector @@ to_tsquery('gpt & turbo');
--
-- 4. Check query performance with:
--    EXPLAIN (ANALYZE, BUFFERS) SELECT ... FROM models WHERE ...;
