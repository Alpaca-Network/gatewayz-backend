-- ============================================================================
-- Fix Populate Model Pricing Function After top_provider Removal
-- ============================================================================
-- Update to remove top_provider column reference that was dropped
-- ============================================================================

CREATE OR REPLACE FUNCTION populate_model_pricing_table()
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
    v_model RECORD;
    v_provider_slug TEXT;
    v_input_price NUMERIC(20, 15);
    v_output_price NUMERIC(20, 15);
    v_image_price NUMERIC(20, 15);
BEGIN
    -- Loop through all models with provider info
    FOR v_model IN
        SELECT
            m.id,
            m.pricing_prompt,
            m.pricing_completion,
            m.pricing_image,
            m.pricing_request,
            m.metadata,
            COALESCE(p.slug, 'unknown') as provider_slug
        FROM models m
        LEFT JOIN providers p ON p.id = m.provider_id
    LOOP
        v_models_processed := v_models_processed + 1;

        -- Skip models without pricing
        IF v_model.pricing_prompt IS NULL AND v_model.pricing_completion IS NULL THEN
            v_records_skipped := v_records_skipped + 1;
            CONTINUE;
        END IF;

        -- Get provider slug (try metadata.source_gateway first, then provider_slug)
        v_provider_slug := COALESCE(
            LOWER(v_model.metadata->>'source_gateway'),
            LOWER(v_model.provider_slug),
            'unknown'
        );

        -- Normalize input price
        IF v_model.pricing_prompt IS NOT NULL THEN
            -- Auto-detect format from price value
            IF v_model.pricing_prompt >= 1 THEN
                -- Price >= 1 means per-1M tokens
                v_input_price := v_model.pricing_prompt / 1000000.0;
            ELSIF v_model.pricing_prompt >= 0.001 THEN
                -- Price >= 0.001 means per-1K tokens
                v_input_price := v_model.pricing_prompt / 1000.0;
            ELSE
                -- Already per-token
                v_input_price := v_model.pricing_prompt;
            END IF;
        ELSE
            v_input_price := 0;
        END IF;

        -- Normalize output price
        IF v_model.pricing_completion IS NOT NULL THEN
            IF v_model.pricing_completion >= 1 THEN
                v_output_price := v_model.pricing_completion / 1000000.0;
            ELSIF v_model.pricing_completion >= 0.001 THEN
                v_output_price := v_model.pricing_completion / 1000.0;
            ELSE
                v_output_price := v_model.pricing_completion;
            END IF;
        ELSE
            v_output_price := 0;
        END IF;

        -- Normalize image price
        IF v_model.pricing_image IS NOT NULL THEN
            IF v_model.pricing_image >= 1 THEN
                v_image_price := v_model.pricing_image / 1000000.0;
            ELSIF v_model.pricing_image >= 0.001 THEN
                v_image_price := v_model.pricing_image / 1000.0;
            ELSE
                v_image_price := v_model.pricing_image;
            END IF;
        ELSE
            v_image_price := NULL;
        END IF;

        -- Insert or update pricing
        INSERT INTO model_pricing (
            model_id,
            price_per_input_token,
            price_per_output_token,
            price_per_image_token,
            price_per_request,
            pricing_source
        ) VALUES (
            v_model.id,
            v_input_price,
            v_output_price,
            v_image_price,
            v_model.pricing_request,
            'provider'
        )
        ON CONFLICT (model_id)
        DO UPDATE SET
            price_per_input_token = EXCLUDED.price_per_input_token,
            price_per_output_token = EXCLUDED.price_per_output_token,
            price_per_image_token = EXCLUDED.price_per_image_token,
            price_per_request = EXCLUDED.price_per_request,
            pricing_source = EXCLUDED.pricing_source,
            last_updated = NOW();

        v_records_inserted := v_records_inserted + 1;
    END LOOP;

    RETURN QUERY SELECT v_models_processed, v_records_inserted, v_records_skipped;
END;
$$;

COMMENT ON FUNCTION populate_model_pricing_table() IS
    'Populates model_pricing table with normalized per-token pricing from models table (updated after top_provider removal)';

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ RPC function updated: populate_model_pricing_table()';
    RAISE NOTICE '   Fixed: Removed top_provider column reference';
    RAISE NOTICE '   Usage: SELECT * FROM populate_model_pricing_table();';
END$$;
