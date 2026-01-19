-- ============================================================================
-- QUICK PRICING CHECK - Simple & Fast
-- ============================================================================
-- Run this in Supabase SQL Editor for a quick verification
-- ============================================================================

-- Check 1: Do migration columns exist?
SELECT
    '✅ Migration columns exist' as status
WHERE EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'models'
    AND column_name = 'pricing_format_migrated'
)
UNION ALL
SELECT
    '❌ Migration NOT applied - columns missing' as status
WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'models'
    AND column_name = 'pricing_format_migrated'
);

-- Check 2: How many models migrated?
SELECT
    COUNT(*) FILTER (WHERE pricing_format_migrated = TRUE) as migrated_models,
    COUNT(*) FILTER (WHERE pricing_format_migrated = FALSE) as not_migrated,
    COUNT(*) as total_models
FROM models;

-- Check 3: Pricing format distribution
SELECT
    CASE
        WHEN pricing_prompt > 0.001 THEN '❌ Per-1M/1K (NOT normalized)'
        WHEN pricing_prompt < 0.000001 AND pricing_prompt > 0 THEN '✅ Per-token (normalized)'
        ELSE 'Other'
    END as format,
    COUNT(*) as count,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER() * 100), 1)::TEXT || '%' as percentage
FROM models
WHERE pricing_prompt IS NOT NULL
GROUP BY format
ORDER BY count DESC;

-- Check 4: Sample prices (should be VERY small if normalized)
SELECT
    id,
    source_gateway,
    pricing_prompt as "Price per Token",
    (1000 * pricing_prompt) as "Cost for 1K tokens",
    CASE
        WHEN (1000 * pricing_prompt) < 0.01 THEN '✅ Good'
        ELSE '❌ Too high'
    END as status
FROM models
WHERE pricing_prompt > 0
ORDER BY pricing_prompt ASC
LIMIT 10;

-- Check 5: Before/After samples (if migrated)
SELECT
    id,
    pricing_original_prompt as "Before",
    pricing_prompt as "After",
    ROUND((pricing_original_prompt / pricing_prompt)::NUMERIC, 0)::TEXT || 'x smaller' as "Change"
FROM models
WHERE pricing_format_migrated = TRUE
  AND pricing_original_prompt > 0
  AND pricing_prompt > 0
ORDER BY pricing_original_prompt DESC
LIMIT 10;

-- Final verdict
SELECT
    CASE
        WHEN COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) =
             COUNT(*) FILTER (WHERE pricing_prompt > 0)
        THEN '✅ SUCCESS: All pricing normalized!'
        ELSE '❌ NOT NORMALIZED: Mixed formats detected'
    END as verdict,
    COUNT(*) FILTER (WHERE pricing_prompt < 0.000001 AND pricing_prompt > 0) as normalized,
    COUNT(*) FILTER (WHERE pricing_prompt > 0) as total_priced
FROM models;
