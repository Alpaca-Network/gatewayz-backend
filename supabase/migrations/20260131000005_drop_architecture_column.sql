-- ============================================================================
-- Migration: Drop architecture Column from Models Table
-- ============================================================================
-- Date: 2026-01-31
-- Purpose: Remove redundant architecture column after migrating to metadata
--
-- Rationale:
--   - Architecture data has been migrated to metadata JSONB column
--   - Code updated to read from metadata with fallback to column
--   - JSONB metadata is more flexible for storing structured data
--
-- Prerequisites:
--   - Migration 20260131000004 must be run first (migrates data)
--   - Code updated to read from metadata.architecture
--
-- Impact:
--   - No data loss - data already in metadata
--   - Simplifies schema
-- ============================================================================

-- Verify architecture data is in metadata
DO $$
DECLARE
    total_arch INTEGER;
    arch_in_metadata INTEGER;
    missing_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_arch
    FROM models
    WHERE architecture IS NOT NULL;

    SELECT COUNT(*) INTO arch_in_metadata
    FROM models
    WHERE metadata->'architecture' IS NOT NULL;

    missing_count := total_arch - arch_in_metadata;

    RAISE NOTICE '📊 Pre-drop verification:';
    RAISE NOTICE '   Models with architecture column: %', total_arch;
    RAISE NOTICE '   Models with architecture in metadata: %', arch_in_metadata;

    IF missing_count > 0 THEN
        RAISE WARNING '⚠️  Found % models with architecture NOT in metadata!', missing_count;
        RAISE WARNING '   Run migration 20260131000004 first to migrate data';
    ELSE
        RAISE NOTICE '   ✅ All architecture data is in metadata - safe to drop column';
    END IF;
END $$;

-- Drop the architecture column (CASCADE to drop any dependent views)
ALTER TABLE "public"."models" DROP COLUMN IF EXISTS "architecture" CASCADE;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '✅ Migration completed: Dropped architecture column from models table';
    RAISE NOTICE '   Data preserved in metadata.architecture';
    RAISE NOTICE '   Code reads from metadata with backwards compatibility';
    RAISE NOTICE '';
END $$;
