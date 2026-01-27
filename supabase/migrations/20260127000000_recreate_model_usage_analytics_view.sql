-- ============================================================================
-- Recreate Model Usage Analytics View
-- ============================================================================
-- Migration: 20260127000000
-- Description: Recreates the model_usage_analytics view using the new
--              model_pricing table after the pricing consolidation migration
--              (20260121000003) dropped it without recreating.
--
-- This view provides comprehensive analytics for model usage including:
--   - Request counts
--   - Token usage (input/output totals and averages)
--   - Cost calculations based on model_pricing table
--   - Performance metrics
--   - Time-series data for usage over time
-- ============================================================================

-- Drop view if it exists (for idempotency)
DROP VIEW IF EXISTS "public"."model_usage_analytics";

-- Create the model_usage_analytics view using model_pricing table
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
    COALESCE(SUM(ccr.input_tokens), 0) as total_input_tokens,
    COALESCE(SUM(ccr.output_tokens), 0) as total_output_tokens,
    COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0) as total_tokens,

    -- Average token usage per request
    ROUND(AVG(ccr.input_tokens), 2) as avg_input_tokens_per_request,
    ROUND(AVG(ccr.output_tokens), 2) as avg_output_tokens_per_request,

    -- Pricing (per 1M tokens for display - derived from per-token pricing)
    -- model_pricing stores per-token, multiply by 1,000,000 for per-1M display
    -- This matches the original view format for backward compatibility
    COALESCE(mp.price_per_input_token * 1000000, 0) as input_token_price_per_1m,
    COALESCE(mp.price_per_output_token * 1000000, 0) as output_token_price_per_1m,

    -- Cost calculations (in USD)
    -- model_pricing stores per-token pricing, so multiply tokens directly
    ROUND(
        CAST(
            (COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(mp.price_per_input_token, 0)) +
            (COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(mp.price_per_output_token, 0))
            AS NUMERIC
        ),
        6
    ) as total_cost_usd,

    -- Cost breakdown
    ROUND(
        CAST(COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(mp.price_per_input_token, 0) AS NUMERIC),
        6
    ) as input_cost_usd,
    ROUND(
        CAST(COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(mp.price_per_output_token, 0) AS NUMERIC),
        6
    ) as output_cost_usd,

    -- Average cost per request
    ROUND(
        CAST(
            (COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(mp.price_per_input_token, 0)) +
            (COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(mp.price_per_output_token, 0))
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
WHERE ccr.status = 'completed'  -- Only successful requests
GROUP BY
    m.id,
    m.model_name,
    m.model_id,
    m.provider_model_id,
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
HAVING COUNT(ccr.id) > 0  -- Only models with at least 1 successful request
ORDER BY successful_requests DESC, total_cost_usd DESC;

-- Add helpful comment
COMMENT ON VIEW "public"."model_usage_analytics" IS
    'Comprehensive analytics view showing all models with at least one successful request. '
    'Includes request counts, token usage breakdown (input/output), pricing from model_pricing table, '
    'and calculated costs (total, input, output, per-request average). '
    'Updated in real-time as new requests are completed. '
    'Useful for cost analysis, usage tracking, and identifying most expensive/popular models. '
    'Updated 2026-01-27 to use model_pricing table after pricing consolidation.';

-- Grant appropriate permissions
GRANT SELECT ON "public"."model_usage_analytics" TO authenticated;
GRANT SELECT ON "public"."model_usage_analytics" TO anon;
GRANT SELECT ON "public"."model_usage_analytics" TO service_role;

-- Log success
DO $$
DECLARE
    view_exists BOOLEAN;
    model_count INTEGER;
BEGIN
    -- Check if view was created successfully
    SELECT EXISTS (
        SELECT 1 FROM information_schema.views
        WHERE table_schema = 'public'
          AND table_name = 'model_usage_analytics'
    ) INTO view_exists;

    IF view_exists THEN
        -- Count models in the view
        SELECT COUNT(*) INTO model_count FROM model_usage_analytics;

        RAISE NOTICE '';
        RAISE NOTICE '========================================';
        RAISE NOTICE '✅ MODEL USAGE ANALYTICS VIEW RECREATED';
        RAISE NOTICE '========================================';
        RAISE NOTICE '';
        RAISE NOTICE 'Summary:';
        RAISE NOTICE '  • View now uses model_pricing table for costs';
        RAISE NOTICE '  • Pricing displayed as per-1K tokens';
        RAISE NOTICE '  • Models with usage data: %', model_count;
        RAISE NOTICE '';
        RAISE NOTICE 'API Endpoint: GET /admin/model-usage-analytics';
        RAISE NOTICE 'Admin Dashboard: /model-analytics';
        RAISE NOTICE '';
    ELSE
        RAISE EXCEPTION 'Failed to create model_usage_analytics view';
    END IF;
END $$;
