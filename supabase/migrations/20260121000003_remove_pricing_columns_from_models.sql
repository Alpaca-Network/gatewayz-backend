-- ============================================================================
-- Remove Legacy Pricing Columns from Models Table
-- ============================================================================
-- Migration: 20260121000003
-- Description: Consolidates all pricing data to model_pricing table
-- WARNING: This migration is IRREVERSIBLE without a database backup
--
-- Prerequisites:
--   1. All code changes deployed and stable (48+ hours monitoring)
--   2. Verification script passed: python3 scripts/verify_pricing_migration.py
--   3. Database backup created
--   4. model_pricing table fully populated
--
-- This migration:
--   - Removes pricing columns from models table (single source of truth)
--   - Drops unused pricing_tiers table
--   - Updates comments to reflect new architecture
-- ============================================================================

-- ============================================================================
-- SAFETY CHECKS
-- ============================================================================

DO $$
DECLARE
    models_with_pricing INTEGER;
    pricing_entries INTEGER;
    tables_exist BOOLEAN;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'PRICING CONSOLIDATION MIGRATION';
    RAISE NOTICE '========================================';
    RAISE NOTICE '';

    -- Check if model_pricing table exists
    SELECT EXISTS (
        SELECT FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_name = 'model_pricing'
    ) INTO tables_exist;

    IF NOT tables_exist THEN
        RAISE EXCEPTION 'model_pricing table does not exist. Cannot proceed.';
    END IF;

    -- Count models with pricing (if columns still exist)
    SELECT COUNT(*) INTO models_with_pricing
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'models'
      AND column_name IN ('pricing_prompt', 'pricing_completion');

    -- Count model_pricing entries
    SELECT COUNT(*) INTO pricing_entries
    FROM model_pricing;

    RAISE NOTICE 'Pricing columns in models table: %',
        CASE WHEN models_with_pricing > 0 THEN 'YES (will be removed)' ELSE 'NO (already removed)' END;
    RAISE NOTICE 'model_pricing entries: %', pricing_entries;
    RAISE NOTICE '';

    IF pricing_entries = 0 THEN
        RAISE WARNING 'model_pricing table is EMPTY. This may cause issues.';
        RAISE WARNING 'Consider running: SELECT * FROM populate_model_pricing_with_classification();';
    END IF;

    IF models_with_pricing = 0 THEN
        RAISE NOTICE '✓ Pricing columns already removed from models table';
        RAISE NOTICE '  Migration may have been previously applied';
    END IF;

END$$;

-- ============================================================================
-- STEP 1: Drop pricing columns from models table
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '→ Removing pricing columns from models table...';

    -- Drop pricing_prompt column (with CASCADE to drop dependent views)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'models'
          AND column_name = 'pricing_prompt'
    ) THEN
        ALTER TABLE models DROP COLUMN pricing_prompt CASCADE;
        RAISE NOTICE '  ✓ Dropped column: pricing_prompt (with dependent objects)';
    ELSE
        RAISE NOTICE '  • Column already removed: pricing_prompt';
    END IF;

    -- Drop pricing_completion column
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'models'
          AND column_name = 'pricing_completion'
    ) THEN
        ALTER TABLE models DROP COLUMN pricing_completion CASCADE;
        RAISE NOTICE '  ✓ Dropped column: pricing_completion';
    ELSE
        RAISE NOTICE '  • Column already removed: pricing_completion';
    END IF;

    -- Drop pricing_image column
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'models'
          AND column_name = 'pricing_image'
    ) THEN
        ALTER TABLE models DROP COLUMN pricing_image CASCADE;
        RAISE NOTICE '  ✓ Dropped column: pricing_image';
    ELSE
        RAISE NOTICE '  • Column already removed: pricing_image';
    END IF;

    -- Drop pricing_request column
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'models'
          AND column_name = 'pricing_request'
    ) THEN
        ALTER TABLE models DROP COLUMN pricing_request CASCADE;
        RAISE NOTICE '  ✓ Dropped column: pricing_request';
    ELSE
        RAISE NOTICE '  • Column already removed: pricing_request';
    END IF;

END$$;

-- ============================================================================
-- STEP 2: Drop unused pricing_tiers table
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '→ Dropping unused pricing_tiers table...';

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'pricing_tiers'
    ) THEN
        DROP TABLE IF EXISTS pricing_tiers CASCADE;
        RAISE NOTICE '  ✓ Dropped table: pricing_tiers';
    ELSE
        RAISE NOTICE '  • Table already removed: pricing_tiers';
    END IF;

END$$;

-- ============================================================================
-- STEP 3: Recreate views using model_pricing table
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '→ Recreating views with model_pricing...';
END$$;

-- Drop and recreate views to avoid column conflicts
DROP VIEW IF EXISTS "public"."models_with_pricing" CASCADE;
DROP VIEW IF EXISTS "public"."models_pricing_status" CASCADE;
DROP VIEW IF EXISTS "public"."models_pricing_classified" CASCADE;
DROP VIEW IF EXISTS "public"."model_usage_analytics" CASCADE;

-- Recreate models_with_pricing view
CREATE OR REPLACE VIEW "public"."models_with_pricing" AS
SELECT
    m.*,
    mp.price_per_input_token,
    mp.price_per_output_token,
    mp.price_per_image_token,
    mp.price_per_request,
    mp.pricing_source,
    mp.pricing_type,
    mp.last_updated as pricing_last_updated
