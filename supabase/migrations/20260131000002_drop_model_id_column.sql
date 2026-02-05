-- ============================================================================
-- Migration: Drop model_id column from models table
-- ============================================================================
-- Date: 2026-01-31
-- Purpose: Remove redundant model_id column in favor of model_name
--
-- Rationale:
--   - Verification confirmed model_id and model_name are functionally equivalent
--   - model_name is now the canonical identifier for multi-provider grouping
--   - provider_model_id remains for provider-specific API identifiers
--   - This simplifies the schema and eliminates data redundancy
--
-- Impact:
--   - All code references have been updated to use model_name instead
--   - No data loss - model_name contains all necessary information
--   - Failover queries now group by model_name
-- ============================================================================

-- Drop the model_id column from the models table (CASCADE to drop any dependent views)
ALTER TABLE "public"."models" DROP COLUMN IF EXISTS "model_id" CASCADE;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Migration completed: Dropped model_id column from models table';
    RAISE NOTICE '';
    RAISE NOTICE 'Summary:';
    RAISE NOTICE '  - Removed: model_id (str) - redundant canonical identifier';
    RAISE NOTICE '  - Kept: model_name (str) - now the canonical identifier';
    RAISE NOTICE '  - Kept: provider_model_id (str) - provider-specific API identifier';
    RAISE NOTICE '  - Kept: id (int) - primary key';
    RAISE NOTICE '';
    RAISE NOTICE 'Impact:';
    RAISE NOTICE '  - Schema simplified';
    RAISE NOTICE '  - Data redundancy eliminated';
    RAISE NOTICE '  - Multi-provider grouping now uses model_name';
    RAISE NOTICE '';
END $$;
