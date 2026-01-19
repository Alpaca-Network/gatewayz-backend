-- ============================================================================
-- Direct SQL to Populate Model Pricing Table
-- Run this in Supabase SQL Editor to bypass PostgREST schema cache
-- ============================================================================

-- Step 1: Call the RPC function we created
SELECT * FROM populate_model_pricing_table();

-- Step 2: Verify the results
SELECT
    'Total records in model_pricing' as metric,
    COUNT(*)::TEXT as value
FROM model_pricing
UNION ALL
SELECT
    'Models with pricing',
    COUNT(*)::TEXT
FROM model_pricing
WHERE price_per_input_token > 0 OR price_per_output_token > 0
UNION ALL
SELECT
    'Average input price per token',
    TO_CHAR(AVG(price_per_input_token), 'FM0.000000000000')
FROM model_pricing
WHERE price_per_input_token > 0
UNION ALL
SELECT
    'Average output price per token',
    TO_CHAR(AVG(price_per_output_token), 'FM0.000000000000')
FROM model_pricing
WHERE price_per_output_token > 0;

-- Step 3: Show sample records
SELECT
    mp.model_id,
    m.model_name,
    m.source_gateway,
    mp.price_per_input_token,
    mp.price_per_output_token,
    mp.pricing_source
FROM model_pricing mp
JOIN models m ON m.id = mp.model_id
ORDER BY mp.price_per_input_token DESC
LIMIT 10;
