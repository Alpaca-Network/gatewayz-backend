-- Rebuild the public status-page views from their canonical definition.
--
-- WHY THIS IS STAGED (human-gated, NOT auto-applied)
-- ==================================================
-- It uses DROP VIEW ... CASCADE. Nothing depends on these views today (only
-- src/routes/status_page.py reads them, over PostgREST), but CASCADE is a
-- destructive verb and this repo's convention is that destructive migrations
-- get a human. Apply it against staging first, confirm GET /v1/status/models
-- returns 200, then production. No data is touched: both objects are VIEWS over
-- model_health_tracking.
--
-- WHAT WENT WRONG
-- ===============
-- GET /v1/status/models and GET /v1/status/models/{provider}/{model} return 500
-- in production while GET /v1/status/search and GET /v1/status/providers — the
-- same view, fewer columns — return 200. The query itself is fine: a filter that
-- matches zero rows returns 200, and ordering by usage_count_24h is accepted, so
-- PostgREST is happy. The 500 happens while formatting the rows, i.e. a column
-- the route reads is absent from the view as it actually exists in production.
--
-- That is the documented failure mode of CREATE OR REPLACE VIEW: it can only
-- APPEND columns, and fails outright if it would rename, retype or reorder an
-- existing one. Every migration that has ever touched model_status_current used
-- CREATE OR REPLACE (20251128000000, 20251205000000,
-- scripts/database/create_model_health_tables.sql), so a single failure at any
-- point left the older definition in place and every later run was a silent
-- no-op against it. DROP + CREATE is the only form that actually converges.
--
-- src/routes/status_page.py was also hardened to read rows with .get() and log
-- which columns are missing, so the endpoint degrades to nulls instead of a 500
-- even if the view drifts again. This migration fixes the underlying drift.

BEGIN;

DROP VIEW IF EXISTS model_status_current CASCADE;

CREATE VIEW model_status_current
WITH (security_invoker = true) AS
SELECT
    mht.provider,
    mht.model,
    mht.gateway,
    mht.monitoring_tier,
    mht.last_status,
    mht.uptime_percentage_24h,
    mht.uptime_percentage_7d,
    mht.uptime_percentage_30d,
    mht.average_response_time_ms,
    mht.last_called_at,
    mht.last_success_at,
    mht.last_failure_at,
    mht.circuit_breaker_state,
    mht.consecutive_failures,
    mht.usage_count_24h,
    mht.is_enabled,
    CASE
        -- #2366: the breaker decides the health verdict only when the
        -- measurement beside it agrees. This branch used to be unconditional,
        -- so it short-circuited the ladder below and one trip published
        -- 'offline' forever. In production on 2026-09-23:
        --
        --   openai/gpt-4o   status_indicator = offline   breaker = open
        --                   uptime_24h = 100.0           uptime_7d = 100.0
        --                   last_checked = today   last_failure = 7 days ago
        --
        -- Twenty-five advertised models were published as down while serving
        -- every check for a week. The breaker is still published verbatim in
        -- circuit_breaker_state, so nothing is lost -- it simply no longer
        -- outranks evidence that contradicts it.
        --
        -- COALESCE to 0: no measurement plus an open breaker is offline.
        -- Absent evidence must not read as healthy evidence. 99.0 rather than
        -- the ladder's 99.9 so the override releases only on a clearly
        -- healthy window.
        WHEN mht.circuit_breaker_state = 'open'
             AND COALESCE(mht.uptime_percentage_24h, 0) < 99.0 THEN 'offline'
        WHEN mht.uptime_percentage_24h >= 99.9 THEN 'operational'
        WHEN mht.uptime_percentage_24h >= 95.0 THEN 'degraded'
        WHEN mht.uptime_percentage_24h >= 50.0 THEN 'partial_outage'
        ELSE 'major_outage'
    END AS status_indicator,
    (SELECT COUNT(*) FROM model_health_incidents mhi
      WHERE mhi.provider = mht.provider
        AND mhi.model = mht.model
        AND mhi.status = 'active') AS active_incidents_count
FROM model_health_tracking mht
WHERE mht.is_enabled = TRUE;

COMMENT ON VIEW model_status_current IS
    'Current status for every enabled model. Read by GET /v1/status/models, '
    '/v1/status/models/{provider}/{model} and /v1/status/search.';

DROP VIEW IF EXISTS provider_health_current CASCADE;

CREATE VIEW provider_health_current
WITH (security_invoker = true) AS
SELECT
    mht.provider,
    mht.gateway,
    COUNT(*) AS total_models,
    COUNT(*) FILTER (WHERE mht.last_status = 'success') AS healthy_models,
    COUNT(*) FILTER (WHERE mht.circuit_breaker_state = 'open') AS offline_models,
    -- Throttled / unauthorized probes measure OUR access, not the provider's
    -- models (see UNMEASURED_STATUSES in
    -- src/services/monitoring/intelligent_health_monitor.py). Surfaced as their
    -- own counts so they are visible without being counted as unhealthy.
    COUNT(*) FILTER (WHERE mht.last_status = 'rate_limited') AS rate_limited_models,
    COUNT(*) FILTER (WHERE mht.last_status = 'unauthorized') AS unauthorized_models,
    ROUND(AVG(mht.uptime_percentage_24h), 2) AS avg_uptime_24h,
    ROUND(AVG(mht.uptime_percentage_7d), 2) AS avg_uptime_7d,
    ROUND(AVG(mht.average_response_time_ms), 2) AS avg_response_time_ms,
    MAX(mht.last_called_at) AS last_checked_at,
    SUM(mht.usage_count_24h) AS total_usage_24h,
    CASE
        WHEN ROUND(AVG(mht.uptime_percentage_24h), 2) >= 99.0 THEN 'operational'
        WHEN ROUND(AVG(mht.uptime_percentage_24h), 2) >= 95.0 THEN 'degraded'
        ELSE 'major_outage'
    END AS status_indicator
FROM model_health_tracking mht
WHERE mht.is_enabled = TRUE
GROUP BY mht.provider, mht.gateway;

COMMENT ON VIEW provider_health_current IS
    'Provider-level health aggregation. Read by GET /v1/status/providers.';

-- Both views are SECURITY INVOKER (20260527000001_full_security_hardening.sql):
-- only service_role may read them, and RLS on model_health_tracking still applies.
GRANT SELECT ON model_status_current TO service_role;
GRANT SELECT ON provider_health_current TO service_role;

COMMIT;
