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

-- Create an index on api_keys for faster lookups if it doesn't exist
-- This is a no-op if the index already exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
        AND tablename = 'api_keys'
        AND indexname = 'idx_api_keys_user_id_active'
    ) THEN
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_api_keys_user_id_active
        ON api_keys(user_id)
        WHERE is_active = true;
    END IF;
EXCEPTION
    WHEN duplicate_table THEN
        NULL; -- Index already exists, ignore
    WHEN undefined_table THEN
        NULL; -- Table doesn't exist yet, ignore
END $$;
