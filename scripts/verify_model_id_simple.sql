-- ============================================================================
-- Simple Verification: Check if model_id can be safely removed
-- ============================================================================
-- This version outputs results as tables (no RAISE NOTICE needed)
-- ============================================================================

-- Step 1: Count analysis
WITH stats AS (
    SELECT
        COUNT(*) as total_models,
        COUNT(CASE WHEN model_id = model_name THEN 1 END) as exact_matches,
        COUNT(CASE
            WHEN model_id != model_name
            AND LOWER(REPLACE(REPLACE(model_name, ' ', '-'), '_', '-')) = model_id
            THEN 1
        END) as slugified_matches,
        COUNT(CASE
            WHEN model_id != model_name
            AND LOWER(REPLACE(REPLACE(model_name, ' ', '-'), '_', '-')) != model_id
            THEN 1
        END) as different_values
    FROM models
),
multi_provider_discrepancies AS (
    SELECT COUNT(*) as discrepancy_count
    FROM (
        SELECT m.model_id
        FROM models m
        WHERE m.model_id IN (
            SELECT model_id
            FROM models
            GROUP BY model_id
            HAVING COUNT(DISTINCT provider_id) > 1
        )
        GROUP BY m.model_id
        HAVING COUNT(DISTINCT m.model_name) > 1
    ) t
)
SELECT
    '📊 VERIFICATION RESULTS' as "Analysis",
    '══════════════════════════════════════' as "Details"
UNION ALL
SELECT
    'Total models',
    total_models::text
FROM stats
UNION ALL
SELECT
    'Exact matches (model_id == model_name)',
    exact_matches::text || ' (' || ROUND(exact_matches * 100.0 / total_models, 1)::text || '%)'
FROM stats
UNION ALL
SELECT
    'Slugified matches',
    slugified_matches::text || ' (' || ROUND(slugified_matches * 100.0 / total_models, 1)::text || '%)'
FROM stats
UNION ALL
SELECT
    'Different values',
    different_values::text || ' (' || ROUND(different_values * 100.0 / total_models, 1)::text || '%)'
FROM stats
UNION ALL
SELECT
    'Multi-provider discrepancies',
    discrepancy_count::text
FROM multi_provider_discrepancies
UNION ALL
SELECT
    '',
    ''
UNION ALL
SELECT
    '🎯 RECOMMENDATION',
    CASE
        WHEN different_values = 0 AND discrepancy_count = 0
            THEN '✅ SAFE TO REMOVE model_id column'
        WHEN different_values * 100.0 / total_models < 5 AND discrepancy_count = 0
            THEN '⚠️ PROBABLY SAFE - Review ' || different_values::text || ' different values first'
        WHEN discrepancy_count > 0
            THEN '❌ NOT SAFE - ' || discrepancy_count::text || ' multi-provider discrepancies found!'
        ELSE '❌ NOT SAFE - Too many different values (' || ROUND(different_values * 100.0 / total_models, 1)::text || '%)'
    END
FROM stats, multi_provider_discrepancies;

-- Step 2: Show examples of DIFFERENT values (if any exist)
SELECT
    '═══════════════════════════════════════════════════════' as divider,
    'EXAMPLES OF DIFFERENT VALUES (if this shows results, review them!)' as note;

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

-- Step 3: Show multi-provider discrepancies (if any exist)
SELECT
    '═══════════════════════════════════════════════════════' as divider,
    'MULTI-PROVIDER DISCREPANCIES (if this shows results, THIS IS CRITICAL!)' as note;

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
