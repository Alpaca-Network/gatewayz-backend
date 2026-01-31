-- ============================================================================
-- Migration: Drop top_provider Column from Models Table
-- Created: 2026-01-31
-- Description: Removes the unused top_provider column from the models table.
--              This column was never populated (always NULL) and served no purpose.
-- ============================================================================

-- First, drop the view that depends on the top_provider column
DROP VIEW IF EXISTS "public"."models_with_pricing" CASCADE;

-- Drop the top_provider column from the models table
ALTER TABLE "public"."models" DROP COLUMN IF EXISTS "top_provider";

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Migration completed: Dropped top_provider column from models table';
    RAISE NOTICE '   Reason: Column was never populated and served no functional purpose';
    RAISE NOTICE '   Impact: No data loss - column always contained NULL values';
END$$;
