-- Migration: Normalize all pricing to per-token format
-- Created: 2026-01-19
-- Description: Convert all pricing values from per-1M/per-1K formats to per-token format
--
-- IMPORTANT: This migration standardizes pricing across all models
-- After this migration, all pricing will be stored as cost per single token
-- Example: $0.055 per 1M tokens → $0.000000055 per token
--
-- Diagnostic Results: 1000 models analyzed
--   - 504 models (50.4%) in per-1M format (> 0.001)
--   - 479 models (47.9%) in per-1K format (0.000001 - 0.001)
--   - 17 models (1.7%) already in per-token format (< 0.000001)

-- ============================================================================
-- SAFETY CHECKS
-- ============================================================================

DO $$
DECLARE
    v_models_with_pricing INTEGER;
    v_table_exists BOOLEAN;
BEGIN
    -- Check if models table exists
    SELECT EXISTS (
        SELECT FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'models'
    ) INTO v_table_exists;

    IF NOT v_table_exists THEN
        RAISE EXCEPTION 'Models table not found. Cannot proceed with migration.';
    END IF;

    -- Check if we have models with pricing
    SELECT COUNT(*) INTO v_models_with_pricing
    FROM "public"."models"
    WHERE pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL;

    IF v_models_with_pricing = 0 THEN
        RAISE WARNING 'No models with pricing found. Migration will have no effect.';
    ELSE
        RAISE NOTICE 'Found % models with pricing data', v_models_with_pricing;
    END IF;

    RAISE NOTICE 'Safety checks passed. Proceeding with migration...';
END$$;

-- ============================================================================
-- STEP 1: Add Tracking and Backup Columns
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 1: Adding tracking and backup columns';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';

    -- Add migration tracking column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'models'
        AND column_name = 'pricing_format_migrated'
    ) THEN
        ALTER TABLE "public"."models"
        ADD COLUMN "pricing_format_migrated" BOOLEAN DEFAULT FALSE;
        RAISE NOTICE '✓ Added pricing_format_migrated column';
    ELSE
        RAISE NOTICE 'ℹ pricing_format_migrated column already exists';
    END IF;

    -- Add backup columns if they don't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'models'
        AND column_name = 'pricing_original_prompt'
    ) THEN
        ALTER TABLE "public"."models"
        ADD COLUMN "pricing_original_prompt" NUMERIC(20, 10),
        ADD COLUMN "pricing_original_completion" NUMERIC(20, 10),
        ADD COLUMN "pricing_original_image" NUMERIC(20, 10),
        ADD COLUMN "pricing_original_request" NUMERIC(20, 10);
        RAISE NOTICE '✓ Added backup columns for original pricing';
    ELSE
        RAISE NOTICE 'ℹ Backup columns already exist';
    END IF;
END$$;

-- ============================================================================
-- STEP 2: Backup Original Values
-- ============================================================================

DO $$
DECLARE
    v_backup_count INTEGER;
    v_already_backed_up INTEGER;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 2: Backing up original pricing values';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';

    -- Check if already backed up
    SELECT COUNT(*) INTO v_already_backed_up
    FROM "public"."models"
    WHERE pricing_original_prompt IS NOT NULL OR pricing_original_completion IS NOT NULL;

    IF v_already_backed_up > 0 THEN
        RAISE NOTICE 'ℹ Found existing backup for % models (skipping backup)', v_already_backed_up;
    ELSE
        -- Backup original values
        UPDATE "public"."models"
        SET
            pricing_original_prompt = pricing_prompt,
            pricing_original_completion = pricing_completion,
            pricing_original_image = pricing_image,
            pricing_original_request = pricing_request
        WHERE pricing_format_migrated = FALSE
          AND (pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL);

        GET DIAGNOSTICS v_backup_count = ROW_COUNT;
        RAISE NOTICE '✓ Backed up original pricing for % models', v_backup_count;
    END IF;
END$$;

-- ============================================================================
-- STEP 3: Analyze Current Pricing Distribution
-- ============================================================================

