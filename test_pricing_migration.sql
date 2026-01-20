-- ============================================================================
-- Pricing Migration Testing Script
-- ============================================================================
-- Run this script to verify the pricing normalization migration
-- Execute with: psql $DATABASE_URL -f test_pricing_migration.sql
-- ============================================================================

\echo ''
\echo '=========================================================================='
\echo 'PRICING MIGRATION VERIFICATION'
\echo '=========================================================================='
\echo ''

-- ============================================================================
-- 1. PRE-MIGRATION STATE
-- ============================================================================
\echo '1. PRE-MIGRATION STATE'
\echo '----------------------'

-- Check if migration columns exist
\echo 'Checking migration status...'
SELECT
    CASE
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'models'
            AND column_name = 'pricing_format_migrated'
        ) THEN '✓ Migration columns exist'
        ELSE '✗ Migration columns do not exist - migration not run'
    END as migration_status;

-- Count models by migration status
\echo ''
\echo 'Models by migration status:'
SELECT
    pricing_format_migrated,
    COUNT(*) as model_count
FROM "public"."models"
GROUP BY pricing_format_migrated;

\echo ''

-- ============================================================================
-- 2. CURRENT PRICING DISTRIBUTION
-- ============================================================================
\echo '2. CURRENT PRICING DISTRIBUTION'
\echo '--------------------------------'

SELECT
    CASE
        WHEN pricing_prompt IS NULL THEN 'NULL pricing'
        WHEN pricing_prompt > 0.001 THEN 'Per-1M format (> 0.001)'
        WHEN pricing_prompt >= 0.000001 THEN 'Per-1K format (0.000001-0.001)'
        WHEN pricing_prompt > 0 THEN 'Per-token format (< 0.000001)'
        ELSE 'Zero/Other'
    END as format_category,
    COUNT(*) as count,
    ROUND(AVG(pricing_prompt)::numeric, 10) as avg_price,
    ROUND(MIN(pricing_prompt)::numeric, 10) as min_price,
    ROUND(MAX(pricing_prompt)::numeric, 10) as max_price
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
GROUP BY
    CASE
        WHEN pricing_prompt IS NULL THEN 'NULL pricing'
        WHEN pricing_prompt > 0.001 THEN 'Per-1M format (> 0.001)'
        WHEN pricing_prompt >= 0.000001 THEN 'Per-1K format (0.000001-0.001)'
        WHEN pricing_prompt > 0 THEN 'Per-token format (< 0.000001)'
        ELSE 'Zero/Other'
    END
ORDER BY count DESC;

\echo ''

-- ============================================================================
-- 3. SAMPLE MODELS WITH PRICING
-- ============================================================================
\echo '3. SAMPLE MODELS WITH PRICING (Top 10 by prompt price)'
\echo '--------------------------------------------------------'

SELECT
    id,
    name,
    source_gateway,
    ROUND(pricing_prompt::numeric, 12) as prompt_price,
    ROUND(pricing_completion::numeric, 12) as completion_price,
    CASE
        WHEN pricing_prompt > 0.001 THEN 'Per-1M'
        WHEN pricing_prompt >= 0.000001 THEN 'Per-1K'
        WHEN pricing_prompt > 0 THEN 'Per-token'
        ELSE 'Unknown'
    END as detected_format,
    pricing_format_migrated as migrated
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
ORDER BY pricing_prompt DESC
LIMIT 10;

\echo ''

-- ============================================================================
-- 4. BACKUP DATA CHECK (If migration was run)
-- ============================================================================
\echo '4. BACKUP DATA CHECK'
\echo '--------------------'

SELECT
    COUNT(*) as models_with_backup,
    COUNT(*) FILTER (WHERE pricing_original_prompt IS NOT NULL) as have_original_prompt,
    COUNT(*) FILTER (WHERE pricing_original_completion IS NOT NULL) as have_original_completion
FROM "public"."models"
WHERE pricing_format_migrated = TRUE;

\echo ''

-- ============================================================================
-- 5. VERIFICATION VIEW (If migration was run)
-- ============================================================================
\echo '5. MIGRATION VERIFICATION VIEW (Sample)'
\echo '----------------------------------------'

SELECT * FROM "public"."pricing_migration_verification"
LIMIT 10;

\echo ''

-- ============================================================================
-- 6. COST CALCULATION TESTS
-- ============================================================================
\echo '6. COST CALCULATION TESTS'
\echo '-------------------------'
\echo 'Testing cost calculations with current pricing...'
\echo ''

