-- ============================================================================
-- Analyze Models Without Pricing
-- ============================================================================

-- 1. Count by provider - which providers have models without pricing?
SELECT
    COALESCE(p.name, 'Unknown Provider') as provider_name,
    COUNT(*) as models_without_pricing
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.pricing_prompt IS NULL AND m.pricing_completion IS NULL
GROUP BY p.name
ORDER BY models_without_pricing DESC;

-- 2. Count by modality - are they specific types of models?
SELECT
    COALESCE(modality, 'unknown') as modality,
    COUNT(*) as models_without_pricing
FROM models
WHERE pricing_prompt IS NULL AND pricing_completion IS NULL
GROUP BY modality
ORDER BY models_without_pricing DESC;

-- 3. Sample of models without pricing
SELECT
    m.id,
    m.model_id,
    m.model_name,
    p.name as provider_name,
    m.modality,
    m.top_provider,
    m.is_active
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
WHERE m.pricing_prompt IS NULL AND m.pricing_completion IS NULL
LIMIT 20;

-- 4. Are they active or inactive?
SELECT
    is_active,
    COUNT(*) as count
FROM models
WHERE pricing_prompt IS NULL AND pricing_completion IS NULL
GROUP BY is_active;

-- 5. Check if any have pricing_request (per-request pricing instead of per-token)
SELECT
    'Models with ONLY request pricing (no per-token)' as category,
    COUNT(*) as count
FROM models
WHERE (pricing_prompt IS NULL AND pricing_completion IS NULL)
  AND pricing_request IS NOT NULL;