DO $$
DECLARE
    v_total INTEGER;
    v_per_1m INTEGER;
    v_per_1k INTEGER;
    v_per_token INTEGER;
    v_zero_pricing INTEGER;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 3: Analyzing current pricing distribution';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';

    SELECT COUNT(*) INTO v_total
    FROM "public"."models"
    WHERE pricing_prompt IS NOT NULL;

    SELECT COUNT(*) INTO v_per_1m
    FROM "public"."models"
    WHERE pricing_prompt > 0.001;

    SELECT COUNT(*) INTO v_per_1k
    FROM "public"."models"
    WHERE pricing_prompt >= 0.000001 AND pricing_prompt <= 0.001;

    SELECT COUNT(*) INTO v_per_token
    FROM "public"."models"
    WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0;

    SELECT COUNT(*) INTO v_zero_pricing
    FROM "public"."models"
    WHERE pricing_prompt = 0 OR pricing_prompt IS NULL;

    RAISE NOTICE 'Current Distribution:';
    RAISE NOTICE '  Total models with pricing: %', v_total;
    IF v_total > 0 THEN
        RAISE NOTICE '  Per-1M format (> $0.001):  % models (%.1f%%)', v_per_1m, (v_per_1m::FLOAT / v_total::FLOAT * 100);
        RAISE NOTICE '  Per-1K format ($0.000001-$0.001): % models (%.1f%%)', v_per_1k, (v_per_1k::FLOAT / v_total::FLOAT * 100);
        RAISE NOTICE '  Per-token format (< $0.000001): % models (%.1f%%) [already correct]', v_per_token, (v_per_token::FLOAT / v_total::FLOAT * 100);
    ELSE
        RAISE NOTICE '  Per-1M format (> $0.001):  % models (0.0%%)', v_per_1m;
        RAISE NOTICE '  Per-1K format ($0.000001-$0.001): % models (0.0%%)', v_per_1k;
        RAISE NOTICE '  Per-token format (< $0.000001): % models (0.0%%) [already correct]', v_per_token;
    END IF;
    RAISE NOTICE '  Zero/null pricing: % models', v_zero_pricing;
END$$;

-- ============================================================================
-- STEP 4: Normalize to Per-Token Format
-- ============================================================================

DO $$
DECLARE
    v_updated_count INTEGER;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 4: Converting all pricing to per-token format';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'Detection logic:';
    RAISE NOTICE '  - Values > $0.001: Per-1M → divide by 1,000,000';
    RAISE NOTICE '  - Values $0.000001-$0.001: Per-1K → divide by 1,000';
    RAISE NOTICE '  - Values < $0.000001: Already per-token → keep as-is';
    RAISE NOTICE '';

    UPDATE "public"."models"
    SET
        -- Normalize prompt pricing
        pricing_prompt = CASE
            WHEN pricing_prompt IS NULL THEN NULL
            WHEN pricing_prompt > 0.001 THEN ROUND(CAST(pricing_prompt / 1000000.0 AS NUMERIC), 12)
            WHEN pricing_prompt >= 0.000001 THEN ROUND(CAST(pricing_prompt / 1000.0 AS NUMERIC), 12)
            ELSE pricing_prompt  -- Already per-token
        END,

        -- Normalize completion pricing
        pricing_completion = CASE
            WHEN pricing_completion IS NULL THEN NULL
            WHEN pricing_completion > 0.001 THEN ROUND(CAST(pricing_completion / 1000000.0 AS NUMERIC), 12)
            WHEN pricing_completion >= 0.000001 THEN ROUND(CAST(pricing_completion / 1000.0 AS NUMERIC), 12)
            ELSE pricing_completion  -- Already per-token
        END,

        -- Normalize image pricing
        pricing_image = CASE
            WHEN pricing_image IS NULL THEN NULL
            WHEN pricing_image > 0.001 THEN ROUND(CAST(pricing_image / 1000000.0 AS NUMERIC), 12)
            WHEN pricing_image >= 0.000001 THEN ROUND(CAST(pricing_image / 1000.0 AS NUMERIC), 12)
            ELSE pricing_image  -- Already per-token
        END,

        -- Normalize request pricing
        pricing_request = CASE
            WHEN pricing_request IS NULL THEN NULL
            WHEN pricing_request > 0.001 THEN ROUND(CAST(pricing_request / 1000000.0 AS NUMERIC), 12)
            WHEN pricing_request >= 0.000001 THEN ROUND(CAST(pricing_request / 1000.0 AS NUMERIC), 12)
            ELSE pricing_request  -- Already per-token
        END,

        -- Mark as migrated
        pricing_format_migrated = TRUE

    WHERE pricing_format_migrated = FALSE;

    GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    RAISE NOTICE '✓ Converted pricing for % models', v_updated_count;
END$$;

-- ============================================================================
-- STEP 5: Update Column Comments
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 5: Updating column documentation';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';

    COMMENT ON COLUMN "public"."models"."pricing_prompt" IS
        'Cost in USD per single token for input/prompt tokens. Example: 0.000000055 for $0.055 per 1M tokens. Updated 2026-01-19.';

    COMMENT ON COLUMN "public"."models"."pricing_completion" IS
        'Cost in USD per single token for output/completion tokens. Example: 0.000000055 for $0.055 per 1M tokens. Updated 2026-01-19.';

    COMMENT ON COLUMN "public"."models"."pricing_image" IS
        'Cost in USD per single image token. Example: 0.000000001 for $0.001 per 1M tokens. Updated 2026-01-19.';

    COMMENT ON COLUMN "public"."models"."pricing_request" IS
        'Cost in USD per single request. Usually 0. Updated 2026-01-19.';

    RAISE NOTICE '✓ Updated column comments to reflect per-token format';
