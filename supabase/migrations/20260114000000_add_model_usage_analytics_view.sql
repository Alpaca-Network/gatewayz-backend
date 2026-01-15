-- Migration: Create model_usage_analytics view
-- Created: 2026-01-14
-- Description: Comprehensive view showing model usage statistics with pricing and cost calculations
-- for all models that have at least one successful request.

-- Drop view if it exists (for idempotency)
DROP VIEW IF EXISTS "public"."model_usage_analytics";

-- Create the model_usage_analytics view
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

    -- Pricing (per 1M tokens in USD)
    m.pricing_prompt as input_token_price_per_1m,
    m.pricing_completion as output_token_price_per_1m,

    -- Cost calculations (in USD)
    ROUND(
        CAST(
            (SUM(ccr.input_tokens) * COALESCE(m.pricing_prompt, 0) / 1000000.0) +
            (SUM(ccr.output_tokens) * COALESCE(m.pricing_completion, 0) / 1000000.0)
            AS NUMERIC
        ),
        6
    ) as total_cost_usd,

    -- Cost breakdown
    ROUND(
        CAST(SUM(ccr.input_tokens) * COALESCE(m.pricing_prompt, 0) / 1000000.0 AS NUMERIC),
        6
    ) as input_cost_usd,
    ROUND(
        CAST(SUM(ccr.output_tokens) * COALESCE(m.pricing_completion, 0) / 1000000.0 AS NUMERIC),
        6
    ) as output_cost_usd,

    -- Average cost per request
    ROUND(
        CAST(
            (SUM(ccr.input_tokens) * COALESCE(m.pricing_prompt, 0) / 1000000.0) +
            (SUM(ccr.output_tokens) * COALESCE(m.pricing_completion, 0) / 1000000.0)
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

-- Add helpful comment
COMMENT ON VIEW "public"."model_usage_analytics" IS
    'Comprehensive analytics view showing all models with at least one successful request. '
    'Includes request counts, token usage breakdown (input/output), pricing per 1M tokens, '
    'and calculated costs (total, input, output, per-request average). '
    'Updated in real-time as new requests are completed. '
    'Useful for cost analysis, usage tracking, and identifying most expensive/popular models.';

-- Create indexes on underlying tables to optimize view queries (if not already exist)
-- These improve performance when querying the view
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_id_status"
    ON "public"."chat_completion_requests" ("model_id", "status");

CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_status_created_at"
    ON "public"."chat_completion_requests" ("status", "created_at");

CREATE INDEX IF NOT EXISTS "idx_models_pricing"
    ON "public"."models" ("pricing_prompt", "pricing_completion");

-- Grant appropriate permissions
GRANT SELECT ON "public"."model_usage_analytics" TO authenticated;
GRANT SELECT ON "public"."model_usage_analytics" TO anon;

-- Log success
DO $$
BEGIN
    RAISE NOTICE 'Successfully created model_usage_analytics view with comprehensive cost and usage tracking';
END $$;
