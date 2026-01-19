-- ============================================================================
-- VIEW DATABASE CHANGES - Run this on REMOTE/PRODUCTION database
-- ============================================================================
-- Run with:
--   psql postgresql://postgres:[PASSWORD]@[HOST]:5432/postgres -f view_remote_changes.sql
-- Or use Supabase Dashboard > SQL Editor
-- ============================================================================

\echo ''
\echo '===================================================================================================='
\echo 'PRICING MIGRATION - DATABASE CHANGES'
\echo '===================================================================================================='
\echo ''

-- ============================================================================
-- 1. MIGRATION COLUMNS (NEW)
-- ============================================================================
\echo '1️⃣  NEW MIGRATION COLUMNS'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    column_name,
    data_type,
    CASE WHEN is_nullable = 'YES' THEN '✓' ELSE '✗' END as nullable
FROM information_schema.columns
WHERE table_name = 'models'
  AND column_name LIKE 'pricing_%'
ORDER BY column_name;

\echo ''
\echo 'New columns added by migration:'
\echo '  ✅ pricing_format_migrated (boolean) - Migration status flag'
\echo '  ✅ pricing_original_prompt (numeric) - Backup of original value'
\echo '  ✅ pricing_original_completion (numeric) - Backup of original value'
\echo '  ✅ pricing_original_image (numeric) - Backup of original value'
\echo '  ✅ pricing_original_request (numeric) - Backup of original value'
\echo ''

-- ============================================================================
-- 2. MIGRATION STATUS
-- ============================================================================
\echo '2️⃣  MIGRATION STATUS'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    pricing_format_migrated as migrated,
    COUNT(*) as model_count,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER() * 100), 1) as percentage
FROM "public"."models"
GROUP BY pricing_format_migrated
ORDER BY migrated DESC NULLS LAST;

\echo ''

-- ============================================================================
-- 3. PRICING DISTRIBUTION (CURRENT)
-- ============================================================================
\echo '3️⃣  CURRENT PRICING DISTRIBUTION'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    CASE
        WHEN pricing_prompt IS NULL THEN '❌ NULL pricing'
        WHEN pricing_prompt > 0.001 THEN '❌ Per-1M format (> $0.001)'
        WHEN pricing_prompt >= 0.000001 THEN '❌ Per-1K format ($0.000001-$0.001)'
        WHEN pricing_prompt > 0 THEN '✅ Per-token format (< $0.000001)'
        ELSE '⚠️  Zero pricing'
    END as format_status,
    COUNT(*) as count,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER() * 100), 1) as percentage,
    ROUND(AVG(pricing_prompt)::NUMERIC, 12) as avg_price,
    ROUND(MIN(pricing_prompt)::NUMERIC, 12) as min_price,
    ROUND(MAX(pricing_prompt)::NUMERIC, 12) as max_price
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
GROUP BY format_status
ORDER BY count DESC;

\echo ''

-- ============================================================================
-- 4. BEFORE/AFTER COMPARISON (Sample of 10)
-- ============================================================================
\echo '4️⃣  BEFORE → AFTER COMPARISON (10 samples)'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    LEFT(name, 40) as model_name,
    source_gateway as provider,
    TO_CHAR(pricing_original_prompt, 'FM$0.000000') as "BEFORE (original)",
    TO_CHAR(pricing_prompt, 'FM$0.000000000000') as "AFTER (normalized)",
    CASE
        WHEN pricing_original_prompt > 0 AND pricing_prompt > 0
        THEN ROUND((pricing_original_prompt / pricing_prompt)::NUMERIC, 0)::TEXT || 'x'
        ELSE 'N/A'
    END as "Change Factor"
FROM "public"."models"
WHERE pricing_format_migrated = TRUE
  AND pricing_original_prompt IS NOT NULL
  AND pricing_prompt IS NOT NULL
  AND pricing_prompt > 0
ORDER BY pricing_original_prompt DESC
LIMIT 10;

\echo ''

