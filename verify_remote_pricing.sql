-- Quick verification of remote database pricing migration
-- Run with: supabase db remote exec --linked -f verify_remote_pricing.sql

\echo 'Pricing Migration Verification (Remote Database)'
\echo '================================================'
\echo ''

-- Check if migration column exists
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'models'
    AND column_name = 'pricing_format_migrated'
) as "Migration Column Exists?";

\echo ''

-- Check pricing distribution
SELECT
    CASE
        WHEN pricing_prompt > 0.001 THEN 'Per-1M (NOT migrated)'
        WHEN pricing_prompt >= 0.000001 AND pricing_prompt <= 0.001 THEN 'Per-1K (NOT migrated)'
        WHEN pricing_prompt < 0.000001 AND pricing_prompt > 0 THEN 'Per-token (MIGRATED)'
        ELSE 'Zero/NULL'
    END as format_status,
    COUNT(*) as count,
    ROUND((COUNT(*)::FLOAT / SUM(COUNT(*)) OVER() * 100)::NUMERIC, 1) as percentage
FROM "public"."models"
WHERE pricing_prompt IS NOT NULL
GROUP BY format_status
ORDER BY count DESC;

\echo ''

-- Sample lowest prices
SELECT
    id,
    ROUND(pricing_prompt::NUMERIC, 12) as price
FROM "public"."models"
WHERE pricing_prompt > 0
ORDER BY pricing_prompt ASC
LIMIT 5;