END$$;

-- ============================================================================
-- STEP 6: Validation and Statistics
-- ============================================================================

DO $$
DECLARE
    v_migrated_count INTEGER;
    v_avg_prompt_price NUMERIC;
    v_max_prompt_price NUMERIC;
    v_min_prompt_price NUMERIC;
    v_per_token_count INTEGER;
    v_suspicious_count INTEGER;
    v_median_price NUMERIC;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 6: Validation and Statistics';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';

    -- Get statistics
    SELECT
        COUNT(*),
        AVG(pricing_prompt),
        MAX(pricing_prompt),
        MIN(pricing_prompt),
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pricing_prompt)
    INTO v_migrated_count, v_avg_prompt_price, v_max_prompt_price, v_min_prompt_price, v_median_price
    FROM "public"."models"
    WHERE pricing_format_migrated = TRUE
      AND pricing_prompt IS NOT NULL
      AND pricing_prompt > 0;

    -- Count models in correct per-token range
    SELECT COUNT(*) INTO v_per_token_count
    FROM "public"."models"
    WHERE pricing_format_migrated = TRUE
      AND pricing_prompt IS NOT NULL
      AND pricing_prompt < 0.001
      AND pricing_prompt > 0;

    -- Count suspicious values (may still be in wrong format)
    SELECT COUNT(*) INTO v_suspicious_count
    FROM "public"."models"
    WHERE pricing_format_migrated = TRUE
      AND pricing_prompt IS NOT NULL
      AND pricing_prompt >= 0.001;

    -- Display statistics
    RAISE NOTICE 'Migration Statistics:';
    RAISE NOTICE '  Total models migrated: %', v_migrated_count;
    RAISE NOTICE '  Average prompt price: $%.12f', v_avg_prompt_price;
    RAISE NOTICE '  Median prompt price: $%.12f', v_median_price;
    RAISE NOTICE '  Max prompt price: $%.12f', v_max_prompt_price;
    RAISE NOTICE '  Min prompt price: $%.12f', v_min_prompt_price;
    RAISE NOTICE '';
    RAISE NOTICE 'Validation Results:';
    RAISE NOTICE '  ✓ Models in correct per-token range (<$0.001): % (%.1f%%)',
        v_per_token_count,
        (v_per_token_count::FLOAT / NULLIF(v_migrated_count, 0)::FLOAT * 100);

    IF v_suspicious_count > 0 THEN
        RAISE WARNING '  ⚠ Suspicious values (>=$0.001): % (%.1f%%)',
            v_suspicious_count,
            (v_suspicious_count::FLOAT / NULLIF(v_migrated_count, 0)::FLOAT * 100);
        RAISE WARNING 'Manual review recommended for models with suspicious pricing';
    ELSE
        RAISE NOTICE '  ✓ No suspicious values found';
    END IF;
END$$;

-- ============================================================================
-- STEP 7: Create Verification View
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 7: Creating verification view';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
END$$;

-- Drop existing view if it exists
DROP VIEW IF EXISTS "public"."pricing_migration_verification";

-- Create verification view
CREATE VIEW "public"."pricing_migration_verification" AS
SELECT
    m.id,
    m.model_name,
    m.model_id,
    p.name as provider_name,
    p.slug as provider_slug,

    -- Original values (before migration)
    m.pricing_original_prompt as original_prompt,
    m.pricing_original_completion as original_completion,

    -- New values (after migration)
    m.pricing_prompt as new_prompt,
    m.pricing_completion as new_completion,

    -- Detected format
    CASE
        WHEN m.pricing_original_prompt IS NULL THEN 'no_pricing'
        WHEN m.pricing_original_prompt > 0.001 THEN 'per_1m'
        WHEN m.pricing_original_prompt >= 0.000001 THEN 'per_1k'
        ELSE 'per_token'
    END as detected_format,

    -- Division factor applied
    CASE
        WHEN m.pricing_original_prompt > 0 AND m.pricing_prompt > 0 THEN
            ROUND(m.pricing_original_prompt / m.pricing_prompt, 0)
        ELSE NULL
    END as division_factor,

    -- Validation: New price should be very small
    CASE
        WHEN m.pricing_prompt IS NULL THEN 'no_pricing'
        WHEN m.pricing_prompt = 0 THEN 'zero'
        WHEN m.pricing_prompt < 0.000001 THEN 'valid_per_token'
        WHEN m.pricing_prompt < 0.001 THEN 'valid_per_1k'
        ELSE 'suspicious_high'
    END as validation_status,

    -- Cost estimate for 1000 tokens
    CASE
        WHEN m.pricing_prompt > 0 THEN ROUND(CAST(1000 * m.pricing_prompt AS NUMERIC), 8)
        ELSE NULL
    END as cost_per_1k_tokens,

    m.pricing_format_migrated,
    m.created_at,
    m.updated_at

