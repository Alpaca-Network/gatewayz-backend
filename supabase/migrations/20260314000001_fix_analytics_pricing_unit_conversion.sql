-- ============================================================================
-- Fix Model Usage Analytics View: Correct pricing_raw Unit Conversion
-- ============================================================================
-- Migration: 20260314000001
-- Description: Fixes cost inflation ($156B averages) caused by treating
--              metadata.pricing_raw values as per-token when they are actually
--              per-1M-token. Divides pricing_raw fallback by 1,000,000.
--
-- Root cause: metadata->'pricing_raw'->>'prompt' stores per-1M-token pricing
-- (e.g. 0.15 = $0.15 per 1M tokens), but the previous view used these values
-- directly as per-token prices, inflating costs by 1,000,000x.
--
-- Fix: Divide metadata.pricing_raw values by 1,000,000 to convert to per-token.
-- model_pricing.price_per_input_token is already per-token, so no change needed.
-- ============================================================================

-- Drop and recreate the view
DROP VIEW IF EXISTS "public"."model_usage_analytics";

CREATE VIEW "public"."model_usage_analytics" AS
SELECT
    -- Model identification
    m.id as model_id,
    m.model_name,
    m.provider_model_id,
    p.name as provider_name,
    p.slug as provider_slug,

    -- Request counts
    COUNT(ccr.id) as successful_requests,

    -- Token usage breakdown
    COALESCE(SUM(ccr.input_tokens), 0) as total_input_tokens,
    COALESCE(SUM(ccr.output_tokens), 0) as total_output_tokens,
    COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0) as total_tokens,

    -- Average token usage per request
    ROUND(AVG(ccr.input_tokens), 2) as avg_input_tokens_per_request,
    ROUND(AVG(ccr.output_tokens), 2) as avg_output_tokens_per_request,

    -- Pricing (per 1M tokens for display)
    -- model_pricing stores per-token → multiply by 1M for display
    -- metadata.pricing_raw stores per-1M-token → use directly for display
    COALESCE(
        mp.price_per_input_token * 1000000,
        CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC),
        0
    ) as input_token_price_per_1m,
    COALESCE(
        mp.price_per_output_token * 1000000,
        CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC),
        0
    ) as output_token_price_per_1m,

    -- Per-token pricing (raw, for cost calculations)
    -- model_pricing is already per-token
    -- metadata.pricing_raw is per-1M-token → divide by 1,000,000
    COALESCE(
        mp.price_per_input_token,
        CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC) / 1000000,
        0
    ) as input_token_price,
    COALESCE(
        mp.price_per_output_token,
        CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC) / 1000000,
        0
    ) as output_token_price,

    -- Cost calculations (in USD)
    -- Uses per-token price × token count
    ROUND(
        CAST(
            (COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(
                mp.price_per_input_token,
                CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC) / 1000000,
                0
            )) +
            (COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(
                mp.price_per_output_token,
                CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC) / 1000000,
                0
            ))
            AS NUMERIC
        ),
        6
    ) as total_cost_usd,

    -- Cost breakdown
    ROUND(
        CAST(COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(
            mp.price_per_input_token,
            CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC) / 1000000,
            0
        ) AS NUMERIC),
        6
    ) as input_cost_usd,
    ROUND(
        CAST(COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(
            mp.price_per_output_token,
            CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC) / 1000000,
            0
        ) AS NUMERIC),
        6
    ) as output_cost_usd,

    -- Average cost per request
    ROUND(
        CAST(
            (COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(
                mp.price_per_input_token,
                CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC) / 1000000,
                0
            )) +
            (COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(
                mp.price_per_output_token,
                CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC) / 1000000,
                0
            ))
            AS NUMERIC
        ) / NULLIF(COUNT(ccr.id), 0),
        6
    ) as avg_cost_per_request_usd,

    -- Performance metrics
    ROUND(AVG(ccr.processing_time_ms), 2) as avg_processing_time_ms,

    -- Model metadata
    m.context_length,
    m.modality,
    m.health_status,
    m.is_active,

    -- Pricing metadata
    mp.pricing_type,
    mp.pricing_source,

    -- Time tracking
    MIN(ccr.created_at) as first_request_at,
    MAX(ccr.created_at) as last_request_at

FROM "public"."models" m
INNER JOIN "public"."providers" p ON m.provider_id = p.id
INNER JOIN "public"."chat_completion_requests" ccr ON m.id = ccr.model_id
LEFT JOIN "public"."model_pricing" mp ON m.id = mp.model_id
WHERE ccr.status = 'completed'
GROUP BY
    m.id,
    m.model_name,
    m.provider_model_id,
    m.metadata,
    m.context_length,
    m.modality,
    m.health_status,
    m.is_active,
    p.name,
    p.slug,
    mp.price_per_input_token,
    mp.price_per_output_token,
    mp.pricing_type,
    mp.pricing_source
HAVING COUNT(ccr.id) > 0
ORDER BY successful_requests DESC, total_cost_usd DESC;

-- Add comment
COMMENT ON VIEW "public"."model_usage_analytics" IS
    'Model usage analytics with corrected pricing. '
    'metadata.pricing_raw is per-1M-token; divided by 1,000,000 for per-token cost calculations. '
    'model_pricing.price_per_input/output_token is already per-token. '
    'Updated 2026-03-14 to fix 1M× cost inflation.';

-- Grant permissions
GRANT SELECT ON "public"."model_usage_analytics" TO authenticated;
GRANT SELECT ON "public"."model_usage_analytics" TO anon;
GRANT SELECT ON "public"."model_usage_analytics" TO service_role;
