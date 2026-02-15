-- ============================================================================
-- Backfill model_pricing from metadata.pricing_raw
-- ============================================================================
-- Migration: 20260212100000
-- Description: One-time backfill of the model_pricing table using pricing
--              data already stored in models.metadata->'pricing_raw'.
--
-- Background: The sync service stores per-token pricing in
--   models.metadata->'pricing_raw' but never populated model_pricing.
--   The model_usage_analytics view JOINs to model_pricing, so all costs
--   showed as $0. Going forward, bulk_upsert_models() now writes to
--   model_pricing after every sync. This migration backfills existing data.
-- ============================================================================

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
    m.id AS model_id,
    COALESCE(CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC), 0)
        AS price_per_input_token,
    COALESCE(CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC), 0)
        AS price_per_output_token,
    CAST(m.metadata->'pricing_raw'->>'image' AS NUMERIC)
        AS price_per_image_token,
    CAST(m.metadata->'pricing_raw'->>'request' AS NUMERIC)
        AS price_per_request,
    'provider' AS pricing_source,
    CASE
        WHEN COALESCE(CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC), 0) > 0
          OR COALESCE(CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC), 0) > 0
        THEN 'paid'
        ELSE 'free'
    END AS pricing_type
FROM models m
WHERE m.metadata->'pricing_raw' IS NOT NULL
  AND m.metadata->'pricing_raw'->>'prompt' IS NOT NULL
ON CONFLICT (model_id)
DO UPDATE SET
    price_per_input_token  = EXCLUDED.price_per_input_token,
    price_per_output_token = EXCLUDED.price_per_output_token,
    price_per_image_token  = EXCLUDED.price_per_image_token,
    price_per_request      = EXCLUDED.price_per_request,
    pricing_source         = EXCLUDED.pricing_source,
    pricing_type           = EXCLUDED.pricing_type,
    last_updated           = NOW();

-- Report results
DO $$
DECLARE
    total_rows INTEGER;
    paid_rows  INTEGER;
    free_rows  INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_rows FROM model_pricing;
    SELECT COUNT(*) INTO paid_rows  FROM model_pricing WHERE pricing_type = 'paid';
    SELECT COUNT(*) INTO free_rows  FROM model_pricing WHERE pricing_type = 'free';

    RAISE NOTICE '';
    RAISE NOTICE '==========================================';
    RAISE NOTICE 'model_pricing backfill complete';
    RAISE NOTICE '==========================================';
    RAISE NOTICE '  Total entries: %', total_rows;
    RAISE NOTICE '  Paid models:   %', paid_rows;
    RAISE NOTICE '  Free models:   %', free_rows;
    RAISE NOTICE '';
END $$;