FROM "public"."models" m
LEFT JOIN "public"."model_pricing" mp ON m.id = mp.model_id;

-- Recreate models_pricing_status view
CREATE OR REPLACE VIEW "public"."models_pricing_status" AS
SELECT
    m.id,
    m.model_id,
    m.model_name,
    p.name as provider_name,
    m.is_active,
    mp.pricing_type,
    mp.price_per_input_token,
    mp.price_per_output_token,
    CASE
        WHEN mp.pricing_type = 'paid' THEN '💰 Paid'
        WHEN mp.pricing_type = 'free' THEN '🆓 Free'
        WHEN mp.pricing_type = 'deprecated' THEN '🗑️ Deprecated'
        WHEN mp.pricing_type = 'missing' THEN '❓ Missing Pricing'
        WHEN m.is_active = false THEN '⏸️ Inactive'
        WHEN mp.model_id IS NULL THEN '❌ Not Classified'
        ELSE '❓ Unknown'
    END as status_display
FROM "public"."models" m
LEFT JOIN "public"."providers" p ON p.id = m.provider_id
LEFT JOIN "public"."model_pricing" mp ON mp.model_id = m.id;

-- Grant permissions
GRANT SELECT ON "public"."models_with_pricing" TO authenticated, anon, service_role;
GRANT SELECT ON "public"."models_pricing_status" TO authenticated, anon, service_role;

DO $$
BEGIN
    RAISE NOTICE '  ✓ Recreated models_with_pricing view';
    RAISE NOTICE '  ✓ Recreated models_pricing_status view';
END$$;

-- ============================================================================
-- STEP 4: Update table comments
-- ============================================================================

COMMENT ON TABLE models IS
    'AI models catalog. Pricing data stored in separate model_pricing table. Updated: 2026-01-21';

COMMENT ON TABLE model_pricing IS
    'SINGLE SOURCE OF TRUTH for all model pricing. Normalized per-token format. Updated: 2026-01-21';

-- ============================================================================
-- STEP 5: Verify key views exist
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '→ Verifying pricing views...';

    IF EXISTS (
        SELECT 1 FROM information_schema.views
        WHERE table_schema = 'public'
          AND table_name = 'models_with_pricing'
    ) THEN
        RAISE NOTICE '  ✓ View exists: models_with_pricing';
    ELSE
        RAISE WARNING '  ⚠ View missing: models_with_pricing (may need to be recreated)';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.views
        WHERE table_schema = 'public'
          AND table_name = 'models_pricing_classified'
    ) THEN
        RAISE NOTICE '  ✓ View exists: models_pricing_classified';
    ELSE
        RAISE WARNING '  ⚠ View missing: models_pricing_classified';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.views
        WHERE table_schema = 'public'
          AND table_name = 'models_pricing_status'
    ) THEN
        RAISE NOTICE '  ✓ View exists: models_pricing_status';
    ELSE
        RAISE WARNING '  ⚠ View missing: models_pricing_status';
    END IF;

END$$;

-- ============================================================================
-- COMPLETION
-- ============================================================================

DO $$
DECLARE
    pricing_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO pricing_count FROM model_pricing;

    RAISE NOTICE '';
    RAISE NOTICE '========================================';
    RAISE NOTICE '✅ MIGRATION COMPLETED SUCCESSFULLY';
    RAISE NOTICE '========================================';
    RAISE NOTICE '';
    RAISE NOTICE 'Summary:';
    RAISE NOTICE '  • Legacy pricing columns removed from models table';
    RAISE NOTICE '  • pricing_tiers table dropped (unused)';
    RAISE NOTICE '  • model_pricing is now ONLY source for pricing';
    RAISE NOTICE '  • Total pricing entries: %', pricing_count;
    RAISE NOTICE '';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '  1. Monitor application for pricing-related errors';
    RAISE NOTICE '  2. Verify model catalog API returns pricing';
    RAISE NOTICE '  3. Check chat request costing accuracy';
    RAISE NOTICE '  4. Review admin dashboard pricing display';
    RAISE NOTICE '';
    RAISE NOTICE '⚠ Note: This migration is IRREVERSIBLE without backup';
    RAISE NOTICE '   Keep database backup for at least 7 days';
    RAISE NOTICE '';
END$$;

-- ============================================================================
-- ROLLBACK INSTRUCTIONS (for documentation only)
-- ============================================================================

-- If you need to rollback this migration (within backup retention):
--
-- 1. Restore from backup, OR:
--
-- 2. Manually re-add columns and repopulate:
--
--    ALTER TABLE models
--        ADD COLUMN pricing_prompt NUMERIC(20, 10),
--        ADD COLUMN pricing_completion NUMERIC(20, 10),
--        ADD COLUMN pricing_image NUMERIC(20, 10),
--        ADD COLUMN pricing_request NUMERIC(10, 6);
--
--    UPDATE models m
--    SET
--        pricing_prompt = mp.price_per_input_token,
--        pricing_completion = mp.price_per_output_token,
--        pricing_image = mp.price_per_image_token,
--        pricing_request = mp.price_per_request
--    FROM model_pricing mp
--    WHERE m.id = mp.model_id;
--
-- ============================================================================
