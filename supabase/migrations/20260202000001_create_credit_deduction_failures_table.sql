-- Migration: Create credit_deduction_failures table for billing reconciliation
-- This table tracks failed credit deductions from streaming requests for manual reconciliation
--
-- Background:
-- When streaming API requests complete, credit deduction happens in a background task.
-- If this task fails (database issues, network problems, etc.), the user gets the response
-- but credits are not deducted. This table provides an audit trail for reconciliation.
--
-- Security Note:
-- This table contains sensitive billing data and should only be accessible by:
-- - The service role (for API inserts)
-- - Admin users (for reconciliation queries)

-- ============================================================================
-- PRE-CREATE CLEANUP (make migration idempotent)
-- ============================================================================

-- Drop existing policies and trigger if table exists (for idempotency)
-- Using DO block to conditionally drop only if the table exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'credit_deduction_failures') THEN
        DROP POLICY IF EXISTS credit_deduction_failures_insert_policy ON credit_deduction_failures;
        DROP POLICY IF EXISTS credit_deduction_failures_select_policy ON credit_deduction_failures;
        DROP POLICY IF EXISTS credit_deduction_failures_update_policy ON credit_deduction_failures;
        DROP TRIGGER IF EXISTS trigger_update_credit_deduction_failures_updated_at ON credit_deduction_failures;
    END IF;
END $$;

-- ============================================================================
-- UP MIGRATION
-- ============================================================================

-- Create the credit_deduction_failures table
CREATE TABLE IF NOT EXISTS credit_deduction_failures (
    id BIGSERIAL PRIMARY KEY,

    -- User identification
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    api_key_prefix VARCHAR(20),  -- First 10 chars + "..." for debugging without exposing full key

    -- Usage details
    model VARCHAR(255) NOT NULL,
    cost_usd DECIMAL(12, 8) NOT NULL,  -- High precision for accurate billing
    total_tokens INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,

    -- Request context
    endpoint VARCHAR(100) NOT NULL DEFAULT '/v1/chat/completions',
    error_message TEXT,

    -- Reconciliation status
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, resolved, written_off
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(255),  -- Admin who resolved it
    resolution_notes TEXT,

    -- Audit timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT valid_status CHECK (status IN ('pending', 'resolved', 'written_off'))
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_credit_deduction_failures_user_id
    ON credit_deduction_failures(user_id);

CREATE INDEX IF NOT EXISTS idx_credit_deduction_failures_status
    ON credit_deduction_failures(status);

CREATE INDEX IF NOT EXISTS idx_credit_deduction_failures_created_at
    ON credit_deduction_failures(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_credit_deduction_failures_pending
    ON credit_deduction_failures(status, created_at DESC)
    WHERE status = 'pending';

-- Add trigger for updated_at (CREATE OR REPLACE for idempotency)
CREATE OR REPLACE FUNCTION update_credit_deduction_failures_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_credit_deduction_failures_updated_at
    BEFORE UPDATE ON credit_deduction_failures
    FOR EACH ROW
    EXECUTE FUNCTION update_credit_deduction_failures_updated_at();

-- Add comments for documentation
COMMENT ON TABLE credit_deduction_failures IS
    'Tracks failed credit deductions from streaming requests for manual reconciliation. Service role only.';

COMMENT ON COLUMN credit_deduction_failures.status IS
    'pending: needs reconciliation, resolved: credits recovered, written_off: cannot recover';

COMMENT ON COLUMN credit_deduction_failures.cost_usd IS
    'The amount that should have been deducted from user credits';

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
-- Enable RLS - this table contains sensitive billing data
ALTER TABLE credit_deduction_failures ENABLE ROW LEVEL SECURITY;

-- Policy: Service role has full access (for API inserts and admin operations)
-- Note: Service role bypasses RLS by default, but we add explicit policies
-- for documentation and to support potential future role changes.

-- Insert policy: Only service role can insert (API backend uses service role)
-- Regular authenticated users cannot insert directly
CREATE POLICY credit_deduction_failures_insert_policy ON credit_deduction_failures
    FOR INSERT
    TO service_role
    WITH CHECK (true);

-- Select policy: Service role and admin users can read
-- Admin check: users with tier = 'admin' can view for reconciliation
CREATE POLICY credit_deduction_failures_select_policy ON credit_deduction_failures
    FOR SELECT
    TO authenticated
    USING (
        -- Service role has full access (checked via current_setting)
        current_setting('role', true) = 'service_role'
        OR
        -- Admin users can view their own records or all if they're an admin
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::bigint
            AND users.tier = 'admin'
        )
    );

-- Update policy: Only service role can update (for admin reconciliation via API)
CREATE POLICY credit_deduction_failures_update_policy ON credit_deduction_failures
    FOR UPDATE
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- DOWN MIGRATION (commented out - run manually to rollback)
-- ============================================================================
-- To rollback this migration, run:
--
-- DROP POLICY IF EXISTS credit_deduction_failures_update_policy ON credit_deduction_failures;
-- DROP POLICY IF EXISTS credit_deduction_failures_select_policy ON credit_deduction_failures;
-- DROP POLICY IF EXISTS credit_deduction_failures_insert_policy ON credit_deduction_failures;
-- DROP TRIGGER IF EXISTS trigger_update_credit_deduction_failures_updated_at ON credit_deduction_failures;
-- DROP FUNCTION IF EXISTS update_credit_deduction_failures_updated_at();
-- DROP INDEX IF EXISTS idx_credit_deduction_failures_pending;
-- DROP INDEX IF EXISTS idx_credit_deduction_failures_created_at;
-- DROP INDEX IF EXISTS idx_credit_deduction_failures_status;
-- DROP INDEX IF EXISTS idx_credit_deduction_failures_user_id;
-- DROP TABLE IF EXISTS credit_deduction_failures;
