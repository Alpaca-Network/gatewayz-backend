-- ============================================================================
-- Analyze the 1,041 Skipped Models
-- ============================================================================

-- 1. Are they active or inactive?
SELECT
    CASE WHEN is_active THEN 'Active' ELSE 'Inactive' END as status,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) as percentage
FROM models m
LEFT JOIN model_pricing mp ON mp.model_id = m.id
WHERE mp.model_id IS NULL  -- Not in pricing table = skipped
GROUP BY is_active
ORDER BY count DESC;

-- 2. Breakdown by provider
SELECT
    COALESCE(p.name, 'Unknown') as provider,
    COUNT(*) as skipped_count,
    COUNT(*) FILTER (WHERE m.is_active = true) as active,
    COUNT(*) FILTER (WHERE m.is_active = false) as inactive,
    COUNT(*) FILTER (WHERE m.health_status = 'down') as health_down
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
LEFT JOIN model_pricing mp ON mp.model_id = m.id
WHERE mp.model_id IS NULL
GROUP BY p.name
ORDER BY skipped_count DESC
LIMIT 15;

-- 3. Check health status
SELECT
    COALESCE(health_status, 'unknown') as health,
    COUNT(*) as count
FROM models m
LEFT JOIN model_pricing mp ON mp.model_id = m.id
WHERE mp.model_id IS NULL
GROUP BY health_status
ORDER BY count DESC;

-- 4. When were they last checked?
SELECT
    CASE
        WHEN last_health_check_at IS NULL THEN 'Never checked'
        WHEN last_health_check_at < NOW() - INTERVAL '90 days' THEN '90+ days ago'
        WHEN last_health_check_at < NOW() - INTERVAL '60 days' THEN '60-90 days ago'
        WHEN last_health_check_at < NOW() - INTERVAL '30 days' THEN '30-60 days ago'
        ELSE 'Within 30 days'
    END as last_check,
    COUNT(*) as count
FROM models m
LEFT JOIN model_pricing mp ON mp.model_id = m.id
WHERE mp.model_id IS NULL
GROUP BY last_check
ORDER BY count DESC;

-- 5. Sample of ACTIVE skipped models (these need investigation)
SELECT
    m.id,
    m.model_name,
    p.name as provider,
    m.is_active,
    m.health_status,
    m.last_health_check_at,
    m.created_at,
    m.pricing_prompt,
    m.pricing_completion
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
LEFT JOIN model_pricing mp ON mp.model_id = m.id
WHERE mp.model_id IS NULL
  AND m.is_active = true
ORDER BY m.created_at DESC
LIMIT 20;

-- 6. Sample of INACTIVE skipped models (truly deprecated)
SELECT
    m.id,
    m.model_name,
    p.name as provider,
    m.is_active,
    m.health_status,
    m.last_health_check_at
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
LEFT JOIN model_pricing mp ON mp.model_id = m.id
WHERE mp.model_id IS NULL
  AND m.is_active = false
LIMIT 20;
