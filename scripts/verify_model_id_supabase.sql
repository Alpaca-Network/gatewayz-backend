-- ============================================================================
-- Verification Script: Check if model_id can be safely removed
-- ============================================================================
-- Run this in Supabase SQL Editor by copying and pasting the entire file
-- ============================================================================

-- ============================================================================
-- 1. BASIC STATISTICS
-- ============================================================================

SELECT
    '1. BASIC STATISTICS' as section,
    '' as details;

SELECT
    COUNT(*) as total_models,
    COUNT(DISTINCT model_id) as unique_model_ids,
    COUNT(DISTINCT model_name) as unique_model_names,
    COUNT(DISTINCT provider_model_id) as unique_provider_model_ids
FROM models;

-- ============================================================================
-- 2. EXACT MATCHES (model_id == model_name)
-- ============================================================================

SELECT
    '2. EXACT MATCHES (model_id == model_name)' as section,
    '' as details;

WITH exact_matches AS (
    SELECT
        m.id,
        m.model_id,
        m.model_name,
        p.slug as provider_slug
    FROM models m
    LEFT JOIN providers p ON p.id = m.provider_id
    WHERE m.model_id = m.model_name
)
SELECT
    COUNT(*) as exact_match_count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM models), 2) as percentage
FROM exact_matches;

SELECT
    'Examples of exact matches:' as section,
    '' as details;

SELECT
    p.slug as provider,
    m.model_id,
    m.model_name
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.model_id = m.model_name
LIMIT 5;

-- ============================================================================
-- 3. SLUGIFIED MATCHES (lower(model_name with spaces->hyphens) == model_id)
-- ============================================================================

SELECT
    '3. SLUGIFIED MATCHES (slugify(model_name) == model_id)' as section,
    '' as details;

WITH slugified_matches AS (
    SELECT
        m.id,
        m.model_id,
        m.model_name,
        LOWER(REPLACE(REPLACE(m.model_name, ' ', '-'), '_', '-')) as slugified_name,
        p.slug as provider_slug
    FROM models m
    LEFT JOIN providers p ON p.id = m.provider_id
    WHERE m.model_id != m.model_name
      AND LOWER(REPLACE(REPLACE(m.model_name, ' ', '-'), '_', '-')) = m.model_id
)
SELECT
    COUNT(*) as slugified_match_count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM models), 2) as percentage
FROM slugified_matches;

SELECT
    'Examples of slugified matches:' as section,
    '' as details;

SELECT
    p.slug as provider,
    m.model_id,
    m.model_name,
    LOWER(REPLACE(REPLACE(m.model_name, ' ', '-'), '_', '-')) as slugified_name
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.model_id != m.model_name
  AND LOWER(REPLACE(REPLACE(m.model_name, ' ', '-'), '_', '-')) = m.model_id
LIMIT 10;

-- ============================================================================
-- 4. DIFFERENT VALUES (model_id != model_name, even after slugification)
-- ============================================================================

SELECT
    '4. DIFFERENT VALUES (model_id != model_name)' as section,
    '' as details;

WITH different_values AS (
    SELECT
        m.id,
        m.model_id,
        m.model_name,
        m.provider_model_id,
        p.slug as provider_slug
    FROM models m
    LEFT JOIN providers p ON p.id = m.provider_id
    WHERE m.model_id != m.model_name
      AND LOWER(REPLACE(REPLACE(m.model_name, ' ', '-'), '_', '-')) != m.model_id
)
SELECT
    COUNT(*) as different_count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM models), 2) as percentage
FROM different_values;

SELECT
    'Examples of different values (CRITICAL TO REVIEW):' as section,
    '' as details;

SELECT
    p.slug as provider,
    m.model_id,
    m.model_name,
    m.provider_model_id
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.model_id != m.model_name
  AND LOWER(REPLACE(REPLACE(m.model_name, ' ', '-'), '_', '-')) != m.model_id
