-- Migration: Add model health tracking table
-- Description: Track model calls, response times, and health status per provider-model combination
-- Created: 2025-11-21

-- Create model_health_tracking table
CREATE TABLE IF NOT EXISTS model_health_tracking (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    last_response_time_ms NUMERIC,
    last_status TEXT NOT NULL,
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

-- Create index for timestamp queries
CREATE INDEX IF NOT EXISTS idx_model_health_last_called
    ON model_health_tracking(last_called_at DESC);

-- Create index for status queries
CREATE INDEX IF NOT EXISTS idx_model_health_status
    ON model_health_tracking(last_status);

-- Create index for provider queries
CREATE INDEX IF NOT EXISTS idx_model_health_provider
    ON model_health_tracking(provider);

-- Add comment to table
COMMENT ON TABLE model_health_tracking IS 'Tracks health metrics and response times for each provider-model combination';

-- Add column comments
COMMENT ON COLUMN model_health_tracking.provider IS 'AI provider name (e.g., openrouter, portkey, huggingface)';
COMMENT ON COLUMN model_health_tracking.model IS 'Model identifier as used by the provider';
COMMENT ON COLUMN model_health_tracking.last_response_time_ms IS 'Response time of the last call in milliseconds';
COMMENT ON COLUMN model_health_tracking.last_status IS 'Status of last call: success, error, timeout, rate_limited, etc.';
COMMENT ON COLUMN model_health_tracking.last_called_at IS 'Timestamp of the most recent call';
COMMENT ON COLUMN model_health_tracking.call_count IS 'Total number of calls made to this provider-model';
COMMENT ON COLUMN model_health_tracking.success_count IS 'Number of successful calls';
COMMENT ON COLUMN model_health_tracking.error_count IS 'Number of failed calls';
COMMENT ON COLUMN model_health_tracking.average_response_time_ms IS 'Running average of response times';
COMMENT ON COLUMN model_health_tracking.last_error_message IS 'Error message from the last failed call';

-- Create function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_model_health_tracking_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to automatically update updated_at
CREATE TRIGGER trigger_update_model_health_tracking_updated_at
    BEFORE UPDATE ON model_health_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_model_health_tracking_updated_at();

-- Grant permissions (adjust based on your auth setup)
-- For authenticated users to read
ALTER TABLE model_health_tracking ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role can do anything" ON model_health_tracking
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Allow authenticated users to read
CREATE POLICY "Authenticated users can read" ON model_health_tracking
    FOR SELECT
    USING (auth.role() = 'authenticated');
