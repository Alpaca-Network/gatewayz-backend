-- ============================================================================
-- VIEW DATABASE CHANGES - For Supabase SQL Editor
-- ============================================================================
-- Copy and paste this into Supabase Dashboard > SQL Editor > Run
-- ============================================================================

-- ============================================================================
-- 1. MIGRATION COLUMNS (NEW)
-- ============================================================================
SELECT '1️⃣  NEW MIGRATION COLUMNS' as section;

SELECT
    column_name,
    data_type,
    CASE WHEN is_nullable = 'YES' THEN '✓ Nullable' ELSE '✗ Required' END as nullable
FROM information_schema.columns
WHERE table_name = 'models'
  AND column_name LIKE 'pricing_%'
ORDER BY column_name;

-- ============================================================================
-- 2. MIGRATION STATUS
-- ============================================================================
SELECT '2️⃣  MIGRATION STATUS' as section;

SELECT
    CASE
        WHEN pricing_format_migrated = TRUE THEN '✅ Migrated'
        WHEN pricing_format_migrated = FALSE THEN '⏳ Not Migrated'
        ELSE '❓ Unknown'
    END as status,
    COUNT(*) as model_count,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER() * 100), 1)::TEXT || '%' as percentage
FROM "public"."models"
GROUP BY pricing_format_migrated
ORDER BY pricing_format_migrated DESC NULLS LAST;

-- ============================================================================
-- 3. PRICING DISTRIBUTION (CURRENT)
-- ============================================================================
SELECT '3️⃣  CURRENT PRICING DISTRIBUTION' as section;

SELECT
    CASE
        WHEN pricing_prompt IS NULL THEN '❌ NULL pricing'
        WHEN pricing_prompt > 0.001 THEN '❌ Per-1M format (> $0.001) - NOT NORMALIZED'
        WHEN pricing_prompt >= 0.000001 THEN '❌ Per-1K format ($0.000001-$0.001) - NOT NORMALIZED'
        WHEN pricing_prompt > 0 THEN '✅ Per-token format (< $0.000001) - NORMALIZED'
        ELSE '⚠️  Zero pricing'
    END as format_status,
    COUNT(*) as count,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER() * 100), 1)::TEXT || '%' as percentage,
    TO_CHAR(AVG(pricing_prompt), 'FM$0.000000000000') as avg_price,
    TO_CHAR(MIN(pricing_prompt), 'FM$0.000000000000') as min_price,
    TO_CHAR(MAX(pricing_prompt), 'FM$0.000000000000') as max_price
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
GROUP BY format_status
ORDER BY count DESC;

-- ============================================================================
-- 4. BEFORE/AFTER COMPARISON (Sample of 10)
-- ============================================================================
SELECT '4️⃣  BEFORE → AFTER COMPARISON (10 samples)' as section;

SELECT
    LEFT(name, 40) as model_name,
    source_gateway as provider,
    TO_CHAR(pricing_original_prompt, 'FM$0.000000') as before_original,
    TO_CHAR(pricing_prompt, 'FM$0.000000000000') as after_normalized,
    CASE
        WHEN pricing_original_prompt > 0 AND pricing_prompt > 0
        THEN ROUND((pricing_original_prompt / pricing_prompt)::NUMERIC, 0)::TEXT || 'x smaller'
        ELSE 'N/A'
    END as change_factor
FROM "public"."models"
WHERE pricing_format_migrated = TRUE
  AND pricing_original_prompt IS NOT NULL
  AND pricing_prompt IS NOT NULL
  AND pricing_prompt > 0
ORDER BY pricing_original_prompt DESC
LIMIT 10;

-- ============================================================================
-- 5. SAMPLE MODELS - LOWEST PRICES (Should be very small numbers)
-- ============================================================================
SELECT '5️⃣  LOWEST PRICED MODELS (10 samples)' as section;

SELECT
    LEFT(name, 45) as model_name,
    source_gateway as provider,
    TO_CHAR(pricing_prompt, 'FM$0.000000000000') as price_per_token,
    TO_CHAR((1000 * pricing_prompt), 'FM$0.000000') as cost_per_1k_tokens,
    CASE
        WHEN (1000 * pricing_prompt) < 0.01 THEN '✅ Reasonable'
        WHEN (1000 * pricing_prompt) < 1.0 THEN '⚠️  High but OK'
        ELSE '❌ Too high!'
    END as status
FROM "public"."models"
WHERE pricing_prompt > 0
ORDER BY pricing_prompt ASC
LIMIT 10;

-- ============================================================================
-- 6. SAMPLE MODELS - HIGHEST PRICES
-- ============================================================================
SELECT '6️⃣  HIGHEST PRICED MODELS (10 samples)' as section;