LIMIT 20;

-- ============================================================================
-- 5. MULTI-PROVIDER GROUPING ANALYSIS
-- ============================================================================

SELECT
    '5. MULTI-PROVIDER GROUPING ANALYSIS' as section,
    '' as details;

SELECT
    'Models offered by multiple providers (grouped by model_id):' as section,
    '' as details;

SELECT
    m.model_id,
    COUNT(DISTINCT m.provider_id) as provider_count,
    STRING_AGG(DISTINCT p.slug, ', ' ORDER BY p.slug) as providers,
    STRING_AGG(DISTINCT m.model_name, ' | ' ORDER BY m.model_name) as model_names
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
GROUP BY m.model_id
HAVING COUNT(DISTINCT m.provider_id) > 1
ORDER BY provider_count DESC
LIMIT 10;

SELECT
    'Models offered by multiple providers (grouped by model_name):' as section,
    '' as details;

SELECT
    m.model_name,
    COUNT(DISTINCT m.provider_id) as provider_count,
    STRING_AGG(DISTINCT p.slug, ', ' ORDER BY p.slug) as providers,
    STRING_AGG(DISTINCT m.model_id, ' | ' ORDER BY m.model_id) as model_ids
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
GROUP BY m.model_name
HAVING COUNT(DISTINCT m.provider_id) > 1
ORDER BY provider_count DESC
LIMIT 10;

-- ============================================================================
-- 6. CRITICAL: MULTI-PROVIDER GROUPING DISCREPANCIES
-- ============================================================================

SELECT
    '6. CRITICAL CHECK: Multi-provider grouping discrepancies' as section,
    '' as details;

SELECT
    'Models with SAME model_id but DIFFERENT model_names (breaks grouping!):' as section,
    '' as details;

WITH multi_provider_models AS (
    SELECT model_id
    FROM models
    GROUP BY model_id
    HAVING COUNT(DISTINCT provider_id) > 1
)
SELECT
    m.model_id,
    COUNT(DISTINCT m.model_name) as distinct_model_names,
    STRING_AGG(DISTINCT m.model_name, ' | ' ORDER BY m.model_name) as different_names,
    STRING_AGG(DISTINCT p.slug, ', ' ORDER BY p.slug) as providers
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.model_id IN (SELECT model_id FROM multi_provider_models)
GROUP BY m.model_id
HAVING COUNT(DISTINCT m.model_name) > 1
ORDER BY COUNT(DISTINCT m.model_name) DESC;

-- ============================================================================
-- 7. SUMMARY & RECOMMENDATION
-- ============================================================================

SELECT
    '7. SUMMARY & RECOMMENDATION' as section,
    '' as details;

DO $$
DECLARE
    total_count INTEGER;
    exact_match_count INTEGER;
    different_count INTEGER;
    discrepancy_count INTEGER;
    different_pct NUMERIC;
