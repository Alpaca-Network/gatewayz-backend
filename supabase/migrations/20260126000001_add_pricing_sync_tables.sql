-- Migration: Add Pricing Sync Infrastructure Tables
-- Created: 2026-01-26
-- Purpose: Support Phase 2 pricing sync to database

-- ============================================================================
-- Table 1: model_pricing_history
-- Purpose: Track all pricing changes over time for audit and analysis
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_pricing_history (
    id BIGSERIAL PRIMARY KEY,
    model_id BIGINT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    price_per_input_token NUMERIC(20, 15) NOT NULL,
    price_per_output_token NUMERIC(20, 15) NOT NULL,
    previous_input_price NUMERIC(20, 15),
    previous_output_price NUMERIC(20, 15),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed_by TEXT NOT NULL,

    -- Constraints
    CONSTRAINT chk_positive_prices CHECK (
        price_per_input_token >= 0 AND
        price_per_output_token >= 0
    )
);

-- Indexes for efficient queries
CREATE INDEX idx_model_pricing_history_model_id ON model_pricing_history(model_id);
CREATE INDEX idx_model_pricing_history_changed_at ON model_pricing_history(changed_at DESC);
CREATE INDEX idx_model_pricing_history_changed_by ON model_pricing_history(changed_by);

-- Comments
COMMENT ON TABLE model_pricing_history IS 'Historical pricing changes for models with audit trail';
COMMENT ON COLUMN model_pricing_history.changed_by IS 'Who/what changed the pricing (e.g., api_sync:openrouter, manual:admin_user_123)';

-- ============================================================================
-- Table 2: pricing_sync_log
-- Purpose: Log all pricing sync operations for monitoring and debugging
-- ============================================================================

CREATE TABLE IF NOT EXISTS pricing_sync_log (
    id BIGSERIAL PRIMARY KEY,
    provider_slug TEXT NOT NULL,
    sync_started_at TIMESTAMPTZ NOT NULL,
    sync_completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'in_progress')),
    models_fetched INTEGER DEFAULT 0,
    models_updated INTEGER DEFAULT 0,
    models_skipped INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    error_message TEXT,

    -- Metadata
    triggered_by TEXT,  -- 'manual', 'scheduler', 'api'
    duration_ms INTEGER GENERATED ALWAYS AS (
        CASE
            WHEN sync_completed_at IS NOT NULL THEN
                EXTRACT(EPOCH FROM (sync_completed_at - sync_started_at))::INTEGER * 1000
            ELSE NULL
        END
    ) STORED,

    -- Constraints
    CONSTRAINT chk_non_negative_counts CHECK (
        models_fetched >= 0 AND
        models_updated >= 0 AND
        models_skipped >= 0 AND
        errors >= 0
    )
);

-- Indexes for efficient queries
CREATE INDEX idx_pricing_sync_log_provider ON pricing_sync_log(provider_slug);
CREATE INDEX idx_pricing_sync_log_started_at ON pricing_sync_log(sync_started_at DESC);
CREATE INDEX idx_pricing_sync_log_status ON pricing_sync_log(status);
CREATE INDEX idx_pricing_sync_log_provider_status ON pricing_sync_log(provider_slug, status);

-- Comments
COMMENT ON TABLE pricing_sync_log IS 'Log of all pricing sync operations across providers';
COMMENT ON COLUMN pricing_sync_log.duration_ms IS 'Sync duration in milliseconds (auto-calculated)';
COMMENT ON COLUMN pricing_sync_log.triggered_by IS 'Source of sync trigger (manual, scheduler, api)';

-- ============================================================================
-- Row Level Security (RLS)
-- ============================================================================

-- Enable RLS
ALTER TABLE model_pricing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE pricing_sync_log ENABLE ROW LEVEL SECURITY;

-- Policy: Allow service role full access (backend services)
CREATE POLICY "Service role has full access to pricing history"
    ON model_pricing_history
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role has full access to pricing sync log"
    ON pricing_sync_log
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policy: Allow authenticated users to read pricing history
CREATE POLICY "Authenticated users can read pricing history"
    ON model_pricing_history
    FOR SELECT
    TO authenticated
    USING (true);

-- Policy: Allow authenticated users to read sync log
CREATE POLICY "Authenticated users can read pricing sync log"
    ON pricing_sync_log
    FOR SELECT
    TO authenticated
    USING (true);

-- Policy: Admins can insert/update (via role check)
-- Note: Admin check should be done at application layer via roles table

-- ============================================================================
-- Grants
-- ============================================================================

-- Grant access to authenticated users (read-only)
GRANT SELECT ON model_pricing_history TO authenticated;
GRANT SELECT ON pricing_sync_log TO authenticated;

-- Grant access to service role (full access)
GRANT ALL ON model_pricing_history TO service_role;
GRANT ALL ON pricing_sync_log TO service_role;

-- Grant sequence usage
GRANT USAGE, SELECT ON SEQUENCE model_pricing_history_id_seq TO authenticated, service_role;
GRANT USAGE, SELECT ON SEQUENCE pricing_sync_log_id_seq TO authenticated, service_role;

-- ============================================================================
-- Table 3: pricing_sync_lock
-- Purpose: Distributed lock to prevent concurrent pricing syncs
-- ============================================================================

CREATE TABLE IF NOT EXISTS pricing_sync_lock (
    id SERIAL PRIMARY KEY,
    lock_key TEXT NOT NULL UNIQUE,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by TEXT NOT NULL,  -- Instance ID or request ID
    expires_at TIMESTAMPTZ NOT NULL,

    CONSTRAINT chk_valid_expiry CHECK (expires_at > locked_at)
);

