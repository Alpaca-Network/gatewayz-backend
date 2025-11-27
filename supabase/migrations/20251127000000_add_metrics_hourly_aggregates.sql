-- Add metrics_hourly_aggregates table for storing aggregated metrics
-- This table stores hourly aggregated metrics from Redis for long-term storage and analysis

CREATE TABLE IF NOT EXISTS metrics_hourly_aggregates (
    id BIGSERIAL PRIMARY KEY,
    hour TIMESTAMPTZ NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    total_requests INTEGER DEFAULT 0,
    successful_requests INTEGER DEFAULT 0,
    failed_requests INTEGER DEFAULT 0,
    total_tokens_input BIGINT DEFAULT 0,
    total_tokens_output BIGINT DEFAULT 0,
    total_cost_credits NUMERIC(12,6) DEFAULT 0,
    avg_latency_ms NUMERIC(10,2),
    p50_latency_ms INTEGER,
    p95_latency_ms INTEGER,
    p99_latency_ms INTEGER,
    min_latency_ms INTEGER,
    max_latency_ms INTEGER,
    error_rate NUMERIC(5,4), -- Percentage as decimal (0.05 = 5%)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(hour, provider, model)
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_metrics_hourly_hour ON metrics_hourly_aggregates(hour DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_hourly_provider ON metrics_hourly_aggregates(provider, hour DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_hourly_model ON metrics_hourly_aggregates(model, hour DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_hourly_provider_model ON metrics_hourly_aggregates(provider, model, hour DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_hourly_created_at ON metrics_hourly_aggregates(created_at DESC);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_metrics_hourly_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update updated_at
CREATE TRIGGER trigger_update_metrics_hourly_updated_at
    BEFORE UPDATE ON metrics_hourly_aggregates
    FOR EACH ROW
    EXECUTE FUNCTION update_metrics_hourly_updated_at();

-- Add RLS (Row Level Security) policies
ALTER TABLE metrics_hourly_aggregates ENABLE ROW LEVEL SECURITY;

-- Policy: Allow service role full access
CREATE POLICY "Service role has full access to metrics_hourly_aggregates"
    ON metrics_hourly_aggregates
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policy: Allow authenticated users to read metrics (for admin dashboards)
CREATE POLICY "Authenticated users can read metrics_hourly_aggregates"
    ON metrics_hourly_aggregates
    FOR SELECT
    TO authenticated
    USING (true);

-- Policy: Allow anon users to read metrics (for public dashboards if needed)
-- Uncomment if you want public access to aggregated metrics
-- CREATE POLICY "Anon users can read metrics_hourly_aggregates"
--     ON metrics_hourly_aggregates
--     FOR SELECT
--     TO anon
--     USING (true);

-- Add comments for documentation
COMMENT ON TABLE metrics_hourly_aggregates IS 'Stores hourly aggregated metrics for providers and models';
COMMENT ON COLUMN metrics_hourly_aggregates.hour IS 'Hour timestamp (rounded to hour start)';
COMMENT ON COLUMN metrics_hourly_aggregates.provider IS 'Provider name (e.g., openrouter, fireworks)';
COMMENT ON COLUMN metrics_hourly_aggregates.model IS 'Model ID';
COMMENT ON COLUMN metrics_hourly_aggregates.total_requests IS 'Total number of requests in this hour';
COMMENT ON COLUMN metrics_hourly_aggregates.successful_requests IS 'Number of successful requests';
COMMENT ON COLUMN metrics_hourly_aggregates.failed_requests IS 'Number of failed requests';
COMMENT ON COLUMN metrics_hourly_aggregates.total_tokens_input IS 'Total input tokens used';
COMMENT ON COLUMN metrics_hourly_aggregates.total_tokens_output IS 'Total output tokens generated';
COMMENT ON COLUMN metrics_hourly_aggregates.total_cost_credits IS 'Total cost in credits/USD';
COMMENT ON COLUMN metrics_hourly_aggregates.avg_latency_ms IS 'Average latency in milliseconds';
COMMENT ON COLUMN metrics_hourly_aggregates.p50_latency_ms IS '50th percentile (median) latency';
COMMENT ON COLUMN metrics_hourly_aggregates.p95_latency_ms IS '95th percentile latency';
COMMENT ON COLUMN metrics_hourly_aggregates.p99_latency_ms IS '99th percentile latency';
COMMENT ON COLUMN metrics_hourly_aggregates.min_latency_ms IS 'Minimum latency observed';
COMMENT ON COLUMN metrics_hourly_aggregates.max_latency_ms IS 'Maximum latency observed';
COMMENT ON COLUMN metrics_hourly_aggregates.error_rate IS 'Error rate as decimal (0.05 = 5% errors)';

-- Create materialized view for quick provider statistics
CREATE MATERIALIZED VIEW IF NOT EXISTS provider_stats_24h AS
SELECT
    provider,
    SUM(total_requests) as total_requests,
    SUM(successful_requests) as successful_requests,
    SUM(failed_requests) as failed_requests,
    AVG(avg_latency_ms) as avg_latency_ms,
    SUM(total_cost_credits) as total_cost,
    SUM(total_tokens_input + total_tokens_output) as total_tokens,
    AVG(error_rate) as avg_error_rate,
    COUNT(DISTINCT model) as unique_models,
    MAX(hour) as last_hour
FROM metrics_hourly_aggregates
WHERE hour >= NOW() - INTERVAL '24 hours'
GROUP BY provider;

-- Create index on materialized view
CREATE INDEX IF NOT EXISTS idx_provider_stats_24h_provider ON provider_stats_24h(provider);

-- Function to refresh materialized view
CREATE OR REPLACE FUNCTION refresh_provider_stats_24h()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY provider_stats_24h;
END;
$$ LANGUAGE plpgsql;

-- Grant permissions
GRANT SELECT ON metrics_hourly_aggregates TO authenticated;
GRANT SELECT ON provider_stats_24h TO authenticated;
GRANT ALL ON metrics_hourly_aggregates TO service_role;
GRANT ALL ON provider_stats_24h TO service_role;
