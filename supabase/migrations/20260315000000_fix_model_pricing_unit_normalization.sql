-- ============================================================================
-- Fix Model Pricing Unit Normalization
-- ============================================================================
-- Migration: 20260315000000
-- Description: Fixes pricing data that was stored in per-1M-token format
--              instead of per-token format in both the model_pricing table
--              and models.metadata.pricing_raw.
--
-- Root cause: model_catalog_sync.py did not normalize pricing from providers
-- that return per-1M-token prices (NEAR, DeepInfra, Fireworks, etc.) before
-- storing in metadata.pricing_raw. The model_usage_analytics view then
-- multiplied these already-large values by 1,000,000 for display, resulting
-- in $100B+ prices shown on the Model Analytics page.
--
-- Fix: Divide prices > 0.001 by 1,000,000 (per-1M → per-token conversion).
-- Per-token prices should be < 0.001 (even expensive models are ~$0.00006/token).
-- ============================================================================

-- Step 1: Diagnostic — count affected rows (logged via RAISE NOTICE)
DO $$
DECLARE
    affected_model_pricing INTEGER;
    affected_metadata INTEGER;
BEGIN
    SELECT COUNT(*) INTO affected_model_pricing
    FROM model_pricing
    WHERE price_per_input_token > 0.001 OR price_per_output_token > 0.001;

    SELECT COUNT(*) INTO affected_metadata
    FROM models
    WHERE metadata->'pricing_raw' IS NOT NULL
      AND (
        COALESCE((metadata->'pricing_raw'->>'prompt')::NUMERIC, 0) > 0.001
        OR COALESCE((metadata->'pricing_raw'->>'completion')::NUMERIC, 0) > 0.001
      );

    RAISE NOTICE '';
    RAISE NOTICE '==========================================';
    RAISE NOTICE 'Pricing unit normalization — pre-fix report';
    RAISE NOTICE '==========================================';
    RAISE NOTICE '  model_pricing rows with inflated prices: %', affected_model_pricing;
    RAISE NOTICE '  models rows with inflated pricing_raw:   %', affected_metadata;
    RAISE NOTICE '';
END $$;

-- Step 2: Fix model_pricing table
-- Divide inflated per-1M values by 1,000,000 to get per-token
UPDATE model_pricing
SET
    price_per_input_token = CASE
        WHEN price_per_input_token > 0.001
        THEN price_per_input_token / 1000000
        ELSE price_per_input_token
    END,
    price_per_output_token = CASE
        WHEN price_per_output_token > 0.001
        THEN price_per_output_token / 1000000
        ELSE price_per_output_token
    END,
    last_updated = NOW()
WHERE price_per_input_token > 0.001 OR price_per_output_token > 0.001;

-- Step 3: Fix models.metadata.pricing_raw (JSONB)
-- Only update rows where pricing_raw values are inflated
UPDATE models
SET metadata = jsonb_set(
    jsonb_set(
        metadata,
        '{pricing_raw,prompt}',
        to_jsonb(
            CASE
                WHEN COALESCE((metadata->'pricing_raw'->>'prompt')::NUMERIC, 0) > 0.001
                THEN (metadata->'pricing_raw'->>'prompt')::NUMERIC / 1000000
                ELSE COALESCE((metadata->'pricing_raw'->>'prompt')::NUMERIC, 0)
            END
        )::TEXT::JSONB
    ),
    '{pricing_raw,completion}',
    to_jsonb(
        CASE
            WHEN COALESCE((metadata->'pricing_raw'->>'completion')::NUMERIC, 0) > 0.001
            THEN (metadata->'pricing_raw'->>'completion')::NUMERIC / 1000000
            ELSE COALESCE((metadata->'pricing_raw'->>'completion')::NUMERIC, 0)
        END
    )::TEXT::JSONB
)
WHERE metadata->'pricing_raw' IS NOT NULL
  AND (
    COALESCE((metadata->'pricing_raw'->>'prompt')::NUMERIC, 0) > 0.001
    OR COALESCE((metadata->'pricing_raw'->>'completion')::NUMERIC, 0) > 0.001
  );

-- Step 4: Post-fix verification
DO $$
DECLARE
    remaining_model_pricing INTEGER;
    remaining_metadata INTEGER;
    sample_model TEXT;
    sample_input NUMERIC;
    sample_output NUMERIC;
BEGIN
    SELECT COUNT(*) INTO remaining_model_pricing
    FROM model_pricing
    WHERE price_per_input_token > 0.001 OR price_per_output_token > 0.001;

    SELECT COUNT(*) INTO remaining_metadata
    FROM models
    WHERE metadata->'pricing_raw' IS NOT NULL
      AND (
        COALESCE((metadata->'pricing_raw'->>'prompt')::NUMERIC, 0) > 0.001
        OR COALESCE((metadata->'pricing_raw'->>'completion')::NUMERIC, 0) > 0.001
      );

    -- Show a sample of fixed data
    SELECT m.model_name, mp.price_per_input_token, mp.price_per_output_token
    INTO sample_model, sample_input, sample_output
    FROM model_pricing mp
    JOIN models m ON m.id = mp.model_id
    WHERE mp.price_per_input_token > 0
    ORDER BY mp.price_per_input_token DESC
    LIMIT 1;

    RAISE NOTICE '';
    RAISE NOTICE '==========================================';
    RAISE NOTICE 'Pricing unit normalization — post-fix report';
    RAISE NOTICE '==========================================';
    RAISE NOTICE '  Remaining inflated model_pricing rows: %', remaining_model_pricing;
    RAISE NOTICE '  Remaining inflated metadata rows:      %', remaining_metadata;
    RAISE NOTICE '  Highest priced model: % (input: %, output: %)', sample_model, sample_input, sample_output;
    RAISE NOTICE '';

    IF remaining_model_pricing > 0 THEN
        RAISE WARNING 'Some model_pricing rows still have inflated prices — manual review needed';
    END IF;
END $$;
