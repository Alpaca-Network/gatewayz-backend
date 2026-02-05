-- Migration: Drop Pricing Sync Infrastructure Tables (Final)
-- Created: 2026-02-05
-- Phase 4 of Pricing Sync Deprecation
-- Purpose: Permanently remove pricing sync tables (second attempt after rollback)
-- Related Issues: #1061, #1062, #1063, #1064

-- ============================================================================
-- IMPORTANT: Final cleanup of pricing sync infrastructure
-- ============================================================================
-- ✅ Deletes: pricing_sync_jobs, pricing_sync_lock, pricing_sync_log
-- ✅ Keeps: model_pricing_history (valuable audit trail)
-- ✅ Keeps: model_pricing (actively used by model sync and chat handlers)
-- ============================================================================

-- Verification message
DO $$
BEGIN
    RAISE NOTICE 'Starting final cleanup: Dropping pricing sync infrastructure tables';
END $$;

-- ============================================================================
-- Drop Tables (in order to avoid foreign key conflicts)
-- ============================================================================

-- Drop pricing sync jobs table
DROP TABLE IF EXISTS pricing_sync_jobs CASCADE;

-- Drop pricing sync lock table
DROP TABLE IF EXISTS pricing_sync_lock CASCADE;

-- Drop pricing sync log table
DROP TABLE IF EXISTS pricing_sync_log CASCADE;

-- ============================================================================
-- Drop Related Functions
-- ============================================================================

-- Drop cleanup functions for sync tables
DROP FUNCTION IF EXISTS cleanup_expired_pricing_locks() CASCADE;
DROP FUNCTION IF EXISTS cleanup_old_pricing_jobs() CASCADE;

-- ============================================================================
-- Update Comments on Retained Tables
-- ============================================================================

-- Update model_pricing_history comment to reflect new status
COMMENT ON TABLE model_pricing_history IS
'Historical pricing changes for models with audit trail. Pricing is now synced via model_catalog_sync, not the deprecated pricing_sync system.';

-- Ensure model_pricing table comment is accurate
COMMENT ON TABLE model_pricing IS
'Current pricing data for all models. Updated by model_catalog_sync service during model synchronization.';

-- ============================================================================
-- Verification
-- ============================================================================

-- Verify tables were dropped successfully
DO $$
BEGIN
    -- Check that sync tables are gone
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pricing_sync_jobs') THEN
        RAISE EXCEPTION 'Failed to drop pricing_sync_jobs table';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pricing_sync_lock') THEN
        RAISE EXCEPTION 'Failed to drop pricing_sync_lock table';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pricing_sync_log') THEN
        RAISE EXCEPTION 'Failed to drop pricing_sync_log table';
    END IF;

    -- Check that model_pricing_history is still there
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'model_pricing_history') THEN
        RAISE EXCEPTION 'model_pricing_history table is missing (should be kept)';
    END IF;

    -- Check that model_pricing is still there
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'model_pricing') THEN
        RAISE EXCEPTION 'model_pricing table is missing (should be kept)';
    END IF;

    -- Check that functions are gone
    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'public'
        AND p.proname = 'cleanup_expired_pricing_locks'
    ) THEN
        RAISE EXCEPTION 'Failed to drop cleanup_expired_pricing_locks function';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'public'
        AND p.proname = 'cleanup_old_pricing_jobs'
    ) THEN
        RAISE EXCEPTION 'Failed to drop cleanup_old_pricing_jobs function';
    END IF;

    RAISE NOTICE '✅ Phase 4 completed successfully!';
    RAISE NOTICE '✅ Dropped tables: pricing_sync_jobs, pricing_sync_lock, pricing_sync_log';
    RAISE NOTICE '✅ Dropped functions: cleanup_expired_pricing_locks, cleanup_old_pricing_jobs';
    RAISE NOTICE '✅ Kept tables: model_pricing_history (audit trail), model_pricing (active use)';
    RAISE NOTICE '✅ Database cleanup complete - pricing sync infrastructure removed';
END $$;
