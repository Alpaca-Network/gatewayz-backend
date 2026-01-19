-- ============================================================================
-- Debug: Check Models Table Data
-- ============================================================================

-- Step 1: Check if models table has any data at all
SELECT 'Total models' as check_name, COUNT(*)::TEXT as result
FROM models
UNION ALL
SELECT 'Models with pricing_prompt', COUNT(*)::TEXT
FROM models
WHERE pricing_prompt IS NOT NULL
UNION ALL
SELECT 'Models with pricing_completion', COUNT(*)::TEXT
FROM models
WHERE pricing_completion IS NOT NULL
UNION ALL
SELECT 'Models with ANY pricing', COUNT(*)::TEXT
FROM models
WHERE pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL;

-- Step 2: Show sample model structure
SELECT
    id,
    model_id,
    model_name,
    provider_id,
    top_provider,
    pricing_prompt,
    pricing_completion,
    pricing_image,
    pricing_request,
    metadata
FROM models
LIMIT 5;

-- Step 3: Check if there are providers
SELECT 'Total providers' as check_name, COUNT(*)::TEXT as result
FROM providers;

-- Step 4: Show what the populate function sees
SELECT
    m.id,
    m.pricing_prompt,
    m.pricing_completion,
    m.pricing_image,
    m.pricing_request,
    m.top_provider,
    m.metadata,
    COALESCE(p.slug, 'unknown') as provider_slug
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.pricing_prompt IS NOT NULL OR m.pricing_completion IS NOT NULL
LIMIT 5;
