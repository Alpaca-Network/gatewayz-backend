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

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON COLUMN "public"."chat_completion_requests"."model_name" IS 'Model identifier from the request (e.g., gpt-4, google/gemini-2.0-flash-exp:free)';
COMMENT ON COLUMN "public"."chat_completion_requests"."provider_name" IS 'Provider used for this request (e.g., openrouter, openai, anthropic)';
COMMENT ON COLUMN "public"."chat_completion_requests"."model_id" IS 'Optional reference to the model in models table (nullable)';
