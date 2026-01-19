-- ============================================================================
-- Simplified Populate Function (No JOIN, Direct Insert)
-- ============================================================================
-- This version doesn't join with providers table and processes all models
-- ============================================================================

CREATE OR REPLACE FUNCTION populate_model_pricing_simple()
RETURNS TABLE (
    models_processed INTEGER,
    records_inserted INTEGER,
    records_skipped INTEGER
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_models_processed INTEGER := 0;
    v_records_inserted INTEGER := 0;
    v_records_skipped INTEGER := 0;
    v_input_price NUMERIC(20, 15);
    v_output_price NUMERIC(20, 15);
    v_image_price NUMERIC(20, 15);
BEGIN
    -- Direct insert without looping
    -- This is much faster and simpler
    INSERT INTO model_pricing (
        model_id,
        price_per_input_token,
        price_per_output_token,
        price_per_image_token,
        price_per_request,
        pricing_source
    )
    SELECT
        id as model_id,
        -- Normalize input price
        CASE
            WHEN pricing_prompt >= 1 THEN pricing_prompt / 1000000.0
            WHEN pricing_prompt >= 0.001 THEN pricing_prompt / 1000.0
            WHEN pricing_prompt IS NOT NULL THEN pricing_prompt
            ELSE 0
        END as price_per_input_token,
        -- Normalize output price
        CASE
            WHEN pricing_completion >= 1 THEN pricing_completion / 1000000.0
            WHEN pricing_completion >= 0.001 THEN pricing_completion / 1000.0
            WHEN pricing_completion IS NOT NULL THEN pricing_completion
            ELSE 0
        END as price_per_output_token,
        -- Normalize image price
        CASE
            WHEN pricing_image >= 1 THEN pricing_image / 1000000.0
            WHEN pricing_image >= 0.001 THEN pricing_image / 1000.0
            ELSE pricing_image
        END as price_per_image_token,
        pricing_request as price_per_request,
        'provider' as pricing_source
    FROM models
    WHERE pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL
    ON CONFLICT (model_id)
    DO UPDATE SET
        price_per_input_token = EXCLUDED.price_per_input_token,
        price_per_output_token = EXCLUDED.price_per_output_token,
        price_per_image_token = EXCLUDED.price_per_image_token,
        price_per_request = EXCLUDED.price_per_request,
        pricing_source = EXCLUDED.pricing_source,
        last_updated = NOW();

    -- Get counts
    GET DIAGNOSTICS v_records_inserted = ROW_COUNT;

    SELECT COUNT(*) INTO v_models_processed
    FROM models;

    SELECT COUNT(*) INTO v_records_skipped
    FROM models
    WHERE pricing_prompt IS NULL AND pricing_completion IS NULL;

    RETURN QUERY SELECT v_models_processed, v_records_inserted, v_records_skipped;
END;
$$;

COMMENT ON FUNCTION populate_model_pricing_simple() IS
    'Simplified populate function using direct INSERT SELECT (faster and more reliable)';

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Simplified RPC function created: populate_model_pricing_simple()';
    RAISE NOTICE '   This version uses direct INSERT SELECT for better performance';
    RAISE NOTICE '   Usage: SELECT * FROM populate_model_pricing_simple();';
END$$;
