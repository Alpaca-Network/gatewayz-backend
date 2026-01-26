-- ============================================================================
-- Test the exact query used in the populate function
-- ============================================================================

-- This is the SELECT query from the populate function
-- Let's see if it returns any data

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
LIMIT 10;

-- Check: Are there providers in the providers table?
SELECT 'Total providers' as check, COUNT(*)::TEXT as count
FROM providers;

-- Check: Do models have valid provider_id?
SELECT 'Models with provider_id' as check, COUNT(*)::TEXT as count
FROM models
WHERE provider_id IS NOT NULL;

-- Check: Models with pricing but NULL provider_id?
SELECT 'Models with pricing but NULL provider_id' as check, COUNT(*)::TEXT as count
FROM models
WHERE (pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL)
AND provider_id IS NULL;
