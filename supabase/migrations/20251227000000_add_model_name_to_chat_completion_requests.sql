-- Migration: Add model_name and provider_name to chat_completion_requests
-- Created: 2025-12-27
-- Description: Allow storing model name and provider as text fields instead of
--              requiring foreign key lookup. This ensures all requests are tracked
--              even if the model doesn't exist in the models table.

-- ============================================================================
-- ALTER TABLE: Add new columns and make model_id nullable
-- ============================================================================

-- Add model_name column (the model identifier from the request)
ALTER TABLE "public"."chat_completion_requests"
ADD COLUMN IF NOT EXISTS "model_name" TEXT;

-- Add provider_name column (the provider used for this request)
ALTER TABLE "public"."chat_completion_requests"
ADD COLUMN IF NOT EXISTS "provider_name" TEXT;

-- Add provider_id column (optional foreign key to providers table)
ALTER TABLE "public"."chat_completion_requests"
ADD COLUMN IF NOT EXISTS "provider_id" INTEGER REFERENCES "public"."providers"("id") ON DELETE SET NULL;

-- Make model_id nullable (optional foreign key)
ALTER TABLE "public"."chat_completion_requests"
ALTER COLUMN "model_id" DROP NOT NULL;

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Add index for model_name for faster lookups
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_name"
ON "public"."chat_completion_requests" ("model_name");

-- Add index for provider_name for faster lookups
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_provider_name"
ON "public"."chat_completion_requests" ("provider_name");

-- Composite index for model + provider analytics
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_provider"
ON "public"."chat_completion_requests" ("model_name", "provider_name", "created_at" DESC);

-- Add index for provider_id
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_provider_id"
ON "public"."chat_completion_requests" ("provider_id");

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON COLUMN "public"."chat_completion_requests"."model_name" IS 'Model identifier from the request (e.g., gpt-4, google/gemini-2.0-flash-exp:free)';
COMMENT ON COLUMN "public"."chat_completion_requests"."provider_name" IS 'Provider used for this request (e.g., openrouter, openai, anthropic)';
COMMENT ON COLUMN "public"."chat_completion_requests"."model_id" IS 'Optional reference to the model in models table (nullable)';
COMMENT ON COLUMN "public"."chat_completion_requests"."provider_id" IS 'Optional reference to the provider in providers table (nullable)';

-- ============================================================================
-- RECONCILIATION FUNCTION
-- ============================================================================
-- This function backfills model_id and provider_id from text fields
-- Run this periodically or after adding new models/providers to the database

CREATE OR REPLACE FUNCTION reconcile_chat_completion_request_ids()
RETURNS TABLE(
    updated_count INTEGER,
    message TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_provider_updates INTEGER := 0;
    v_model_updates INTEGER := 0;
    v_total_updates INTEGER := 0;
BEGIN
    -- Update provider_id based on provider_name
    UPDATE chat_completion_requests ccr
    SET provider_id = p.id
    FROM providers p
    WHERE ccr.provider_id IS NULL
      AND ccr.provider_name IS NOT NULL
      AND (
        LOWER(p.slug) = LOWER(ccr.provider_name)
        OR LOWER(p.name) = LOWER(ccr.provider_name)
      );

    GET DIAGNOSTICS v_provider_updates = ROW_COUNT;

    -- Update model_id based on model_name and provider_id
    UPDATE chat_completion_requests ccr
    SET model_id = m.id
    FROM models m
    WHERE ccr.model_id IS NULL
      AND ccr.model_name IS NOT NULL
      AND (
        m.model_id = ccr.model_name
        OR m.provider_model_id = ccr.model_name
      )
      AND (
        ccr.provider_id IS NULL
        OR m.provider_id = ccr.provider_id
      );

    GET DIAGNOSTICS v_model_updates = ROW_COUNT;

    v_total_updates := v_provider_updates + v_model_updates;

    RETURN QUERY SELECT v_total_updates,
        'Reconciliation complete. Updated ' || v_provider_updates || ' providers and ' ||
        v_model_updates || ' models (' || v_total_updates || ' total).';
END;
$$;

COMMENT ON FUNCTION reconcile_chat_completion_request_ids() IS
'Backfills model_id and provider_id for chat completion requests based on model_name and provider_name text fields. Run this after adding new models or providers to the database.';
