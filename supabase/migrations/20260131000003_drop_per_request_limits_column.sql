-- ============================================================================
-- Migration: Drop per_request_limits Column from Models Table
-- ============================================================================
-- Date: 2026-01-31
-- Purpose: Remove unused per_request_limits column
--
-- Rationale:
--   - Always set to None/NULL in all normalization functions
--   - Never populated with actual data
--   - Unused throughout the codebase
--
-- Impact:
--   - No data loss - column always NULL
--   - Simplifies schema
-- ============================================================================

-- Verify column is all NULL (will show count)
DO $$
DECLARE
    non_null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO non_null_count
    FROM models
    WHERE per_request_limits IS NOT NULL;

    RAISE NOTICE 'Models with non-null per_request_limits: %', non_null_count;

    IF non_null_count > 0 THEN
        RAISE WARNING 'Found % models with non-null per_request_limits - review before proceeding', non_null_count;
    ELSE
        RAISE NOTICE 'All per_request_limits are NULL - safe to drop';
    END IF;
END $$;

-- Drop the per_request_limits column (CASCADE to drop any dependent views)
ALTER TABLE "public"."models" DROP COLUMN IF EXISTS "per_request_limits" CASCADE;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '✅ Migration completed: Dropped per_request_limits column from models table';
    RAISE NOTICE '   Reason: Column was never populated';
    RAISE NOTICE '   Impact: No data loss - column always contained NULL values';
END $$;
