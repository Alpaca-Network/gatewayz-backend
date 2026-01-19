-- ============================================================================
-- SIMPLE PRICING CHECK - Works with any schema
-- ============================================================================
-- Run this in Supabase SQL Editor
-- ============================================================================

-- Step 1: What columns exist in models table?
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'models'
ORDER BY ordinal_position;

-- Step 2: Do migration columns exist?
SELECT
    CASE
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'models'
            AND column_name = 'pricing_format_migrated'
        ) THEN '✅ Migration columns exist - migration was applied'
        ELSE '❌ Migration NOT applied - columns missing'
    END as migration_status;

-- Step 3: How many models have pricing?
SELECT
    COUNT(*) as total_models,
    COUNT(pricing_prompt) as models_with_pricing,
    COUNT(*) FILTER (WHERE pricing_format_migrated = TRUE) as migrated_models
FROM models;

-- Step 4: Pricing distribution (is it normalized?)
SELECT
    CASE
        WHEN pricing_prompt IS NULL THEN '⚪ No pricing'
        WHEN pricing_prompt > 0.001 THEN '❌ Per-1M/1K format (NOT normalized)'
        WHEN pricing_prompt < 0.000001 AND pricing_prompt > 0 THEN '✅ Per-token format (NORMALIZED)'
        ELSE '⚠️  Zero or unusual value'
    END as pricing_format,
    COUNT(*) as count,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER() * 100), 1) as percentage
FROM models
WHERE pricing_prompt IS NOT NULL
GROUP BY pricing_format
ORDER BY count DESC;

-- Step 5: Sample prices (lowest 10)
SELECT
    id,
    pricing_prompt as "Price per Token",
    (1000 * pricing_prompt) as "Cost for 1K tokens",
    CASE
        WHEN (1000 * pricing_prompt) < 0.01 THEN '✅ Good'
        WHEN (1000 * pricing_prompt) < 1.0 THEN '⚠️  High'
        ELSE '❌ Too high'
    END as status
FROM models
WHERE pricing_prompt > 0
ORDER BY pricing_prompt ASC
LIMIT 10;

-- Step 6: Sample prices (highest 10)
SELECT
    id,
    pricing_prompt as "Price per Token",
    (1000 * pricing_prompt) as "Cost for 1K tokens",
    CASE
        WHEN (1000 * pricing_prompt) < 1.0 THEN '✅ Reasonable'
        WHEN (1000 * pricing_prompt) < 10.0 THEN '⚠️  Expensive'
        ELSE '❌ Very expensive'
    END as status
FROM models
WHERE pricing_prompt > 0
ORDER BY pricing_prompt DESC
LIMIT 10;

-- Step 7: Before/After comparison (if migration was run)
SELECT
    id,
    pricing_original_prompt as "BEFORE (original)",
    pricing_prompt as "AFTER (normalized)",
    CASE
        WHEN pricing_original_prompt > 0 AND pricing_prompt > 0
        THEN ROUND((pricing_original_prompt / pricing_prompt)::NUMERIC, 0)
        ELSE NULL
    END as "Change Factor (Nx smaller)"
FROM models
WHERE pricing_format_migrated = TRUE
  AND pricing_original_prompt IS NOT NULL
  AND pricing_prompt > 0
ORDER BY pricing_original_prompt DESC
LIMIT 10;

-- Step 8: Average pricing stats
SELECT
    ROUND(AVG(pricing_prompt)::NUMERIC, 12) as "Average Price per Token",
    ROUND(MIN(pricing_prompt)::NUMERIC, 12) as "Min Price",
    ROUND(MAX(pricing_prompt)::NUMERIC, 12) as "Max Price",
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pricing_prompt)::NUMERIC, 12) as "Median Price"
FROM models
WHERE pricing_prompt > 0;

-- Step 9: Final verdict
SELECT
    CASE
        WHEN COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) =
             COUNT(*) FILTER (WHERE pricing_prompt > 0)
        THEN '✅ SUCCESS: All pricing normalized to per-token format!'
        WHEN COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0)::FLOAT /
             COUNT(*) FILTER (WHERE pricing_prompt > 0) > 0.9
        THEN '⚠️  MOSTLY NORMALIZED: ' ||
             COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) || '/' ||
             COUNT(*) FILTER (WHERE pricing_prompt > 0) || ' models (' ||
             ROUND((COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0)::NUMERIC /
                    COUNT(*) FILTER (WHERE pricing_prompt > 0) * 100), 1) || '%)'
        ELSE '❌ NOT NORMALIZED: Only ' ||
             COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) || '/' ||
             COUNT(*) FILTER (WHERE pricing_prompt > 0) || ' models normalized'
    END as verdict,
    COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) as "✅ Normalized Models",
    COUNT(*) FILTER (WHERE pricing_prompt > 0.001) as "❌ Not Normalized",
    COUNT(*) FILTER (WHERE pricing_prompt > 0) as "Total with Pricing"
FROM models;

-- ============================================================================
-- WHAT TO LOOK FOR:
-- ============================================================================
-- ✅ Step 2: Should show "Migration columns exist"
-- ✅ Step 4: Should show "Per-token format (NORMALIZED)" at 100%
-- ✅ Step 5: Prices should be like $0.000000055 (very small)
-- ✅ Step 5: Cost for 1K tokens should be $0.00001 to $0.001 (cents, not dollars)
-- ✅ Step 7: Should show changes like "1000000x smaller"
-- ✅ Step 9: Should show "SUCCESS: All pricing normalized"
-- ============================================================================