-- Index for efficient lock checks
CREATE INDEX idx_pricing_sync_lock_key ON pricing_sync_lock(lock_key);
CREATE INDEX idx_pricing_sync_lock_expires ON pricing_sync_lock(expires_at);

-- Comments
COMMENT ON TABLE pricing_sync_lock IS 'Distributed lock for pricing sync operations';
COMMENT ON COLUMN pricing_sync_lock.lock_key IS 'Lock identifier (e.g., "pricing_sync_global")';
COMMENT ON COLUMN pricing_sync_lock.expires_at IS 'Lock expiry time for automatic cleanup of stale locks';

-- Enable RLS
ALTER TABLE pricing_sync_lock ENABLE ROW LEVEL SECURITY;

-- Policy: Service role full access
CREATE POLICY "Service role has full access to sync lock"
    ON pricing_sync_lock
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Grant access
GRANT ALL ON pricing_sync_lock TO service_role;
GRANT USAGE, SELECT ON SEQUENCE pricing_sync_lock_id_seq TO service_role;

-- Function to clean up expired locks automatically
CREATE OR REPLACE FUNCTION cleanup_expired_pricing_locks()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM pricing_sync_lock
    WHERE expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_expired_pricing_locks() IS 'Clean up expired pricing sync locks';

-- ============================================================================
-- Table 4: pricing_sync_jobs
-- Purpose: Track background pricing sync jobs for async operation
-- ============================================================================

CREATE TABLE IF NOT EXISTS pricing_sync_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),

    -- Job metadata
    triggered_by TEXT NOT NULL,  -- User email or system identifier
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- Results (populated when job completes)
    providers_synced INTEGER DEFAULT 0,
    models_updated INTEGER DEFAULT 0,
    models_skipped INTEGER DEFAULT 0,
    total_errors INTEGER DEFAULT 0,
    error_message TEXT,
    result_data JSONB,  -- Full result details

    -- Duration tracking
    duration_seconds NUMERIC(10, 2) GENERATED ALWAYS AS (
        CASE
            WHEN completed_at IS NOT NULL AND started_at IS NOT NULL THEN
                EXTRACT(EPOCH FROM (completed_at - started_at))
            ELSE NULL
        END
    ) STORED,

    -- Constraints
    CONSTRAINT chk_valid_timestamps CHECK (
        (started_at IS NULL OR started_at >= triggered_at) AND
        (completed_at IS NULL OR completed_at >= started_at)
    ),
    CONSTRAINT chk_non_negative_counts CHECK (
        providers_synced >= 0 AND
        models_updated >= 0 AND
        models_skipped >= 0 AND
        total_errors >= 0
    )
);

-- Indexes for efficient queries
CREATE INDEX idx_pricing_sync_jobs_job_id ON pricing_sync_jobs(job_id);
CREATE INDEX idx_pricing_sync_jobs_status ON pricing_sync_jobs(status);
CREATE INDEX idx_pricing_sync_jobs_triggered_at ON pricing_sync_jobs(triggered_at DESC);
CREATE INDEX idx_pricing_sync_jobs_triggered_by ON pricing_sync_jobs(triggered_by);

-- Comments
COMMENT ON TABLE pricing_sync_jobs IS 'Background job tracking for async pricing sync operations';
COMMENT ON COLUMN pricing_sync_jobs.job_id IS 'Human-readable job identifier (UUID string)';
COMMENT ON COLUMN pricing_sync_jobs.result_data IS 'Full JSON result data from sync operation';
COMMENT ON COLUMN pricing_sync_jobs.duration_seconds IS 'Job duration in seconds (auto-calculated)';

-- Enable RLS
ALTER TABLE pricing_sync_jobs ENABLE ROW LEVEL SECURITY;

-- Policy: Service role full access
CREATE POLICY "Service role has full access to sync jobs"
    ON pricing_sync_jobs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policy: Authenticated users can read their own jobs
CREATE POLICY "Users can read their own sync jobs"
    ON pricing_sync_jobs
    FOR SELECT
    TO authenticated
    USING (triggered_by = current_setting('request.jwt.claims', true)::json->>'email');

-- Policy: Admins can read all jobs (implement via app layer role check)

-- Grant access
GRANT SELECT ON pricing_sync_jobs TO authenticated;
GRANT ALL ON pricing_sync_jobs TO service_role;

-- Function to clean up old completed jobs (keep last 30 days)
CREATE OR REPLACE FUNCTION cleanup_old_pricing_jobs()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM pricing_sync_jobs
    WHERE status IN ('completed', 'failed')
      AND completed_at < NOW() - INTERVAL '30 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_old_pricing_jobs() IS 'Clean up pricing sync jobs older than 30 days';

-- ============================================================================
-- Verification
-- ============================================================================

-- Verify tables created
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'model_pricing_history') THEN
        RAISE EXCEPTION 'Table model_pricing_history was not created';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pricing_sync_log') THEN
        RAISE EXCEPTION 'Table pricing_sync_log was not created';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pricing_sync_lock') THEN
        RAISE EXCEPTION 'Table pricing_sync_lock was not created';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pricing_sync_jobs') THEN
        RAISE EXCEPTION 'Table pricing_sync_jobs was not created';
    END IF;

    RAISE NOTICE 'Migration completed successfully: 4 pricing sync tables created (pricing_history, sync_log, sync_lock, sync_jobs)';
END $$;
