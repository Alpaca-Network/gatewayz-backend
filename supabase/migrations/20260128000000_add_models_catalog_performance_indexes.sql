-- Migration: Add performance indexes for database-first model catalog (Issue #980)
-- Date: 2026-01-28
-- Purpose: Optimize database queries for catalog building when using database as single source of truth
--
-- Background:
-- Previously, the catalog system called provider APIs directly. Now it reads from the database,
-- so we need indexes to ensure fast query performance.
--
-- Expected improvements:
-- - Provider-specific lookups: ~100ms → ~10ms
-- - Active model filtering: ~200ms → ~20ms
-- - Model ID lookups: ~50ms → ~5ms

-- ============================================================================
-- Index 1: Provider + Active Status (Most Common Query Pattern)
-- ============================================================================
-- This index supports queries like:
--   SELECT * FROM models WHERE provider_id = X AND is_active = true
--
-- Used by: get_models_by_gateway_for_catalog()
-- Estimated usage: 80% of catalog requests (single-gateway queries)
CREATE INDEX IF NOT EXISTS idx_models_provider_active
ON models(provider_id, is_active)
WHERE is_active = true;

-- Analyze index
COMMENT ON INDEX idx_models_provider_active IS
'Optimizes provider-specific catalog queries with active filter (Issue #980)';

-- ============================================================================
-- Index 2: Model ID Lookups (Routing & Availability Checks)
-- ============================================================================
-- This index supports queries like:
--   SELECT * FROM models WHERE model_id = 'gpt-4'
--
-- Used by: get_model_by_model_id_string(), model routing, availability checks
-- Estimated usage: 20% of requests (individual model lookups)
CREATE INDEX IF NOT EXISTS idx_models_model_id
ON models(model_id);

-- Analyze index
COMMENT ON INDEX idx_models_model_id IS
'Optimizes model_id string lookups for routing and availability (Issue #980)';

-- ============================================================================
-- Index 3: Provider Model ID + Provider ID (Uniqueness & Sync)
-- ============================================================================
-- This index supports queries like:
--   SELECT * FROM models WHERE provider_id = X AND provider_model_id = 'gpt-4'
--
-- Used by: upsert operations during sync, duplicate detection
-- Note: This is also the unique constraint for the table
CREATE INDEX IF NOT EXISTS idx_models_provider_model_id
ON models(provider_id, provider_model_id);

-- Analyze index
COMMENT ON INDEX idx_models_provider_model_id IS
'Optimizes sync upsert operations and duplicate detection (Issue #980)';

-- ============================================================================
-- Index 4: Health Status (Health Monitoring Queries)
-- ============================================================================
-- This index supports queries like:
--   SELECT * FROM models WHERE health_status = 'down' AND is_active = true
--
-- Used by: health monitoring dashboards, alerting systems
-- Estimated usage: Admin/monitoring endpoints
CREATE INDEX IF NOT EXISTS idx_models_health_status
ON models(health_status, is_active)
WHERE is_active = true;

-- Analyze index
COMMENT ON INDEX idx_models_health_status IS
'Optimizes health monitoring queries (Issue #980)';

-- ============================================================================
-- Index 5: Model Name (Search & Sorting)
-- ============================================================================
-- This index supports queries like:
--   SELECT * FROM models ORDER BY model_name
--   SELECT * FROM models WHERE model_name ILIKE '%gpt%'
--
-- Used by: catalog sorting, model search
-- Note: Using gin_trgm_ops for trigram similarity search (requires pg_trgm extension)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_models_model_name_trgm
ON models USING gin (model_name gin_trgm_ops);

-- Analyze index
COMMENT ON INDEX idx_models_model_name_trgm IS
'Optimizes model name search with trigram similarity (Issue #980)';

-- Also add regular B-tree index for ORDER BY operations
CREATE INDEX IF NOT EXISTS idx_models_model_name
ON models(model_name);

COMMENT ON INDEX idx_models_model_name IS
'Optimizes model name sorting in catalog (Issue #980)';

-- ============================================================================
-- Index 6: Modality Filtering (Text, Image, Audio, etc.)
-- ============================================================================
-- This index supports queries like:
--   SELECT * FROM models WHERE modality = 'text' AND is_active = true
--
-- Used by: modality-specific catalog filtering
CREATE INDEX IF NOT EXISTS idx_models_modality
ON models(modality, is_active)
WHERE is_active = true;

-- Analyze index
COMMENT ON INDEX idx_models_modality IS
'Optimizes modality filtering in catalog (Issue #980)';

-- ============================================================================
-- Performance Statistics & Validation
-- ============================================================================

-- Analyze tables to update statistics for query planner
ANALYZE models;
ANALYZE providers;

-- Log migration completion
DO $$
BEGIN
    RAISE NOTICE 'Migration 20260128000000_add_models_catalog_performance_indexes.sql completed';
    RAISE NOTICE 'Added 7 indexes for database-first catalog optimization (Issue #980)';
    RAISE NOTICE 'Indexes created:';
    RAISE NOTICE '  - idx_models_provider_active (provider + active filtering)';
    RAISE NOTICE '  - idx_models_model_id (model_id lookups)';
    RAISE NOTICE '  - idx_models_provider_model_id (sync operations)';
    RAISE NOTICE '  - idx_models_health_status (health monitoring)';
    RAISE NOTICE '  - idx_models_model_name_trgm (name search with trigrams)';
    RAISE NOTICE '  - idx_models_model_name (name sorting)';
    RAISE NOTICE '  - idx_models_modality (modality filtering)';
END $$;
