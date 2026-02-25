-- Migration: Add request_id column to credit_transactions for idempotency
--
-- Background:
-- When HTTP timeouts or retries occur, deduct_credits() can be called multiple
-- times for the same logical request, causing double-charges. Adding a unique
-- request_id column allows the application to detect and skip duplicate
-- deductions, providing idempotent credit operations.
--
-- The column is NULLABLE for backwards compatibility (existing rows and
-- non-API-usage transactions like admin credits, refunds, etc. don't need it).
-- Only API_USAGE transactions from chat completion requests will populate this.

-- ============================================================================
-- UP MIGRATION
-- ============================================================================

-- Add request_id column (UUID, nullable for backwards compatibility)
ALTER TABLE credit_transactions
    ADD COLUMN IF NOT EXISTS request_id UUID;

-- Create a unique index on request_id (partial: only where NOT NULL)
-- This enforces idempotency: only one transaction per request_id is allowed
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_transactions_request_id
    ON credit_transactions(request_id)
    WHERE request_id IS NOT NULL;

-- Add a regular index for fast lookups by request_id
-- The unique index above already serves as a lookup index, but we add a comment
-- for clarity that the unique partial index is used for both constraint and lookup.

-- Add comment for documentation
COMMENT ON COLUMN credit_transactions.request_id IS
    'Unique idempotency key (UUID) to prevent duplicate credit deductions on retries/timeouts. '
    'Only populated for API_USAGE transactions. NULL for legacy and non-API transactions.';

-- Notify PostgREST to pick up schema changes
NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- DOWN MIGRATION (commented out - run manually to rollback)
-- ============================================================================
-- To rollback this migration, run:
--
-- DROP INDEX IF EXISTS idx_credit_transactions_request_id;
-- ALTER TABLE credit_transactions DROP COLUMN IF EXISTS request_id;
