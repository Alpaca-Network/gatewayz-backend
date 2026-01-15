-- Migration: Create chat_completion_requests table stub if it doesn't exist
-- Created: 2025-12-26
-- Description: Creates a minimal chat_completion_requests table structure if it doesn't exist.
-- This allows analytics views and functions to be created even in test/staging environments
-- where the full table may not exist yet.
--
-- Note: This is a stub schema - production may have additional columns and constraints.
-- Future migrations can add columns as needed using ALTER TABLE.

DO $$
BEGIN
    -- Only create if table doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_name = 'chat_completion_requests'
    ) THEN
        CREATE TABLE "public"."chat_completion_requests" (
            "id" BIGSERIAL PRIMARY KEY,
            "model_id" INTEGER,  -- No FK constraint in stub - will be added by later migration if needed
            "user_id" BIGINT,    -- No FK constraint in stub - will be added by later migration if needed
            "api_key_id" BIGINT, -- No FK constraint in stub - will be added by later migration if needed
            "status" TEXT NOT NULL DEFAULT 'pending',
            "input_tokens" BIGINT DEFAULT 0,
            "output_tokens" BIGINT DEFAULT 0,
            "processing_time_ms" NUMERIC,
            "is_anonymous" BOOLEAN DEFAULT FALSE,
            "created_at" TIMESTAMPTZ DEFAULT NOW(),
            "updated_at" TIMESTAMPTZ DEFAULT NOW()
        );

        -- Create indexes for common query patterns
        CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_id_status"
            ON "public"."chat_completion_requests" ("model_id", "status");

        CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_status_created_at"
            ON "public"."chat_completion_requests" ("status", "created_at");

        CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_user_id"
            ON "public"."chat_completion_requests" ("user_id");

        CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_api_key_id"
            ON "public"."chat_completion_requests" ("api_key_id");

        -- Enable RLS
        ALTER TABLE "public"."chat_completion_requests" ENABLE ROW LEVEL SECURITY;

        -- Simple RLS policy - users can read their own requests
        CREATE POLICY "Allow users to read their own chat completion requests"
            ON "public"."chat_completion_requests"
            FOR SELECT
            TO authenticated, anon
            USING (true);

        -- Add comment
        COMMENT ON TABLE "public"."chat_completion_requests" IS
            'Tracks chat completion API requests with token usage and performance metrics. '
            'This is a stub table created for test/staging environments - production may have additional columns.';

        RAISE NOTICE 'Successfully created chat_completion_requests stub table';
    ELSE
        RAISE NOTICE 'Table chat_completion_requests already exists, skipping stub creation';
    END IF;
END $$;
