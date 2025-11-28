-- Migration: Enhance model health tracking for 10K+ models at scale
-- Description: Add tiered monitoring, historical uptime, and incident tracking
-- Created: 2025-11-28

-- Add new columns to model_health_tracking for enhanced monitoring
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

-- Create index for monitoring tier queries
CREATE INDEX IF NOT EXISTS idx_model_health_monitoring_tier
    ON model_health_tracking(monitoring_tier);

-- Create index for next check scheduling
CREATE INDEX IF NOT EXISTS idx_model_health_next_check
    ON model_health_tracking(next_check_at ASC) WHERE is_enabled = TRUE;

-- Create index for circuit breaker queries
CREATE INDEX IF NOT EXISTS idx_model_health_circuit_breaker
    ON model_health_tracking(circuit_breaker_state);

-- Create index for gateway queries
CREATE INDEX IF NOT EXISTS idx_model_health_gateway
    ON model_health_tracking(gateway);

-- Create composite index for uptime queries
CREATE INDEX IF NOT EXISTS idx_model_health_uptime
    ON model_health_tracking(uptime_percentage_24h DESC, provider, gateway);

-- Create index for priority scoring
CREATE INDEX IF NOT EXISTS idx_model_health_priority
    ON model_health_tracking(priority_score DESC, next_check_at ASC);

-- Add column comments
COMMENT ON COLUMN model_health_tracking.gateway IS 'Gateway/aggregator used to access this model (e.g., openrouter, portkey)';
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

