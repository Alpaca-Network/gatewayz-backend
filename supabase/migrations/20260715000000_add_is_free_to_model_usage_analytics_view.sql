-- ============================================================================
-- Add is_free to Model Usage Analytics View
-- ============================================================================
-- Migration: 20260715000000
-- Description: Adds models.is_free to the model_usage_analytics view so
--              free-tier usage (and failure rates) can be segmented from
--              paid usage in admin tooling.
--
-- Reconstruction note: the view's SELECT/FROM/JOIN/WHERE/GROUP BY/HAVING/
-- ORDER BY below are copied verbatim from
-- 20260212000001_fix_analytics_view_pricing_from_metadata.sql, which is the
-- last migration in this repo's history that redefines the view body (grep
-- across supabase/migrations/ for "model_usage_analytics" confirms no later
-- migration issues DROP VIEW / CREATE VIEW / CREATE OR REPLACE VIEW with an
-- explicit new column list for it). 20260527000001_full_security_hardening.sql
-- runs later and rebuilds the view via
-- `CREATE OR REPLACE VIEW ... AS <pg_get_viewdef(...)>` — i.e. it copies
-- whatever column list already existed rather than changing it — but adds
-- `WITH (security_invoker = true)` and revokes anon/authenticated SELECT
-- grants (leaving only service_role). Both properties are preserved below.
-- Only `m.is_free` (added by 20260401000005_add_model_capability_columns.sql,
-- NOT NULL DEFAULT false) is new here, added to the SELECT list and GROUP BY.
--
-- Confidence: high for the column list/joins/predicates (directly copied from
-- the last defining migration); this was NOT verified against a live/staging
-- database — no DATABASE_URL/Supabase credentials were available in this
-- sandbox. Re-run `pg_get_viewdef('model_usage_analytics'::regclass)` against
-- the real database before/after applying to confirm an exact match.
-- ============================================================================

DROP VIEW IF EXISTS "public"."model_usage_analytics";

CREATE VIEW "public"."model_usage_analytics" WITH (security_invoker = true) AS
SELECT
    -- Model identification
    m.id as model_id,
    m.model_name,
    m.provider_model_id,
    p.name as provider_name,
    p.slug as provider_slug,
    m.is_free,

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
    -- Priority: model_pricing table > metadata.pricing_raw > 0
    -- model_pricing stores per-token, metadata.pricing_raw stores per-token
    -- Multiply by 1,000,000 for per-1M display
    COALESCE(
        mp.price_per_input_token * 1000000,
        CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC) * 1000000,
        0
    ) as input_token_price_per_1m,
    COALESCE(
        mp.price_per_output_token * 1000000,
        CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC) * 1000000,
        0
    ) as output_token_price_per_1m,

    -- Per-token pricing (raw, for cost calculations below)
    -- Used in cost formulas via the resolved per-token price
    COALESCE(
        mp.price_per_input_token,
        CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC),
        0
    ) as input_token_price,
    COALESCE(
        mp.price_per_output_token,
        CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC),
        0
    ) as output_token_price,

    -- Cost calculations (in USD)
    ROUND(
        CAST(
            (COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(
                mp.price_per_input_token,
                CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC),
                0
            )) +
            (COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(
                mp.price_per_output_token,
                CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC),
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
            CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC),
            0
        ) AS NUMERIC),
        6
    ) as input_cost_usd,
    ROUND(
        CAST(COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(
            mp.price_per_output_token,
            CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC),
            0
        ) AS NUMERIC),
        6
    ) as output_cost_usd,

    -- Average cost per request
    ROUND(
        CAST(
            (COALESCE(SUM(ccr.input_tokens), 0) * COALESCE(
                mp.price_per_input_token,
                CAST(m.metadata->'pricing_raw'->>'prompt' AS NUMERIC),
                0
            )) +
            (COALESCE(SUM(ccr.output_tokens), 0) * COALESCE(
                mp.price_per_output_token,
                CAST(m.metadata->'pricing_raw'->>'completion' AS NUMERIC),
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
    m.is_free,
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
    'Model usage analytics with pricing from model_pricing table OR metadata.pricing_raw fallback. '
    'Includes is_free (added 2026-07-15) to segment free-tier from paid usage.';

-- Grants: match the current hardened state (20260527000001_full_security_hardening.sql
-- revoked anon/authenticated SELECT on this view; only service_role should read it).
REVOKE ALL ON "public"."model_usage_analytics" FROM anon, authenticated;
GRANT SELECT ON "public"."model_usage_analytics" TO service_role;
