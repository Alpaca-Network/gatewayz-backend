-- Migration: Restore model_health_tracking table and related objects
-- Description: Restores the model_health_tracking table that was accidentally dropped
--              in 20251126165648_remote_schema.sql and recreates the update_model_tier function
-- Created: 2025-12-05
-- Issue: Error "Could not find the table 'public.model_health_tracking' in the schema cache"

-- Create model_health_tracking table
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
    -- Token tracking columns (from 20251124120000_add_token_tracking_to_health.sql)
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    -- Enhanced monitoring columns (from 20251128000000_enhance_model_health_tracking.sql)
    gateway TEXT,
    monitoring_tier TEXT DEFAULT 'standard',
    uptime_percentage_24h NUMERIC DEFAULT 100.0,
    uptime_percentage_7d NUMERIC DEFAULT 100.0,
    uptime_percentage_30d NUMERIC DEFAULT 100.0,
    last_incident_at TIMESTAMP WITH TIME ZONE,
    consecutive_failures INTEGER DEFAULT 0,
    consecutive_successes INTEGER DEFAULT 0,
    circuit_breaker_state TEXT DEFAULT 'closed',
    last_check_duration_ms NUMERIC,
    priority_score NUMERIC DEFAULT 0,
    usage_count_24h INTEGER DEFAULT 0,
    usage_count_7d INTEGER DEFAULT 0,
    usage_count_30d INTEGER DEFAULT 0,
    last_success_at TIMESTAMP WITH TIME ZONE,
    last_failure_at TIMESTAMP WITH TIME ZONE,
    next_check_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    check_interval_seconds INTEGER DEFAULT 3600,
    is_enabled BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb,
    PRIMARY KEY (provider, model)
);

