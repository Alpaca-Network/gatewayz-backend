-- Migration: Add missing indexes for common models catalog filter patterns (DB-M7)
-- Date: 2026-02-18
-- Purpose: Cover query patterns in models_catalog_db.py that lack dedicated indexes
--
-- Patterns addressed:
--   1. is_active + model_name ORDER BY  → get_all_models_for_catalog(), get_all_models()
--   2. provider_id + is_active + model_name ORDER BY  → get_models_by_provider_slug(),
--                                                        get_models_by_gateway_for_catalog(),
--                                                        get_models_for_catalog_with_filters()
--   3. model_health_history(provider, model, checked_at DESC)  → get_model_health_history()

-- ============================================================================
-- Index 1: Active status + model_name (supports filtered full-catalog scans)
-- ============================================================================
-- Supports queries like:
--   SELECT * FROM models WHERE is_active = true ORDER BY model_name
--
-- Used by: get_all_models_for_catalog(), get_all_models(), get_catalog_statistics()
-- The existing idx_models_is_active covers filtering alone, but adding model_name
-- allows the planner to satisfy ORDER BY from the index without a filesort.
CREATE INDEX IF NOT EXISTS idx_models_active_model_name
ON models (is_active, model_name)
WHERE is_active = true;

COMMENT ON INDEX idx_models_active_model_name IS
'Optimizes full-catalog scans filtered by is_active with ORDER BY model_name (DB-M7)';

-- ============================================================================
-- Index 2: provider_id + is_active + model_name (provider-scoped catalog queries)
-- ============================================================================
-- Supports queries like:
--   SELECT * FROM models WHERE provider_id = X AND is_active = true ORDER BY model_name
--
-- Used by: get_models_by_provider_slug(), get_models_by_gateway_for_catalog(),
--          get_models_for_catalog_with_filters() (when gateway_slug is supplied),
--          get_models_count_by_filters()
-- The existing idx_models_provider_active covers (provider_id, is_active) but forces
-- a separate sort step. Adding model_name eliminates that sort for paginated queries.
CREATE INDEX IF NOT EXISTS idx_models_provider_active_name
ON models (provider_id, is_active, model_name)
WHERE is_active = true;

COMMENT ON INDEX idx_models_provider_active_name IS
'Optimizes provider-filtered catalog queries with ORDER BY model_name (DB-M7)';

-- ============================================================================
-- Index 3: model_health_history (provider, model, checked_at DESC) composite
-- ============================================================================
-- Supports queries like:
--   SELECT * FROM model_health_history
--   WHERE provider = X AND model = Y ORDER BY checked_at DESC LIMIT 100
--
-- Used by: get_model_health_history()
-- The existing idx_history_provider_model_time covers (provider, model, checked_at)
-- but adding a DESC sort on checked_at lets the planner serve ORDER BY DESC
-- directly from the index without a reverse scan.
CREATE INDEX IF NOT EXISTS idx_model_health_history_model_checked
ON model_health_history (provider, model, checked_at DESC);

COMMENT ON INDEX idx_model_health_history_model_checked IS
'Optimizes paginated health-history lookups ordered by most recent check (DB-M7)';

-- ============================================================================
-- Update table statistics
-- ============================================================================

ANALYZE models;
ANALYZE model_health_history;

-- ============================================================================
-- Completion notice
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration 20260218000000_add_models_catalog_filter_indexes.sql completed';
    RAISE NOTICE 'Added 3 indexes for common catalog filter patterns (DB-M7):';
    RAISE NOTICE '  - idx_models_active_model_name (is_active + model_name ORDER BY)';
    RAISE NOTICE '  - idx_models_provider_active_name (provider_id + is_active + model_name ORDER BY)';
    RAISE NOTICE '  - idx_model_health_history_model_checked (provider + model + checked_at DESC)';
END $$;
