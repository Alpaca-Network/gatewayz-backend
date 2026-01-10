-- Migration: Fix provider_health_current view to ensure data consistency
-- Description: Fix healthy_models count to never exceed total_models
-- Created: 2026-01-10
-- Issue: The current view can show healthy_models > total_models when data is stale

-- Drop and recreate the view with LEAST constraint to ensure data consistency
CREATE OR REPLACE VIEW provider_health_current AS
SELECT
    mht.provider,
    mht.gateway,
    COUNT(*) as total_models,
    -- Use LEAST to ensure healthy_models never exceeds total_models
    LEAST(
        COUNT(*) FILTER (WHERE last_status = 'success'),
        COUNT(*)
    ) as healthy_models,
    COUNT(*) FILTER (WHERE circuit_breaker_state = 'open') as offline_models,
    ROUND(AVG(uptime_percentage_24h), 2) as avg_uptime_24h,
    ROUND(AVG(uptime_percentage_7d), 2) as avg_uptime_7d,
    ROUND(AVG(average_response_time_ms), 2) as avg_response_time_ms,
    MAX(last_called_at) as last_checked_at,
    SUM(usage_count_24h) as total_usage_24h,
    CASE
        WHEN ROUND(AVG(uptime_percentage_24h), 2) >= 99.0 THEN 'operational'
        WHEN ROUND(AVG(uptime_percentage_24h), 2) >= 95.0 THEN 'degraded'
        ELSE 'major_outage'
    END as status_indicator
FROM model_health_tracking mht
WHERE mht.is_enabled = TRUE
GROUP BY mht.provider, mht.gateway;

COMMENT ON VIEW provider_health_current IS 'Provider-level health aggregation (for status page) - fixed to ensure healthy_models <= total_models';

-- Grant permissions (same as original)
GRANT SELECT ON provider_health_current TO anon;
