-- ============================================================================
-- Add Pricing Classification to model_pricing table
-- ============================================================================
-- Adds a field to distinguish: paid, free, deprecated, missing
-- ============================================================================

-- Add pricing_type column to model_pricing
ALTER TABLE model_pricing
ADD COLUMN IF NOT EXISTS pricing_type TEXT DEFAULT 'paid'
CHECK (pricing_type IN ('paid', 'free', 'deprecated', 'missing'));

COMMENT ON COLUMN model_pricing.pricing_type IS
    'Classification: paid (has pricing), free (confirmed free), deprecated (inactive), missing (unknown)';

-- Create index for filtering by type
CREATE INDEX IF NOT EXISTS idx_model_pricing_type ON model_pricing(pricing_type);

-- ============================================================================
-- Create function to populate with classification
-- ============================================================================

CREATE OR REPLACE FUNCTION populate_model_pricing_with_classification()
RETURNS TABLE (
    models_processed INTEGER,
    paid_models INTEGER,
    free_models INTEGER,
    skipped INTEGER
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_models_processed INTEGER := 0;
    v_paid_models INTEGER := 0;
    v_free_models INTEGER := 0;
    v_skipped INTEGER := 0;
BEGIN
    -- Insert PAID models (have pricing data)
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
        'provider' as pricing_source,
        'paid' as pricing_type
    FROM models
    WHERE (pricing_prompt IS NOT NULL OR pricing_completion IS NOT NULL)
      AND (pricing_prompt > 0 OR pricing_completion > 0)  -- Actually has a cost
    ON CONFLICT (model_id)
    DO UPDATE SET
        price_per_input_token = EXCLUDED.price_per_input_token,
        price_per_output_token = EXCLUDED.price_per_output_token,
        price_per_image_token = EXCLUDED.price_per_image_token,
        price_per_request = EXCLUDED.price_per_request,
        pricing_source = EXCLUDED.pricing_source,
        pricing_type = EXCLUDED.pricing_type,
        last_updated = NOW();

    GET DIAGNOSTICS v_paid_models = ROW_COUNT;

    -- Insert FREE models (active, no pricing, from known free providers)
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
        'inferred' as pricing_source,
        'free' as pricing_type
    FROM models m
    LEFT JOIN providers p ON p.id = m.provider_id
    WHERE m.pricing_prompt IS NULL
      AND m.pricing_completion IS NULL
      AND m.is_active = true
      AND (
        -- Known free providers
        LOWER(p.slug) IN ('huggingface', 'featherless') OR
        LOWER(m.top_provider) IN ('huggingface', 'featherless') OR
        -- Free indicators in name/description
        LOWER(m.model_name) LIKE '%free%' OR
        LOWER(m.description) LIKE '%free%' OR
        LOWER(m.metadata::text) LIKE '%free%'
      )
    ON CONFLICT (model_id)
    DO UPDATE SET
        pricing_type = EXCLUDED.pricing_type,
        pricing_source = EXCLUDED.pricing_source,
        last_updated = NOW()
    WHERE model_pricing.pricing_type != 'paid';  -- Don't override paid models

    GET DIAGNOSTICS v_free_models = ROW_COUNT;

    -- Count total and skipped
    SELECT COUNT(*) INTO v_models_processed FROM models;
    v_skipped := v_models_processed - v_paid_models - v_free_models;

    RETURN QUERY SELECT v_models_processed, v_paid_models, v_free_models, v_skipped;
END;
$$;

COMMENT ON FUNCTION populate_model_pricing_with_classification() IS
    'Populates model_pricing with classification: paid (has pricing), free (inferred from provider/name), missing/deprecated (rest)';

-- ============================================================================
-- Create view for easy filtering
-- ============================================================================

CREATE OR REPLACE VIEW models_pricing_classified AS
SELECT
    m.*,
    mp.price_per_input_token,
    mp.price_per_output_token,
    mp.price_per_image_token,
    mp.price_per_request,
    mp.pricing_type,
    mp.pricing_source,
    mp.last_updated as pricing_last_updated,
    CASE
        WHEN mp.pricing_type = 'paid' THEN 'Paid Model'
        WHEN mp.pricing_type = 'free' THEN 'Free Model'
        WHEN mp.pricing_type = 'deprecated' THEN 'Deprecated'
        WHEN m.is_active = false THEN 'Inactive (Likely Deprecated)'
        ELSE 'Missing Pricing Data'
    END as pricing_classification
FROM models m
LEFT JOIN model_pricing mp ON m.id = mp.model_id;

COMMENT ON VIEW models_pricing_classified IS
    'Models with pricing classification for easy filtering';

-- Grant permissions
GRANT SELECT ON models_pricing_classified TO authenticated;
GRANT SELECT ON models_pricing_classified TO anon;
GRANT SELECT ON models_pricing_classified TO service_role;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Pricing classification added';
    RAISE NOTICE '   • Added pricing_type column (paid/free/deprecated/missing)';
    RAISE NOTICE '   • Created populate_model_pricing_with_classification() function';
    RAISE NOTICE '   • Created models_pricing_classified view';
    RAISE NOTICE '';
    RAISE NOTICE '   Usage: SELECT * FROM populate_model_pricing_with_classification();';
END$$;
