-- Migration: Drop legacy `credits` column from users table
-- All credit logic now uses `subscription_allowance` (monthly, resets) and
-- `purchased_credits` (one-time, never expire). The `credits` column has been
-- fully migrated and is no longer written to by application code.

-- Drop the index and constraint first, then the column
DROP INDEX IF EXISTS idx_users_credits;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_credits_non_negative;
ALTER TABLE users DROP COLUMN IF EXISTS credits;
