-- Migration: Fix model_health_tracking issues from code review
-- Description: Addresses code review feedback:
--   1. Add missing provider_health_current view (Sentry - CRITICAL)
--   2. Fix duplicate policy creation with DROP IF EXISTS (Cursor)
--   3. Fix COALESCE issue in update_model_tier (Cursor)
--   4. Add missing updated_at trigger for incidents (Cursor)
--   5. Optimize update_model_tier with CTE (Greptile)
-- Created: 2025-12-05

-- ============================================================================
-- 1. Add missing provider_health_current view (required by status_page.py)
-- ============================================================================
CREATE OR REPLACE VIEW provider_health_current AS
SELECT
    mht.provider,
    mht.gateway,
    COUNT(*) as total_models,
    COUNT(*) FILTER (WHERE last_status = 'success') as healthy_models,
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

COMMENT ON VIEW provider_health_current IS 'Provider-level health aggregation (for status page)';

-- Grant permissions on the view
GRANT SELECT ON provider_health_current TO authenticated;
GRANT SELECT ON provider_health_current TO anon;

-- ============================================================================
-- 2. Fix duplicate policy creation - drop existing policies first
-- ============================================================================

-- Drop existing policies on model_health_tracking (may or may not exist)
DROP POLICY IF EXISTS "Authenticated users can read model health" ON model_health_tracking;
DROP POLICY IF EXISTS "Service role can do anything on model health" ON model_health_tracking;
DROP POLICY IF EXISTS "Authenticated users can read" ON model_health_tracking;
DROP POLICY IF EXISTS "Service role can do anything" ON model_health_tracking;

-- Recreate policies with consistent names
CREATE POLICY "Authenticated users can read model health"
    ON model_health_tracking
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Service role can do anything on model health"
    ON model_health_tracking
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Drop existing policies on model_health_incidents
DROP POLICY IF EXISTS "Authenticated users can read incidents" ON model_health_incidents;
DROP POLICY IF EXISTS "Service role can do anything on incidents" ON model_health_incidents;

-- Recreate policies
CREATE POLICY "Authenticated users can read incidents"
    ON model_health_incidents
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Service role can do anything on incidents"
    ON model_health_incidents
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Drop existing policies on model_health_history
DROP POLICY IF EXISTS "Authenticated users can read health history" ON model_health_history;
DROP POLICY IF EXISTS "Service role can do anything on health history" ON model_health_history;
DROP POLICY IF EXISTS "Authenticated users can read history" ON model_health_history;
DROP POLICY IF EXISTS "Service role can do anything on history" ON model_health_history;

-- Recreate policies
CREATE POLICY "Authenticated users can read health history"
    ON model_health_history
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Service role can do anything on health history"
    ON model_health_history
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- 3. Fix update_model_tier function - remove COALESCE issue and optimize with CTE
-- ============================================================================
CREATE OR REPLACE FUNCTION update_model_tier()
RETURNS void AS $$
DECLARE
    p95_threshold NUMERIC;
    p75_threshold NUMERIC;
BEGIN
    -- Pre-calculate percentile thresholds using CTEs for better performance
    -- Only calculate if there are models with usage
    SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY usage_count_24h)
    INTO p95_threshold
    FROM model_health_tracking
    WHERE usage_count_24h > 0;

    SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY usage_count_24h)
    INTO p75_threshold
    FROM model_health_tracking
    WHERE usage_count_24h > 0;

    -- If no models have usage, skip tier updates (preserves existing behavior)
    IF p95_threshold IS NULL OR p75_threshold IS NULL THEN
        -- Only update models with no usage to on_demand tier
        UPDATE model_health_tracking
        SET monitoring_tier = 'on_demand',
            check_interval_seconds = 14400
        WHERE usage_count_24h = 0 AND monitoring_tier != 'on_demand';
        RETURN;
    END IF;

    -- Update to critical tier (top 5% by usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'critical',
        check_interval_seconds = 300 -- 5 minutes
    WHERE usage_count_24h >= p95_threshold
      AND monitoring_tier != 'critical';

    -- Update to popular tier (between 75th and 95th percentile)
    UPDATE model_health_tracking
    SET monitoring_tier = 'popular',
        check_interval_seconds = 1800 -- 30 minutes
    WHERE usage_count_24h >= p75_threshold
      AND usage_count_24h < p95_threshold
      AND monitoring_tier != 'popular';

    -- Update to standard tier (below 75th percentile but has usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'standard',
        check_interval_seconds = 7200 -- 2 hours
    WHERE usage_count_24h > 0
      AND usage_count_24h < p75_threshold
      AND monitoring_tier != 'standard';

    -- Update to on_demand tier (no recent usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'on_demand',
        check_interval_seconds = 14400 -- 4 hours
    WHERE usage_count_24h = 0 AND monitoring_tier != 'on_demand';

END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_model_tier IS 'Automatically update model monitoring tiers based on usage patterns. Uses pre-calculated percentile thresholds for better performance.';

-- ============================================================================
-- 4. Add missing updated_at trigger for model_health_incidents
-- ============================================================================
CREATE OR REPLACE FUNCTION update_model_health_incidents_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_model_health_incidents_updated_at ON model_health_incidents;
CREATE TRIGGER trigger_update_model_health_incidents_updated_at
    BEFORE UPDATE ON model_health_incidents
    FOR EACH ROW
    EXECUTE FUNCTION update_model_health_incidents_updated_at();

-- ============================================================================
-- 5. Notify PostgREST to reload schema cache
-- ============================================================================
NOTIFY pgrst, 'reload schema';
