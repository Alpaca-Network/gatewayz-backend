-- Rollback Migration: Restore original pricing format
-- Created: 2026-01-19
-- Description: Rollback the pricing normalization to restore original values
--
-- IMPORTANT: This rollback restores pricing to pre-migration state
-- Only use this if the migration caused issues

-- Step 1: Verify backup data exists
DO $$
DECLARE
    v_backup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_backup_count
    FROM "public"."models"
    WHERE pricing_original_prompt IS NOT NULL
       OR pricing_original_completion IS NOT NULL;

    IF v_backup_count = 0 THEN
        RAISE EXCEPTION 'No backup data found! Cannot rollback safely.';
    END IF;

    RAISE NOTICE 'Found backup data for % models', v_backup_count;
END$$;

-- Step 2: Restore original pricing values
UPDATE "public"."models"
SET
    pricing_prompt = pricing_original_prompt,
    pricing_completion = pricing_original_completion,
    pricing_image = pricing_original_image,
    pricing_request = pricing_original_request,
    pricing_format_migrated = FALSE
WHERE pricing_format_migrated = TRUE;

-- Step 3: Revert column comments
COMMENT ON COLUMN "public"."models"."pricing_prompt" IS
    'Cost in USD for input/prompt tokens. Format varies by provider.';

COMMENT ON COLUMN "public"."models"."pricing_completion" IS
    'Cost in USD for output/completion tokens. Format varies by provider.';

COMMENT ON COLUMN "public"."models"."pricing_image" IS
    'Cost in USD for image tokens. Format varies by provider.';

COMMENT ON COLUMN "public"."models"."pricing_request" IS
    'Cost in USD per request.';

-- Step 4: Log rollback results
DO $$
DECLARE
    v_restored_count INTEGER;
    v_avg_prompt_price NUMERIC;
BEGIN
    SELECT COUNT(*), AVG(pricing_prompt)
    INTO v_restored_count, v_avg_prompt_price
    FROM "public"."models"
    WHERE pricing_original_prompt IS NOT NULL;

    RAISE NOTICE '════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'PRICING NORMALIZATION ROLLBACK COMPLETE';
    RAISE NOTICE '════════════════════════════════════════════════════════════════';
    RAISE NOTICE 'Models restored: %', v_restored_count;
    RAISE NOTICE 'Average prompt price (restored): $%.6f', v_avg_prompt_price;
    RAISE NOTICE '';
    RAISE NOTICE 'Original pricing format has been restored';
    RAISE NOTICE 'Backup columns retained for safety';
    RAISE NOTICE '════════════════════════════════════════════════════════════════';
END$$;

-- Step 5: Drop verification view
DROP VIEW IF EXISTS "public"."pricing_migration_verification";

-- Optional: Remove backup columns (commented out for safety)
-- Uncomment only if you're sure you want to remove backups

-- ALTER TABLE "public"."models"
-- DROP COLUMN IF EXISTS "pricing_original_prompt",
-- DROP COLUMN IF EXISTS "pricing_original_completion",
-- DROP COLUMN IF EXISTS "pricing_original_image",
-- DROP COLUMN IF EXISTS "pricing_original_request",
-- DROP COLUMN IF EXISTS "pricing_format_migrated";

-- Final rollback confirmation
DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '✓ Rollback completed successfully!';
    RAISE NOTICE '⚠  Backup columns retained for safety';
    RAISE NOTICE '   Run cleanup migration separately if needed';
    RAISE NOTICE '';
END$$;