-- Create partitioned index for history queries (time-series optimized)
CREATE INDEX IF NOT EXISTS idx_history_provider_model_time
    ON model_health_history(provider, model, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_history_checked_at
    ON model_health_history(checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_history_gateway_time
    ON model_health_history(gateway, checked_at DESC);

-- Partition by month for better performance (optional, for high-volume deployments)
-- Note: Partitioning can be enabled later if needed
COMMENT ON TABLE model_health_history IS 'Time-series history of health checks for trend analysis';

-- Create model_health_aggregates table for pre-computed statistics
CREATE TABLE IF NOT EXISTS model_health_aggregates (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    gateway TEXT NOT NULL,
    aggregation_period TEXT NOT NULL, -- 'hour', 'day', 'week', 'month'
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

-- Create indexes for aggregate queries
CREATE INDEX IF NOT EXISTS idx_aggregates_period
    ON model_health_aggregates(aggregation_period, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_aggregates_provider_period
    ON model_health_aggregates(provider, aggregation_period, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_aggregates_gateway_period
    ON model_health_aggregates(gateway, aggregation_period, period_start DESC);

COMMENT ON TABLE model_health_aggregates IS 'Pre-computed health statistics for fast querying';

-- Create function to automatically update incident duration
CREATE OR REPLACE FUNCTION update_incident_duration()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.resolved_at IS NOT NULL AND OLD.resolved_at IS NULL THEN
        NEW.duration_seconds = EXTRACT(EPOCH FROM (NEW.resolved_at - NEW.started_at))::INTEGER;
        NEW.status = 'resolved';
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for incident duration updates
CREATE TRIGGER trigger_update_incident_duration
    BEFORE UPDATE ON model_health_incidents
    FOR EACH ROW
    EXECUTE FUNCTION update_incident_duration();

-- Create function to calculate priority score
CREATE OR REPLACE FUNCTION calculate_model_priority_score(
    p_usage_count_24h INTEGER,
    p_consecutive_failures INTEGER,
    p_uptime_24h NUMERIC,
    p_last_called_at TIMESTAMP WITH TIME ZONE,
    p_monitoring_tier TEXT
) RETURNS NUMERIC AS $$
DECLARE
    priority NUMERIC := 0;
    hours_since_check NUMERIC;
BEGIN
    -- Base priority from tier
    priority := CASE p_monitoring_tier
        WHEN 'critical' THEN 1000
        WHEN 'popular' THEN 500
        WHEN 'standard' THEN 100
        WHEN 'on_demand' THEN 10
        ELSE 50
    END;

    -- Add usage-based priority (logarithmic scale)
    IF p_usage_count_24h > 0 THEN
        priority := priority + (LOG(p_usage_count_24h + 1) * 10);
    END IF;

    -- Penalty for consecutive failures (urgent to recheck)
    IF p_consecutive_failures > 0 THEN
        priority := priority + (p_consecutive_failures * 100);
    END IF;

    -- Penalty for low uptime (needs more attention)
    IF p_uptime_24h < 95 THEN
        priority := priority + ((100 - p_uptime_24h) * 5);
    END IF;

    -- Penalty for staleness (time since last check)
    hours_since_check := EXTRACT(EPOCH FROM (NOW() - p_last_called_at)) / 3600;
    IF hours_since_check > 24 THEN
        priority := priority + (hours_since_check * 2);
    END IF;

    RETURN priority;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION calculate_model_priority_score IS 'Calculate dynamic priority score for health check scheduling';

-- Create function to update model tier based on usage
CREATE OR REPLACE FUNCTION update_model_tier()
RETURNS void AS $$
BEGIN
    -- Update to critical tier (top 5% by usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'critical',
        check_interval_seconds = 300 -- 5 minutes
    WHERE usage_count_24h >= (
        SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY usage_count_24h)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND monitoring_tier != 'critical';

    -- Update to popular tier (next 20%)
    UPDATE model_health_tracking
    SET monitoring_tier = 'popular',
        check_interval_seconds = 1800 -- 30 minutes
    WHERE usage_count_24h >= (
        SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY usage_count_24h)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND usage_count_24h < (
        SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY usage_count_24h)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND monitoring_tier != 'popular';

    -- Update to standard tier (remaining with some usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'standard',
        check_interval_seconds = 7200 -- 2 hours
    WHERE usage_count_24h > 0
    AND usage_count_24h < (
        SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY usage_count_24h)
        FROM model_health_tracking
        WHERE usage_count_24h > 0
    ) AND monitoring_tier NOT IN ('critical', 'popular');

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

-- Create view for current model status (for status page)
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

COMMENT ON VIEW model_status_current IS 'Current status view for all monitored models (for status page)';

-- Create view for provider-level health
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

-- Grant permissions
ALTER TABLE model_health_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_health_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_health_aggregates ENABLE ROW LEVEL SECURITY;

-- Service role full access
CREATE POLICY "Service role can do anything on incidents" ON model_health_incidents
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role can do anything on history" ON model_health_history
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role can do anything on aggregates" ON model_health_aggregates
    FOR ALL USING (true) WITH CHECK (true);

-- Authenticated users can read
CREATE POLICY "Authenticated users can read incidents" ON model_health_incidents
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "Authenticated users can read history" ON model_health_history
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "Authenticated users can read aggregates" ON model_health_aggregates
    FOR SELECT USING (auth.role() = 'authenticated');

-- Anonymous users can read current status (for public status page)
GRANT SELECT ON model_status_current TO anon;
GRANT SELECT ON provider_health_current TO anon;

-- Create initial data and set reasonable defaults
UPDATE model_health_tracking
SET
    gateway = COALESCE(gateway, 'unknown'),
    next_check_at = COALESCE(next_check_at, NOW()),
    check_interval_seconds = COALESCE(check_interval_seconds, 3600),
    monitoring_tier = COALESCE(monitoring_tier, 'standard'),
    circuit_breaker_state = COALESCE(circuit_breaker_state, 'closed'),
    is_enabled = COALESCE(is_enabled, TRUE),
    uptime_percentage_24h = COALESCE(uptime_percentage_24h, 100.0),
    uptime_percentage_7d = COALESCE(uptime_percentage_7d, 100.0),
    uptime_percentage_30d = COALESCE(uptime_percentage_30d, 100.0)
WHERE gateway IS NULL OR next_check_at IS NULL;
