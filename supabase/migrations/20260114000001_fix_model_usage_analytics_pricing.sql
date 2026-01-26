-- Migration: Fix model_usage_analytics view pricing calculation
-- Created: 2026-01-14
-- Description: Fix critical bug where pricing was divided by 1M instead of 1K,
-- causing all costs to be calculated 1000x too low.
-- Pricing in database is per 1,000 tokens (per 1K), not per 1,000,000 (per 1M).

-- Drop the existing view
DROP VIEW IF EXISTS "public"."model_usage_analytics";

-- Recreate the view with correct pricing calculation (divide by 1000, not 1000000)
CREATE VIEW "public"."model_usage_analytics" AS
SELECT
    -- Model identification
    m.id as model_id,
    m.model_name,
    m.model_id as model_identifier,
    m.provider_model_id,
    p.name as provider_name,
    p.slug as provider_slug,

    -- Request counts
    COUNT(ccr.id) as successful_requests,

    -- Token usage breakdown
    SUM(ccr.input_tokens) as total_input_tokens,
    SUM(ccr.output_tokens) as total_output_tokens,
    SUM(ccr.input_tokens + ccr.output_tokens) as total_tokens,

    -- Average token usage per request
    ROUND(AVG(ccr.input_tokens), 2) as avg_input_tokens_per_request,
    ROUND(AVG(ccr.output_tokens), 2) as avg_output_tokens_per_request,

    -- Pricing (per 1K tokens in USD)
    m.pricing_prompt as input_token_price_per_1k,
    m.pricing_completion as output_token_price_per_1k,

    -- Cost calculations (in USD) - FIXED: divide by 1000, not 1000000
    ROUND(
        CAST(
            (SUM(ccr.input_tokens) * COALESCE(m.pricing_prompt, 0) / 1000.0) +
            (SUM(ccr.output_tokens) * COALESCE(m.pricing_completion, 0) / 1000.0)
            AS NUMERIC
        ),
        6
    ) as total_cost_usd,

    -- Cost breakdown - FIXED: divide by 1000, not 1000000
    ROUND(
        CAST(SUM(ccr.input_tokens) * COALESCE(m.pricing_prompt, 0) / 1000.0 AS NUMERIC),
        6
    ) as input_cost_usd,
    ROUND(
        CAST(SUM(ccr.output_tokens) * COALESCE(m.pricing_completion, 0) / 1000.0 AS NUMERIC),
        6
    ) as output_cost_usd,

    -- Average cost per request - FIXED: divide by 1000, not 1000000
    ROUND(
        CAST(
            (SUM(ccr.input_tokens) * COALESCE(m.pricing_prompt, 0) / 1000.0) +
            (SUM(ccr.output_tokens) * COALESCE(m.pricing_completion, 0) / 1000.0)
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

    -- Time tracking
    MIN(ccr.created_at) as first_request_at,
    MAX(ccr.created_at) as last_request_at

FROM "public"."models" m
INNER JOIN "public"."providers" p ON m.provider_id = p.id
INNER JOIN "public"."chat_completion_requests" ccr ON m.id = ccr.model_id
WHERE ccr.status = 'completed'  -- Only successful requests
GROUP BY
    m.id,
    m.model_name,
    m.model_id,
    m.provider_model_id,
    m.pricing_prompt,
    m.pricing_completion,
    m.context_length,
    m.modality,
    m.health_status,
    m.is_active,
    p.name,
    p.slug
HAVING COUNT(ccr.id) > 0  -- Only models with at least 1 successful request
ORDER BY successful_requests DESC, total_cost_usd DESC;

-- Update comment to reflect correct pricing
COMMENT ON VIEW "public"."model_usage_analytics" IS
    'Comprehensive analytics view showing all models with at least one successful request. '
    'Includes request counts, token usage breakdown (input/output), pricing per 1K tokens, '
    'and calculated costs (total, input, output, per-request average). '
    'Updated in real-time as new requests are completed. '
    'Useful for cost analysis, usage tracking, and identifying most expensive/popular models. '
    'Note: Pricing is per 1,000 tokens (per 1K), not per 1,000,000 (per 1M).';

-- Log success
DO $$
BEGIN
    RAISE NOTICE 'Successfully fixed model_usage_analytics view pricing calculation (changed from /1000000 to /1000)';
END $$;
