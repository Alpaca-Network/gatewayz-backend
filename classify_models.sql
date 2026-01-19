-- ============================================================================
-- Classify Models: Free vs Deprecated vs Missing Pricing
-- ============================================================================

-- 1. Check is_active status
SELECT
    'Active models without pricing' as category,
    COUNT(*) as count
FROM models
WHERE (pricing_prompt IS NULL AND pricing_completion IS NULL)
  AND is_active = true
UNION ALL
SELECT
    'Inactive models without pricing',
    COUNT(*)
FROM models
WHERE (pricing_prompt IS NULL AND pricing_completion IS NULL)
  AND is_active = false;

-- 2. Analyze by provider (likely free providers)
SELECT
    COALESCE(p.name, 'Unknown') as provider,
    COUNT(CASE WHEN m.is_active THEN 1 END) as active_no_pricing,
    COUNT(CASE WHEN NOT m.is_active THEN 1 END) as inactive_no_pricing,
    COUNT(*) as total_no_pricing
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.pricing_prompt IS NULL AND m.pricing_completion IS NULL
GROUP BY p.name
ORDER BY total_no_pricing DESC
LIMIT 15;

-- 3. Check for "free" indicators in metadata
SELECT
    'Models with free in metadata' as category,
    COUNT(*) as count
FROM models
WHERE (pricing_prompt IS NULL AND pricing_completion IS NULL)
  AND (
    LOWER(metadata::text) LIKE '%free%' OR
    LOWER(model_name) LIKE '%free%' OR
    LOWER(description) LIKE '%free%'
  );

-- 4. Check last update/health check (deprecated indicators)
SELECT
    'No health check in last 30 days' as category,
    COUNT(*) as count
FROM models
WHERE (pricing_prompt IS NULL AND pricing_completion IS NULL)
  AND (
    last_health_check_at IS NULL OR
    last_health_check_at < NOW() - INTERVAL '30 days'
  )
  AND is_active = true;

-- 5. Sample of likely FREE models (active, reputable provider)
SELECT
    m.id,
    m.model_name,
    p.name as provider,
    m.is_active,
    m.last_health_check_at,
    m.created_at
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE (m.pricing_prompt IS NULL AND m.pricing_completion IS NULL)
  AND m.is_active = true
  AND p.name IN ('HuggingFace', 'Featherless', 'OpenRouter')
ORDER BY m.created_at DESC
LIMIT 10;

-- 6. Sample of likely DEPRECATED models (inactive or old)
SELECT
    m.id,
    m.model_name,
    p.name as provider,
    m.is_active,
    m.last_health_check_at,
    m.health_status,
    m.created_at
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE (m.pricing_prompt IS NULL AND m.pricing_completion IS NULL)
  AND (
    m.is_active = false OR
    m.health_status = 'down' OR
    m.last_health_check_at < NOW() - INTERVAL '60 days'
  )
ORDER BY m.last_health_check_at ASC NULLS FIRST
LIMIT 10;