-- Test 1: Low-cost model (e.g., Llama 3.1 8B)
\echo 'Test 1: 1000 tokens on a low-cost model'
SELECT
    id,
    name,
    pricing_prompt,
    (1000 * pricing_prompt) as calculated_cost_for_1000_tokens,
    CASE
        WHEN (1000 * pricing_prompt) < 0.01 THEN '✓ Reasonable'
        ELSE '✗ Too expensive!'
    END as validation
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
  AND pricing_prompt > 0
ORDER BY pricing_prompt ASC
LIMIT 3;

\echo ''

-- Test 2: High-cost model (e.g., GPT-4)
\echo 'Test 2: 1000 tokens on a high-cost model'
SELECT
    id,
    name,
    pricing_prompt,
    (1000 * pricing_prompt) as calculated_cost_for_1000_tokens,
    CASE
        WHEN (1000 * pricing_prompt) < 1.0 THEN '✓ Reasonable'
        ELSE '✗ Too expensive!'
    END as validation
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
  AND pricing_prompt > 0
ORDER BY pricing_prompt DESC
LIMIT 3;

\echo ''

-- ============================================================================
-- 7. SUSPICIOUS VALUES CHECK
-- ============================================================================
\echo '7. SUSPICIOUS VALUES CHECK'
\echo '--------------------------'

-- Check for suspiciously high prices (likely not normalized)
\echo 'Models with suspiciously high prices (> 0.001 = likely per-1M/per-1K):'
SELECT
    COUNT(*) as suspicious_count,
    AVG(pricing_prompt) as avg_suspicious_price
FROM "public"."models"
WHERE pricing_prompt > 0.001;

\echo ''

-- Check for extremely low prices (likely correct per-token)
\echo 'Models with correct per-token prices (< 0.000001):'
SELECT
    COUNT(*) as correct_count,
    ROUND(AVG(pricing_prompt)::numeric, 12) as avg_correct_price
FROM "public"."models"
WHERE pricing_prompt > 0 AND pricing_prompt < 0.000001;

\echo ''

-- ============================================================================
-- 8. PROVIDER BREAKDOWN
-- ============================================================================
\echo '8. PRICING BY PROVIDER'
\echo '----------------------'

SELECT
    source_gateway,
    COUNT(*) as model_count,
    COUNT(*) FILTER (WHERE pricing_prompt IS NOT NULL) as models_with_pricing,
    ROUND(AVG(pricing_prompt)::numeric, 12) as avg_prompt_price,
    ROUND(AVG(pricing_completion)::numeric, 12) as avg_completion_price
FROM "public"."models"
GROUP BY source_gateway
ORDER BY model_count DESC
LIMIT 15;

\echo ''

-- ============================================================================
-- 9. RANGE VALIDATION
-- ============================================================================
\echo '9. RANGE VALIDATION'
\echo '-------------------'
\echo 'Checking if all prices are in expected per-token range...'

SELECT
    CASE
        WHEN COUNT(*) FILTER (WHERE pricing_prompt > 0.001) = 0
        THEN '✓ All prices appear normalized (< 0.001)'
        ELSE '✗ WARNING: ' || COUNT(*) FILTER (WHERE pricing_prompt > 0.001) || ' models have prices > 0.001'
    END as validation_result,
    COUNT(*) as total_models,
    COUNT(*) FILTER (WHERE pricing_prompt IS NOT NULL AND pricing_prompt > 0) as priced_models,
    COUNT(*) FILTER (WHERE pricing_prompt > 0.001) as suspicious_models,
    COUNT(*) FILTER (WHERE pricing_prompt <= 0.000001 AND pricing_prompt > 0) as normalized_models
FROM "public"."models";

\echo ''

-- ============================================================================
-- 10. SUMMARY STATISTICS
-- ============================================================================
\echo '10. SUMMARY STATISTICS'
\echo '----------------------'

SELECT
    COUNT(*) as total_models,
    COUNT(*) FILTER (WHERE pricing_prompt IS NOT NULL) as models_with_pricing,
    ROUND(AVG(pricing_prompt)::numeric, 12) as avg_prompt_price,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pricing_prompt)::numeric, 12) as median_prompt_price,
    ROUND(MIN(pricing_prompt)::numeric, 12) as min_prompt_price,
    ROUND(MAX(pricing_prompt)::numeric, 12) as max_prompt_price,
    COUNT(*) FILTER (WHERE pricing_format_migrated = TRUE) as migrated_models
FROM "public"."models"
WHERE pricing_prompt > 0;

\echo ''
\echo '=========================================================================='
\echo 'VERIFICATION COMPLETE'
\echo '=========================================================================='
\echo ''
\echo 'If prices are > 0.001, migration has NOT been applied yet.'
\echo 'If prices are < 0.000001, migration HAS been applied successfully.'
\echo ''
