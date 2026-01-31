-- ============================================================================
-- Migration: Migrate architecture column data to metadata JSONB
-- ============================================================================
-- Date: 2026-01-31
-- Purpose: Move architecture data to metadata column before dropping
--
-- Rationale:
--   - Architecture data is used for extracting modality/capabilities
--   - Belongs in metadata JSONB column for better data organization
--   - Enables dropping the dedicated architecture column
--
-- Impact:
--   - No data loss - data copied to metadata
--   - Prepares for architecture column removal
-- ============================================================================

-- Step 1: Show current state
DO $$
DECLARE
    total_models INTEGER;
    non_null_architecture INTEGER;
    arch_in_metadata INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_models FROM models;

    SELECT COUNT(*) INTO non_null_architecture
    FROM models
    WHERE architecture IS NOT NULL;

    SELECT COUNT(*) INTO arch_in_metadata
    FROM models
    WHERE metadata->'architecture' IS NOT NULL;

    RAISE NOTICE '📊 Current State:';
    RAISE NOTICE '   Total models: %', total_models;
    RAISE NOTICE '   Models with architecture column: %', non_null_architecture;
    RAISE NOTICE '   Models with architecture in metadata: %', arch_in_metadata;
    RAISE NOTICE '';
END $$;

-- Step 2: Migrate architecture to metadata (only if not already there)
UPDATE models
SET metadata = COALESCE(metadata, '{}'::jsonb) ||
               jsonb_build_object('architecture', architecture::jsonb)
WHERE architecture IS NOT NULL
  AND architecture != ''
  AND architecture != 'null'
  AND (metadata->'architecture' IS NULL OR metadata->'architecture' = 'null'::jsonb);

-- Step 3: Verify migration
DO $$
DECLARE
    migrated_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO migrated_count
    FROM models
    WHERE architecture IS NOT NULL
      AND metadata->'architecture' IS NOT NULL;

    RAISE NOTICE '';
    RAISE NOTICE '✅ Migration completed: %', migrated_count;
    RAISE NOTICE '   Architecture data copied to metadata column';
    RAISE NOTICE '   Ready to drop architecture column in next migration';
    RAISE NOTICE '';
END $$;