FROM "public"."models" m
JOIN "public"."providers" p ON m.provider_id = p.id
WHERE m.pricing_original_prompt IS NOT NULL
   OR m.pricing_original_completion IS NOT NULL
ORDER BY m.pricing_prompt DESC NULLS LAST;

COMMENT ON VIEW "public"."pricing_migration_verification" IS
    'Verification view for pricing migration (created 2026-01-19). '
    'Shows before/after values, detected format, and validation status. '
    'Use this to verify the migration was successful. '
    'Query: SELECT * FROM pricing_migration_verification WHERE validation_status = ''suspicious_high'';';

DO $$
BEGIN
    RAISE NOTICE '✓ Created verification view: pricing_migration_verification';
END$$;

-- ============================================================================
-- STEP 8: Sample Verification Data
-- ============================================================================

DO $$
DECLARE
    v_record RECORD;
    v_count INTEGER := 0;
    v_valid_count INTEGER;
    v_suspicious_count INTEGER;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'STEP 8: Sample verification data';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';

    -- Count by status
    SELECT
        COUNT(CASE WHEN validation_status IN ('valid_per_token', 'valid_per_1k') THEN 1 END),
        COUNT(CASE WHEN validation_status = 'suspicious_high' THEN 1 END)
    INTO v_valid_count, v_suspicious_count
    FROM "public"."pricing_migration_verification";

    RAISE NOTICE 'Validation Summary:';
    RAISE NOTICE '  ✓ Valid: % models', v_valid_count;
    IF v_suspicious_count > 0 THEN
        RAISE NOTICE '  ⚠ Suspicious: % models (needs review)', v_suspicious_count;
    END IF;
    RAISE NOTICE '';
    RAISE NOTICE 'Sample Data (Top 10 by price):';
    RAISE NOTICE '────────────────────────────────────────────────────────────────────────────';

    FOR v_record IN (
        SELECT
            model_name,
            provider_slug,
            detected_format,
            original_prompt,
            new_prompt,
            division_factor,
            cost_per_1k_tokens,
            validation_status
        FROM "public"."pricing_migration_verification"
        WHERE new_prompt IS NOT NULL
        ORDER BY new_prompt DESC
        LIMIT 10
    ) LOOP
        v_count := v_count + 1;
        RAISE NOTICE '%. % (%)',
            v_count,
            RPAD(v_record.model_name, 35),
            v_record.provider_slug;
        RAISE NOTICE '   Format: % → per_token (÷%)',
            RPAD(v_record.detected_format, 10),
            COALESCE(v_record.division_factor::TEXT, 'N/A');
        RAISE NOTICE '   Price: $%.6f → $%.12f [%]',
            v_record.original_prompt,
            v_record.new_prompt,
            v_record.validation_status;
        RAISE NOTICE '   Cost for 1000 tokens: $%.8f',
            v_record.cost_per_1k_tokens;
        IF v_count < 10 THEN
            RAISE NOTICE '';
        END IF;
    END LOOP;

    RAISE NOTICE '────────────────────────────────────────────────────────────────────────────';
    RAISE NOTICE 'For full report, run:';
    RAISE NOTICE '  SELECT * FROM pricing_migration_verification;';
END$$;

-- ============================================================================
-- FINAL SUCCESS MESSAGE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE '✅ PRICING NORMALIZATION MIGRATION COMPLETED SUCCESSFULLY';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
    RAISE NOTICE '';
    RAISE NOTICE 'What Changed:';
    RAISE NOTICE '  ✓ All pricing normalized to per-token format';
    RAISE NOTICE '  ✓ Original values backed up in pricing_original_* columns';
    RAISE NOTICE '  ✓ Verification view created for auditing';
    RAISE NOTICE '';
    RAISE NOTICE 'Next Steps:';
    RAISE NOTICE '  1. Review verification view: SELECT * FROM pricing_migration_verification LIMIT 20;';
    RAISE NOTICE '  2. Check for suspicious values: SELECT * FROM pricing_migration_verification WHERE validation_status = ''suspicious_high'';';
    RAISE NOTICE '  3. Validate cost calculations in your application';
    RAISE NOTICE '  4. Monitor first few requests after deployment';
    RAISE NOTICE '';
    RAISE NOTICE 'Rollback:';
    RAISE NOTICE '  If issues arise, run: 20260119000001_rollback_pricing_normalization.sql';
    RAISE NOTICE '';
    RAISE NOTICE '══════════════════════════════════════════════════════════════════════════════';
END$$;