BEGIN
    -- Get counts
    SELECT COUNT(*) INTO total_count FROM models;

    SELECT COUNT(*) INTO exact_match_count
    FROM models
    WHERE model_id = model_name;

    SELECT COUNT(*) INTO different_count
    FROM models
    WHERE model_id != model_name
      AND LOWER(REPLACE(REPLACE(model_name, ' ', '-'), '_', '-')) != model_id;

    -- Check for multi-provider discrepancies
    WITH multi_provider_models AS (
        SELECT model_id
        FROM models
        GROUP BY model_id
        HAVING COUNT(DISTINCT provider_id) > 1
    )
    SELECT COUNT(*) INTO discrepancy_count
    FROM (
        SELECT m.model_id
        FROM models m
        WHERE m.model_id IN (SELECT model_id FROM multi_provider_models)
        GROUP BY m.model_id
        HAVING COUNT(DISTINCT m.model_name) > 1
    ) t;

    different_pct := ROUND(different_count * 100.0 / total_count, 2);

    RAISE NOTICE '';
    RAISE NOTICE 'Total models: %', total_count;
    RAISE NOTICE 'Exact matches: % (%.1f%%)', exact_match_count, ROUND(exact_match_count * 100.0 / total_count, 1);
    RAISE NOTICE 'Different values: % (%.1f%%)', different_count, different_pct;
    RAISE NOTICE 'Multi-provider discrepancies: %', discrepancy_count;
    RAISE NOTICE '';

    -- Recommendation
    IF different_count = 0 AND discrepancy_count = 0 THEN
        RAISE NOTICE '✅ RECOMMENDATION: SAFE TO REMOVE model_id column';
        RAISE NOTICE '';
        RAISE NOTICE 'Reasons:';
        RAISE NOTICE '  1. model_id and model_name are 100%% identical';
        RAISE NOTICE '  2. No multi-provider grouping discrepancies';
        RAISE NOTICE '  3. All queries can safely use model_name';
        RAISE NOTICE '';
        RAISE NOTICE 'Next steps:';
        RAISE NOTICE '  1. Update code to use model_name instead of model_id';
        RAISE NOTICE '  2. Create migration to drop model_id column';

    ELSIF different_pct < 5 AND discrepancy_count = 0 THEN
        RAISE NOTICE '⚠️  RECOMMENDATION: PROBABLY SAFE, but review differences first';
        RAISE NOTICE '';
        RAISE NOTICE 'Reasons:';
        RAISE NOTICE '  1. Only %.1f%% of models have different values', different_pct;
        RAISE NOTICE '  2. No multi-provider grouping issues';
        RAISE NOTICE '';
        RAISE NOTICE 'Before removing:';
        RAISE NOTICE '  1. Review the % models with different values (shown above)', different_count;
        RAISE NOTICE '  2. Normalize them to match if needed';
        RAISE NOTICE '  3. Then proceed with removal';

    ELSIF discrepancy_count > 0 THEN
        RAISE NOTICE '❌ RECOMMENDATION: NOT SAFE - Multi-provider grouping breaks!';
        RAISE NOTICE '';
        RAISE NOTICE 'CRITICAL ISSUE:';
        RAISE NOTICE '  % models have the SAME model_id but DIFFERENT model_names', discrepancy_count;
        RAISE NOTICE '  This means failover queries will break if you remove model_id!';
        RAISE NOTICE '';
        RAISE NOTICE 'Example: If "gemini-3-flash" from Google has model_name "Gemini 3 Flash"';
        RAISE NOTICE '         but OpenRouter has model_name "Google: Gemini 3 Flash Preview"';
        RAISE NOTICE '         then grouping by model_name will NOT find both providers!';
        RAISE NOTICE '';
        RAISE NOTICE 'Fix required:';
        RAISE NOTICE '  1. Standardize model_name values for multi-provider models';
        RAISE NOTICE '  2. Ensure same canonical model has same model_name across providers';
        RAISE NOTICE '  3. Then re-run this verification';

    ELSE
        RAISE NOTICE '❌ RECOMMENDATION: NOT SAFE to remove model_id column';
        RAISE NOTICE '';
        RAISE NOTICE 'Reasons:';
        RAISE NOTICE '  1. %.1f%% of models have genuinely different model_id and model_name', different_pct;
        RAISE NOTICE '  2. These fields serve different purposes';
        RAISE NOTICE '';
        RAISE NOTICE 'Options:';
        RAISE NOTICE '  A. Keep both columns (current architecture is correct)';
        RAISE NOTICE '  B. Normalize data so model_id == model_name everywhere';
        RAISE NOTICE '  C. Rename model_id → model_slug for clarity';
    END IF;

    RAISE NOTICE '';
END $$;

-- ============================================================================
-- VERIFICATION COMPLETE
-- ============================================================================

SELECT 'VERIFICATION COMPLETE - Check the Messages tab for RECOMMENDATION' as status;