SELECT
    LEFT(name, 45) as model_name,
    source_gateway as provider,
    TO_CHAR(pricing_prompt, 'FM$0.000000000000') as price_per_token,
    TO_CHAR((1000 * pricing_prompt), 'FM$0.000000') as cost_per_1k_tokens,
    CASE
        WHEN (1000 * pricing_prompt) < 1.0 THEN '✅ Reasonable'
        WHEN (1000 * pricing_prompt) < 10.0 THEN '⚠️  Expensive'
        ELSE '❌ Very expensive!'
    END as status
FROM "public"."models"
WHERE pricing_prompt > 0
ORDER BY pricing_prompt DESC
LIMIT 10;

-- ============================================================================
-- 7. VERIFICATION VIEW DATA (Created by migration)
-- ============================================================================
SELECT '7️⃣  VERIFICATION VIEW (Created by migration)' as section;

SELECT
    LEFT(model_name, 40) as model,
    TO_CHAR(original_prompt, 'FM$0.000000') as before,
    TO_CHAR(new_prompt, 'FM$0.000000000000') as after,
    ROUND(change_percent, 1)::TEXT || '%' as change_pct,
    format_before,
    format_after
FROM "public"."pricing_migration_verification"
LIMIT 10;

-- ============================================================================
-- 8. SUMMARY STATISTICS
-- ============================================================================
SELECT '8️⃣  SUMMARY STATISTICS' as section;

WITH stats AS (
    SELECT
        COUNT(*) as total_models,
        COUNT(*) FILTER (WHERE pricing_prompt IS NOT NULL) as models_with_pricing,
        COUNT(*) FILTER (WHERE pricing_format_migrated = TRUE) as migrated_count,
        COUNT(*) FILTER (WHERE pricing_prompt > 0.001) as per_1m_format,
        COUNT(*) FILTER (WHERE pricing_prompt >= 0.000001 AND pricing_prompt <= 0.001) as per_1k_format,
        COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) as per_token_format,
        AVG(pricing_prompt) as avg_price,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pricing_prompt) as median_price,
        MIN(pricing_prompt) as min_price,
        MAX(pricing_prompt) as max_price
    FROM "public"."models"
    WHERE pricing_prompt > 0
)
SELECT
    total_models as "Total Models",
    models_with_pricing as "With Pricing",
    migrated_count as "✅ Migrated",
    per_token_format as "✅ Per-Token",
    per_1k_format as "❌ Per-1K",
    per_1m_format as "❌ Per-1M",
    TO_CHAR(avg_price, 'FM$0.000000000000') as "Avg Price",
    TO_CHAR(median_price, 'FM$0.000000000000') as "Median Price",
    TO_CHAR(min_price, 'FM$0.000000000000') as "Min Price",
    TO_CHAR(max_price, 'FM$0.000000000000') as "Max Price"
FROM stats;

-- ============================================================================
-- 9. FINAL VERDICT
-- ============================================================================
SELECT '9️⃣  FINAL VERDICT' as section;

WITH verdict AS (
    SELECT
        COUNT(*) FILTER (WHERE pricing_prompt > 0.001) as not_normalized_count,
        COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) as normalized_count,
        COUNT(*) FILTER (WHERE pricing_prompt > 0) as total_priced
    FROM "public"."models"
)
SELECT
    CASE
        WHEN normalized_count = total_priced THEN '✅ SUCCESS: All pricing normalized to per-token format!'
        WHEN normalized_count::FLOAT / total_priced > 0.9 THEN '⚠️  MOSTLY NORMALIZED: ' || normalized_count || '/' || total_priced || ' models (' || ROUND(normalized_count::NUMERIC / total_priced * 100, 1) || '%)'
        ELSE '❌ NOT NORMALIZED: Only ' || normalized_count || '/' || total_priced || ' models normalized (' || ROUND(normalized_count::NUMERIC / total_priced * 100, 1) || '%)'
    END as verdict,
    normalized_count as "✅ Normalized Models",
    not_normalized_count as "❌ Not Normalized",
    total_priced as "Total Models w/ Pricing"
FROM verdict;

-- ============================================================================
-- EXPECTED RESULTS AFTER SUCCESSFUL MIGRATION:
-- ============================================================================
-- ✅ pricing_format_migrated = TRUE for migrated models
-- ✅ All prices < $0.000001 (per-token format)
-- ✅ Cost for 1000 tokens = $0.00001 to $0.001 (reasonable range)
-- ✅ Backup columns contain original values
--
-- If you see prices > $0.001, the migration has NOT been applied yet.
-- ============================================================================