-- Create indexes for model_health_tracking
CREATE INDEX IF NOT EXISTS idx_model_health_last_called
    ON model_health_tracking(last_called_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_health_status
    ON model_health_tracking(last_status);

CREATE INDEX IF NOT EXISTS idx_model_health_provider
    ON model_health_tracking(provider);

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

-- Add column comments
COMMENT ON TABLE model_health_tracking IS 'Tracks health status and metrics for AI models across providers';
COMMENT ON COLUMN model_health_tracking.provider IS 'Provider name (e.g., openrouter, portkey)';
COMMENT ON COLUMN model_health_tracking.model IS 'Model identifier';
COMMENT ON COLUMN model_health_tracking.gateway IS 'Gateway/aggregator used to access this model';
COMMENT ON COLUMN model_health_tracking.monitoring_tier IS 'Monitoring tier: critical, popular, standard, on_demand';
COMMENT ON COLUMN model_health_tracking.uptime_percentage_24h IS 'Uptime percentage over last 24 hours';
COMMENT ON COLUMN model_health_tracking.uptime_percentage_7d IS 'Uptime percentage over last 7 days';
COMMENT ON COLUMN model_health_tracking.uptime_percentage_30d IS 'Uptime percentage over last 30 days';
COMMENT ON COLUMN model_health_tracking.last_incident_at IS 'Timestamp of most recent incident/failure';
COMMENT ON COLUMN model_health_tracking.consecutive_failures IS 'Number of consecutive failures (for circuit breaker)';
COMMENT ON COLUMN model_health_tracking.consecutive_successes IS 'Number of consecutive successes (for circuit breaker recovery)';
COMMENT ON COLUMN model_health_tracking.circuit_breaker_state IS 'Circuit breaker state: closed, open, half_open';
COMMENT ON COLUMN model_health_tracking.last_check_duration_ms IS 'Duration of the last health check in milliseconds';
COMMENT ON COLUMN model_health_tracking.priority_score IS 'Dynamic priority score for scheduling checks (higher = more urgent)';
COMMENT ON COLUMN model_health_tracking.usage_count_24h IS 'Number of actual requests to this model in last 24h';
COMMENT ON COLUMN model_health_tracking.usage_count_7d IS 'Number of actual requests to this model in last 7d';
COMMENT ON COLUMN model_health_tracking.usage_count_30d IS 'Number of actual requests to this model in last 30d';
COMMENT ON COLUMN model_health_tracking.last_success_at IS 'Timestamp of most recent successful check';
COMMENT ON COLUMN model_health_tracking.last_failure_at IS 'Timestamp of most recent failed check';
COMMENT ON COLUMN model_health_tracking.next_check_at IS 'Scheduled time for next health check';
COMMENT ON COLUMN model_health_tracking.check_interval_seconds IS 'Interval between health checks in seconds';
COMMENT ON COLUMN model_health_tracking.is_enabled IS 'Whether monitoring is enabled for this model';
COMMENT ON COLUMN model_health_tracking.metadata IS 'Additional metadata (JSON): pricing_tier, capabilities, etc.';

-- Create trigger for updated_at
CREATE OR REPLACE FUNCTION update_model_health_tracking_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_model_health_tracking_updated_at ON model_health_tracking;
CREATE TRIGGER trigger_update_model_health_tracking_updated_at
    BEFORE UPDATE ON model_health_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_model_health_tracking_updated_at();

-- Create model_health_incidents table for incident tracking
CREATE TABLE IF NOT EXISTS model_health_incidents (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT NOT NULL,
    incident_type TEXT NOT NULL, -- 'outage', 'degradation', 'timeout', 'rate_limit'
    severity TEXT NOT NULL, -- 'critical', 'high', 'medium', 'low'
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    error_message TEXT,
    error_count INTEGER DEFAULT 1,
    affected_requests INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'resolved', 'acknowledged'
    resolution_notes TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    FOREIGN KEY (provider, model) REFERENCES model_health_tracking(provider, model) ON DELETE CASCADE
);

-- Create indexes for incident queries
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

COMMENT ON TABLE model_health_incidents IS 'Tracks incidents and outages for models across providers';

-- Create model_health_history table for time-series data
CREATE TABLE IF NOT EXISTS model_health_history (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT NOT NULL,
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL, -- 'success', 'error', 'timeout', 'rate_limited'
    response_time_ms NUMERIC,
    error_message TEXT,
    http_status_code INTEGER,
    circuit_breaker_state TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create indexes for history queries
CREATE INDEX IF NOT EXISTS idx_history_provider_model_time
    ON model_health_history(provider, model, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_history_checked_at
    ON model_health_history(checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_history_gateway
    ON model_health_history(gateway);

CREATE INDEX IF NOT EXISTS idx_history_status
    ON model_health_history(status);

COMMENT ON TABLE model_health_history IS 'Historical health check data for models (time-series)';

-- Create function to update model tier based on usage
CREATE OR REPLACE FUNCTION update_model_tier()
RETURNS void AS $$
BEGIN
    -- Update to critical tier (top 5% by usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'critical',
        check_interval_seconds = 300 -- 5 minutes
    WHERE usage_count_24h >= (
        SELECT COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY usage_count_24h), 0)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND monitoring_tier != 'critical';

    -- Update to popular tier (next 20%)
    UPDATE model_health_tracking
    SET monitoring_tier = 'popular',
        check_interval_seconds = 1800 -- 30 minutes
    WHERE usage_count_24h >= (
        SELECT COALESCE(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY usage_count_24h), 0)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND usage_count_24h < (
        SELECT COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY usage_count_24h), 0)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND monitoring_tier != 'popular';

    -- Update to standard tier (remaining with some usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'standard',
        check_interval_seconds = 7200 -- 2 hours
    WHERE usage_count_24h > 0
    AND usage_count_24h < (
        SELECT COALESCE(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY usage_count_24h), 0)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND monitoring_tier != 'standard';

    -- Update to on_demand tier (no recent usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'on_demand',
        check_interval_seconds = 14400 -- 4 hours
    WHERE usage_count_24h = 0 AND monitoring_tier != 'on_demand';

END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_model_tier IS 'Automatically update model monitoring tiers based on usage patterns';

-- Create function to clean old health history (retention policy)
CREATE OR REPLACE FUNCTION clean_old_health_history(retention_days INTEGER DEFAULT 90)
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

COMMENT ON FUNCTION clean_old_health_history IS 'Clean health history data older than retention period';

-- Drop existing view first to allow column name changes
DROP VIEW IF EXISTS model_status_current;

-- Create view for current model status (for status page)
CREATE VIEW model_status_current AS
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
       AND mhi.status = 'active') as active_incidents
FROM model_health_tracking mht
WHERE mht.is_enabled = TRUE;

COMMENT ON VIEW model_status_current IS 'Current status view for all enabled models';

-- Grant permissions
GRANT SELECT ON model_health_tracking TO authenticated;
GRANT ALL ON model_health_tracking TO service_role;

GRANT SELECT ON model_health_incidents TO authenticated;
GRANT ALL ON model_health_incidents TO service_role;

GRANT SELECT ON model_health_history TO authenticated;
GRANT ALL ON model_health_history TO service_role;

GRANT SELECT ON model_status_current TO authenticated;
GRANT SELECT ON model_status_current TO anon;

-- Enable RLS on model_health_tracking
ALTER TABLE model_health_tracking ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Authenticated users can read model health" ON model_health_tracking;
DROP POLICY IF EXISTS "Service role can do anything on model health" ON model_health_tracking;

-- Create policies
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

-- Enable RLS on model_health_incidents
ALTER TABLE model_health_incidents ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Authenticated users can read incidents" ON model_health_incidents;
DROP POLICY IF EXISTS "Service role can do anything on incidents" ON model_health_incidents;

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

-- Enable RLS on model_health_history
ALTER TABLE model_health_history ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Authenticated users can read health history" ON model_health_history;
DROP POLICY IF EXISTS "Service role can do anything on health history" ON model_health_history;

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

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';
