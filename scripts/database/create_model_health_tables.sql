-- Combined Migration: Create model health tracking tables
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor)
-- Created: 2025-12-05
--
-- This script combines all model_health_tracking related migrations:
-- - 20251121000001_add_model_health_tracking.sql
-- - 20251124120000_add_token_tracking_to_health.sql
-- - 20251128000000_enhance_model_health_tracking.sql

-- ============================================================================
-- PART 1: Create base model_health_tracking table
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_health_tracking (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    last_response_time_ms NUMERIC,
    last_status TEXT NOT NULL DEFAULT 'unknown',
    last_called_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    call_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    average_response_time_ms NUMERIC,
    last_error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, model)
);

-- Create indexes for base table
CREATE INDEX IF NOT EXISTS idx_model_health_last_called
    ON model_health_tracking(last_called_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_health_status
    ON model_health_tracking(last_status);

CREATE INDEX IF NOT EXISTS idx_model_health_provider
    ON model_health_tracking(provider);

COMMENT ON TABLE model_health_tracking IS 'Tracks health metrics and response times for each provider-model combination';

-- ============================================================================
-- PART 2: Add token tracking columns
-- ============================================================================

ALTER TABLE model_health_tracking
ADD COLUMN IF NOT EXISTS input_tokens INTEGER,
ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
ADD COLUMN IF NOT EXISTS total_tokens INTEGER;

COMMENT ON COLUMN model_health_tracking.input_tokens IS 'Number of input tokens in the last call';
COMMENT ON COLUMN model_health_tracking.output_tokens IS 'Number of output tokens in the last call';
COMMENT ON COLUMN model_health_tracking.total_tokens IS 'Total tokens (input + output) in the last call';

-- ============================================================================
-- PART 3: Add enhanced monitoring columns
-- ============================================================================

ALTER TABLE model_health_tracking
ADD COLUMN IF NOT EXISTS gateway TEXT,
ADD COLUMN IF NOT EXISTS monitoring_tier TEXT DEFAULT 'standard',
ADD COLUMN IF NOT EXISTS uptime_percentage_24h NUMERIC DEFAULT 100.0,
ADD COLUMN IF NOT EXISTS uptime_percentage_7d NUMERIC DEFAULT 100.0,
ADD COLUMN IF NOT EXISTS uptime_percentage_30d NUMERIC DEFAULT 100.0,
ADD COLUMN IF NOT EXISTS last_incident_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS consecutive_successes INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS circuit_breaker_state TEXT DEFAULT 'closed',
ADD COLUMN IF NOT EXISTS last_check_duration_ms NUMERIC,
ADD COLUMN IF NOT EXISTS priority_score NUMERIC DEFAULT 0,
ADD COLUMN IF NOT EXISTS usage_count_24h INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS usage_count_7d INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS usage_count_30d INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
ADD COLUMN IF NOT EXISTS check_interval_seconds INTEGER DEFAULT 3600,
ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

-- Create enhanced indexes
CREATE INDEX IF NOT EXISTS idx_model_health_monitoring_tier
    ON model_health_tracking(monitoring_tier);

CREATE INDEX IF NOT EXISTS idx_model_health_next_check
    ON model_health_tracking(next_check_at ASC) WHERE is_enabled = TRUE;

CREATE INDEX IF NOT EXISTS idx_model_health_circuit_breaker
    ON model_health_tracking(circuit_breaker_state);

CREATE INDEX IF NOT EXISTS idx_model_health_gateway
    ON model_health_tracking(gateway);

CREATE INDEX IF NOT EXISTS idx_model_health_uptime
    ON model_health_tracking(uptime_percentage_24h DESC, provider, gateway);

CREATE INDEX IF NOT EXISTS idx_model_health_priority
    ON model_health_tracking(priority_score DESC, next_check_at ASC);

-- ============================================================================
-- PART 4: Create model_health_incidents table
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_health_incidents (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    error_message TEXT,
    error_count INTEGER DEFAULT 1,
    affected_requests INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    resolution_notes TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    resolved BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (provider, model) REFERENCES model_health_tracking(provider, model) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_incidents_provider_model
    ON model_health_incidents(provider, model);

CREATE INDEX IF NOT EXISTS idx_incidents_gateway
    ON model_health_incidents(gateway);

CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON model_health_incidents(status) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_incidents_started_at
    ON model_health_incidents(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_incidents_severity
    ON model_health_incidents(severity);

CREATE INDEX IF NOT EXISTS idx_incidents_resolved
    ON model_health_incidents(resolved) WHERE resolved = FALSE;

-- Composite index for view subquery performance (provider, model, status)
CREATE INDEX IF NOT EXISTS idx_incidents_provider_model_status
    ON model_health_incidents(provider, model, status);

COMMENT ON TABLE model_health_incidents IS 'Tracks incidents and outages for models across providers';

-- ============================================================================
-- PART 5: Create model_health_history table
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_health_history (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT NOT NULL,
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL,
    response_time_ms NUMERIC,
    error_message TEXT,
    http_status_code INTEGER,
    circuit_breaker_state TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_history_provider_model_time
    ON model_health_history(provider, model, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_history_checked_at
    ON model_health_history(checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_history_gateway_time
    ON model_health_history(gateway, checked_at DESC);

COMMENT ON TABLE model_health_history IS 'Time-series history of health checks for trend analysis';

-- ============================================================================
-- PART 6: Create model_health_aggregates table
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_health_aggregates (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT NOT NULL,
    aggregation_period TEXT NOT NULL,
    period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    total_checks INTEGER NOT NULL DEFAULT 0,
    successful_checks INTEGER NOT NULL DEFAULT 0,
    failed_checks INTEGER NOT NULL DEFAULT 0,
    timeout_checks INTEGER NOT NULL DEFAULT 0,
    avg_response_time_ms NUMERIC,
    min_response_time_ms NUMERIC,
    max_response_time_ms NUMERIC,
    p50_response_time_ms NUMERIC,
    p95_response_time_ms NUMERIC,
    p99_response_time_ms NUMERIC,
    uptime_percentage NUMERIC,
    incident_count INTEGER DEFAULT 0,
    total_downtime_seconds INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, model, gateway, aggregation_period, period_start)
);

CREATE INDEX IF NOT EXISTS idx_aggregates_period
    ON model_health_aggregates(aggregation_period, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_aggregates_provider_period
    ON model_health_aggregates(provider, aggregation_period, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_aggregates_gateway_period
    ON model_health_aggregates(gateway, aggregation_period, period_start DESC);

COMMENT ON TABLE model_health_aggregates IS 'Pre-computed health statistics for fast querying';

-- ============================================================================
-- PART 7: Create triggers and functions
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_model_health_tracking_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for model_health_tracking
DROP TRIGGER IF EXISTS trigger_update_model_health_tracking_updated_at ON model_health_tracking;
CREATE TRIGGER trigger_update_model_health_tracking_updated_at
    BEFORE UPDATE ON model_health_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_model_health_tracking_updated_at();

-- Function to update incident duration
CREATE OR REPLACE FUNCTION update_incident_duration()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.resolved_at IS NOT NULL AND OLD.resolved_at IS NULL THEN
        NEW.duration_seconds = EXTRACT(EPOCH FROM (NEW.resolved_at - NEW.started_at))::INTEGER;
        NEW.status = 'resolved';
        NEW.resolved = TRUE;
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for incidents
DROP TRIGGER IF EXISTS trigger_update_incident_duration ON model_health_incidents;
CREATE TRIGGER trigger_update_incident_duration
    BEFORE UPDATE ON model_health_incidents
    FOR EACH ROW
    EXECUTE FUNCTION update_incident_duration();

-- ============================================================================
-- PART 8: Create views
-- ============================================================================

-- Current model status view
CREATE OR REPLACE VIEW model_status_current AS
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
        WHEN mht.circuit_breaker_state = 'open' THEN 'offline'
        WHEN mht.uptime_percentage_24h >= 99.9 THEN 'operational'
        WHEN mht.uptime_percentage_24h >= 95.0 THEN 'degraded'
        WHEN mht.uptime_percentage_24h >= 50.0 THEN 'partial_outage'
        ELSE 'major_outage'
    END as status_indicator,
    (SELECT COUNT(*) FROM model_health_incidents mhi
     WHERE mhi.provider = mht.provider
     AND mhi.model = mht.model
     AND mhi.status = 'active') as active_incidents_count
FROM model_health_tracking mht
WHERE mht.is_enabled = TRUE;

COMMENT ON VIEW model_status_current IS 'Current status view for all monitored models';

-- Provider health view
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

COMMENT ON VIEW provider_health_current IS 'Provider-level health aggregation';

-- ============================================================================
-- PART 9: Enable Row Level Security
-- ============================================================================

ALTER TABLE model_health_tracking ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_health_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_health_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_health_aggregates ENABLE ROW LEVEL SECURITY;

-- Service role full access policies
DROP POLICY IF EXISTS "Service role can do anything" ON model_health_tracking;
CREATE POLICY "Service role can do anything" ON model_health_tracking
    FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service role can do anything on incidents" ON model_health_incidents;
CREATE POLICY "Service role can do anything on incidents" ON model_health_incidents
    FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service role can do anything on history" ON model_health_history;
CREATE POLICY "Service role can do anything on history" ON model_health_history
    FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service role can do anything on aggregates" ON model_health_aggregates;
CREATE POLICY "Service role can do anything on aggregates" ON model_health_aggregates
    FOR ALL USING (true) WITH CHECK (true);

-- Grant select on views to anonymous users (for public status page)
GRANT SELECT ON model_status_current TO anon;
GRANT SELECT ON provider_health_current TO anon;

-- ============================================================================
-- PART 10: Data retention policy for model_health_history
-- ============================================================================

-- Function to clean up old history records (keeps last 30 days by default)
CREATE OR REPLACE FUNCTION cleanup_model_health_history(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM model_health_history
    WHERE checked_at < NOW() - (retention_days || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_model_health_history IS 'Removes history records older than specified days (default 30). Call periodically via cron or scheduled job.';

-- ============================================================================
-- DONE: Migration complete!
-- ============================================================================

SELECT 'Migration completed successfully!' as status;
