-- Create downtime_incidents table for tracking application downtime events
-- and storing associated logs for debugging

CREATE TABLE IF NOT EXISTS downtime_incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Incident timing
    started_at TIMESTAMPTZ NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,

    -- Health check details
    health_endpoint TEXT NOT NULL DEFAULT '/health',
    error_message TEXT,
    http_status_code INTEGER,
    response_body TEXT,

    -- Incident metadata
    status TEXT NOT NULL DEFAULT 'ongoing' CHECK (status IN ('ongoing', 'resolved', 'investigating')),
    severity TEXT DEFAULT 'high' CHECK (severity IN ('low', 'medium', 'high', 'critical')),

    -- Log storage
    logs_captured JSONB DEFAULT '[]'::jsonb,
    logs_file_path TEXT,  -- Optional: if storing logs in S3 or file system
    log_count INTEGER DEFAULT 0,

    -- Additional context
    environment TEXT DEFAULT 'production',
    server_info JSONB DEFAULT '{}'::jsonb,  -- Can store server metadata
    metrics_snapshot JSONB DEFAULT '{}'::jsonb,  -- Can store Prometheus metrics at time of failure

    -- Tracking
    notified_at TIMESTAMPTZ,
    resolved_by TEXT,  -- Who/what resolved it
    notes TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for efficient querying
CREATE INDEX idx_downtime_incidents_started_at ON downtime_incidents(started_at DESC);
CREATE INDEX idx_downtime_incidents_status ON downtime_incidents(status);
CREATE INDEX idx_downtime_incidents_severity ON downtime_incidents(severity);
CREATE INDEX idx_downtime_incidents_environment ON downtime_incidents(environment);
CREATE INDEX idx_downtime_incidents_detected_at ON downtime_incidents(detected_at DESC);

-- Create updated_at trigger
CREATE OR REPLACE FUNCTION update_downtime_incidents_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();

    -- Auto-calculate duration when ended_at is set
    IF NEW.ended_at IS NOT NULL AND OLD.ended_at IS NULL THEN
        NEW.duration_seconds = EXTRACT(EPOCH FROM (NEW.ended_at - NEW.started_at))::INTEGER;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_downtime_incidents_updated_at
    BEFORE UPDATE ON downtime_incidents
    FOR EACH ROW
    EXECUTE FUNCTION update_downtime_incidents_updated_at();

-- Add RLS policies
-- Note: Access is primarily via service role (API), not direct Supabase auth
ALTER TABLE downtime_incidents ENABLE ROW LEVEL SECURITY;

-- Policy: Allow service role to do everything
CREATE POLICY "Service role can manage downtime incidents"
    ON downtime_incidents
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Create a view for ongoing incidents
CREATE OR REPLACE VIEW ongoing_downtime_incidents AS
SELECT
    id,
    started_at,
    detected_at,
    EXTRACT(EPOCH FROM (NOW() - started_at))::INTEGER as current_duration_seconds,
    error_message,
    http_status_code,
    severity,
    environment
FROM downtime_incidents
WHERE status = 'ongoing'
ORDER BY started_at DESC;

-- Grant permissions
GRANT SELECT ON ongoing_downtime_incidents TO authenticated;
GRANT ALL ON ongoing_downtime_incidents TO service_role;

-- Add comment
COMMENT ON TABLE downtime_incidents IS 'Tracks application downtime incidents with associated logs for debugging';
COMMENT ON COLUMN downtime_incidents.logs_captured IS 'JSONB array of log entries from 5min before to 5min after downtime';
COMMENT ON COLUMN downtime_incidents.logs_file_path IS 'Optional file path if logs are stored externally (S3, filesystem, etc)';
COMMENT ON COLUMN downtime_incidents.metrics_snapshot IS 'Prometheus/system metrics at time of failure';