-- ============================================================================
-- 5. SAMPLE MODELS WITH NEW PRICING
-- ============================================================================
\echo '5️⃣  SAMPLE MODELS (Lowest 10 prices - should be VERY small)'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    LEFT(name, 45) as model_name,
    source_gateway as provider,
    TO_CHAR(pricing_prompt, 'FM$0.000000000000') as price_per_token,
    TO_CHAR((1000 * pricing_prompt), 'FM$0.000000') as cost_per_1k_tokens,
    CASE
        WHEN (1000 * pricing_prompt) < 0.01 THEN '✅ Reasonable'
        ELSE '❌ Too high!'
    END as status
FROM "public"."models"
WHERE pricing_prompt > 0
ORDER BY pricing_prompt ASC
LIMIT 10;

\echo ''

-- ============================================================================
-- 6. SAMPLE MODELS WITH NEW PRICING (Highest)
-- ============================================================================
\echo '6️⃣  SAMPLE MODELS (Highest 10 prices)'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    LEFT(name, 45) as model_name,
    source_gateway as provider,
    TO_CHAR(pricing_prompt, 'FM$0.000000000000') as price_per_token,
    TO_CHAR((1000 * pricing_prompt), 'FM$0.000000') as cost_per_1k_tokens,
    CASE
        WHEN (1000 * pricing_prompt) < 1.0 THEN '✅ Reasonable'
        ELSE '❌ Too high!'
    END as status
FROM "public"."models"
WHERE pricing_prompt > 0
ORDER BY pricing_prompt DESC
LIMIT 10;

\echo ''

-- ============================================================================
-- 7. VERIFICATION VIEW DATA
-- ============================================================================
\echo '7️⃣  VERIFICATION VIEW (Created by migration)'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

SELECT
    LEFT(model_name, 40) as model,
    TO_CHAR(original_prompt, 'FM$0.000000') as before,
    TO_CHAR(new_prompt, 'FM$0.000000000000') as after,
    ROUND(change_percent, 1)::TEXT || '%' as change,
    format_before,
    format_after
FROM "public"."pricing_migration_verification"
LIMIT 10;

\echo ''

-- ============================================================================
-- 8. STATISTICS SUMMARY
-- ============================================================================
\echo '8️⃣  SUMMARY STATISTICS'
\echo '---------------------------------------------------------------------------------------------------'
\echo ''

WITH stats AS (
    SELECT
        COUNT(*) as total_models,
        COUNT(*) FILTER (WHERE pricing_prompt IS NOT NULL) as models_with_pricing,
        COUNT(*) FILTER (WHERE pricing_format_migrated = TRUE) as migrated_count,
        COUNT(*) FILTER (WHERE pricing_prompt > 0.001) as per_1m_format,
        COUNT(*) FILTER (WHERE pricing_prompt >= 0.000001 AND pricing_prompt <= 0.001) as per_1k_format,
        COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) as per_token_format,
        ROUND(AVG(pricing_prompt)::NUMERIC, 12) as avg_price,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pricing_prompt)::NUMERIC, 12) as median_price,
        ROUND(MIN(pricing_prompt)::NUMERIC, 12) as min_price,
        ROUND(MAX(pricing_prompt)::NUMERIC, 12) as max_price
    FROM "public"."models"
    WHERE pricing_prompt > 0
)
SELECT
    total_models as "Total Models",
    models_with_pricing as "With Pricing",
    migrated_count as "Migrated",
    per_token_format as "Per-Token ✅",
    per_1k_format as "Per-1K ❌",
    per_1m_format as "Per-1M ❌",
    TO_CHAR(avg_price, 'FM$0.000000000000') as "Avg Price",
    TO_CHAR(median_price, 'FM$0.000000000000') as "Median Price",
    TO_CHAR(min_price, 'FM$0.000000000000') as "Min Price",
    TO_CHAR(max_price, 'FM$0.000000000000') as "Max Price"
FROM stats;

\echo ''
\echo '===================================================================================================='
\echo 'SUMMARY'
\echo '===================================================================================================='
\echo ''
\echo 'Expected results after successful migration:'
\echo '  ✅ pricing_format_migrated = TRUE for all migrated models'
\echo '  ✅ All prices < $0.000001 (per-token format)'
\echo '  ✅ Cost for 1000 tokens = $0.00001 to $0.001 (reasonable range)'
\echo '  ✅ Backup columns contain original values'
\echo ''
\echo 'If you see prices > $0.001, the migration has NOT been applied yet.'
\echo ''
