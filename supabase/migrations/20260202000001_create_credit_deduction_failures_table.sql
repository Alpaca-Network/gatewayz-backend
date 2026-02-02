-- Migration: Create credit_deduction_failures table for billing reconciliation
-- This table tracks failed credit deductions from streaming requests for manual reconciliation
--
-- Background:
-- When streaming API requests complete, credit deduction happens in a background task.
-- If this task fails (database issues, network problems, etc.), the user gets the response
-- but credits are not deducted. This table provides an audit trail for reconciliation.

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

    -- Indexes for common queries
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

-- Add trigger for updated_at
CREATE OR REPLACE FUNCTION update_credit_deduction_failures_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_credit_deduction_failures_updated_at
    ON credit_deduction_failures;

CREATE TRIGGER trigger_update_credit_deduction_failures_updated_at
    BEFORE UPDATE ON credit_deduction_failures
    FOR EACH ROW
    EXECUTE FUNCTION update_credit_deduction_failures_updated_at();

-- Add comments for documentation
COMMENT ON TABLE credit_deduction_failures IS
    'Tracks failed credit deductions from streaming requests for manual reconciliation';

COMMENT ON COLUMN credit_deduction_failures.status IS
    'pending: needs reconciliation, resolved: credits recovered, written_off: cannot recover';

COMMENT ON COLUMN credit_deduction_failures.cost_usd IS
    'The amount that should have been deducted from user credits';

-- Grant appropriate permissions (adjust based on your RLS policies)
ALTER TABLE credit_deduction_failures ENABLE ROW LEVEL SECURITY;

-- Policy: Only service role can insert (from the API)
CREATE POLICY credit_deduction_failures_insert_policy ON credit_deduction_failures
    FOR INSERT
    TO authenticated
    WITH CHECK (true);

-- Policy: Only service role can select/update (for admin reconciliation)
CREATE POLICY credit_deduction_failures_select_policy ON credit_deduction_failures
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY credit_deduction_failures_update_policy ON credit_deduction_failures
    FOR UPDATE
    TO authenticated
    USING (true)
    WITH CHECK (true);
