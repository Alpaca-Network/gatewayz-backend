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

    RAISE NOTICE 'Migration completed successfully: pricing sync tables created';
END $$;
