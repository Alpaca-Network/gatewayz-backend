-- Migration: Enable Supabase GitHub Integration
-- Description: This migration adds a comment to document the GitHub integration setup
-- and ensures the schema is ready for automated deployments.
--
-- This migration serves as a test to verify:
-- 1. GitHub Actions workflow triggers on PR
-- 2. Migration validation passes
-- 3. Status check appears in GitHub branch protection

-- Add a comment to the public schema documenting the integration
COMMENT ON SCHEMA public IS 'Gatewayz API - Production schema with GitHub-integrated migrations';

-- Create an index on api_keys_new for faster lookups if it doesn't exist
-- Note: Using regular CREATE INDEX (not CONCURRENTLY) because:
-- 1. CONCURRENTLY cannot run inside a transaction block
-- 2. Supabase migrations run within transactions
-- 3. IF NOT EXISTS handles the case where index already exists
CREATE INDEX IF NOT EXISTS idx_api_keys_new_user_id_active
ON api_keys_new(user_id)
WHERE is_active = true;
