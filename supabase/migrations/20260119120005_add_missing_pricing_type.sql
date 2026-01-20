-- ============================================================================
-- Add Active Models Without Pricing as 'missing' Type
-- ============================================================================
-- This ensures ALL active models are in the pricing table, even if we
-- don't have pricing data for them yet
-- ============================================================================

CREATE OR REPLACE FUNCTION add_active_models_as_missing()
RETURNS TABLE (
    added_count INTEGER,
    already_classified INTEGER
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_added INTEGER := 0;
    v_already_classified INTEGER := 0;
BEGIN
    -- Count how many are already in pricing table
    SELECT COUNT(*) INTO v_already_classified
    FROM models m
    INNER JOIN model_pricing mp ON mp.model_id = m.id
    WHERE m.is_active = true;

    -- Insert active models that aren't in pricing table yet as 'missing'
    INSERT INTO model_pricing (
        model_id,
        price_per_input_token,
        price_per_output_token,
        price_per_image_token,
        price_per_request,
        pricing_source,
        pricing_type
    )
    SELECT
        m.id as model_id,
        0 as price_per_input_token,
        0 as price_per_output_token,
        NULL as price_per_image_token,
        NULL as price_per_request,
        'unknown' as pricing_source,
        'missing' as pricing_type
    FROM models m
    LEFT JOIN model_pricing mp ON mp.model_id = m.id
    WHERE m.is_active = true
      AND mp.model_id IS NULL
      AND m.pricing_prompt IS NULL
      AND m.pricing_completion IS NULL
    ON CONFLICT (model_id) DO NOTHING;

    GET DIAGNOSTICS v_added = ROW_COUNT;

    RETURN QUERY SELECT v_added, v_already_classified;
END;
$$;

COMMENT ON FUNCTION add_active_models_as_missing() IS
    'Adds all active models without pricing to pricing table with type=missing';

-- ============================================================================
-- Create comprehensive view showing all model pricing status
-- ============================================================================

CREATE OR REPLACE VIEW models_pricing_status AS
SELECT
    m.id,
    m.model_id,
    m.model_name,
    p.name as provider_name,
    m.is_active,
    m.health_status,
    m.last_health_check_at,
    mp.pricing_type,
    mp.price_per_input_token,
    mp.price_per_output_token,
    CASE
        WHEN mp.pricing_type = 'paid' THEN '💰 Paid'
        WHEN mp.pricing_type = 'free' THEN '🆓 Free'
        WHEN mp.pricing_type = 'deprecated' THEN '🗑️ Deprecated'
        WHEN mp.pricing_type = 'missing' THEN '❓ Missing Pricing'
        WHEN m.is_active = false THEN '⏸️ Inactive'
        WHEN mp.model_id IS NULL THEN '❌ Not Classified'
        ELSE '❓ Unknown'
    END as status_display,
    CASE
        WHEN mp.model_id IS NULL THEN 'Not in pricing table'
        WHEN mp.pricing_type = 'paid' THEN 'Has pricing data'
        WHEN mp.pricing_type = 'free' THEN 'Confirmed free model'
        WHEN mp.pricing_type = 'missing' THEN 'Active but pricing unknown'
        WHEN mp.pricing_type = 'deprecated' THEN 'No longer available'
        ELSE 'Unknown'
    END as status_description
FROM models m
LEFT JOIN providers p ON p.id = m.provider_id
LEFT JOIN model_pricing mp ON mp.model_id = m.id;

COMMENT ON VIEW models_pricing_status IS
    'Comprehensive view showing pricing status for all models';

-- Grant permissions
GRANT SELECT ON models_pricing_status TO authenticated;
GRANT SELECT ON models_pricing_status TO anon;
GRANT SELECT ON models_pricing_status TO service_role;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Missing pricing type handler added';
    RAISE NOTICE '   • Created add_active_models_as_missing() function';
    RAISE NOTICE '   • Created models_pricing_status view';
    RAISE NOTICE '';
    RAISE NOTICE '   Usage: SELECT * FROM add_active_models_as_missing();';
    RAISE NOTICE '   View: SELECT * FROM models_pricing_status WHERE status_display LIKE ''❓%%'';';
END$$;
