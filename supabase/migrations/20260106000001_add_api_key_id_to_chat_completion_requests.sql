-- Migration: Add api_key_id to chat_completion_requests table
-- Created: 2026-01-06
-- Description: Adds api_key_id column to track which API key was used for each request
-- This enables better analytics and audit trails for API key usage

DO $$
BEGIN
    -- Only proceed if the table exists
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'chat_completion_requests') THEN
        -- Add api_key_id column if it doesn't exist
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = 'chat_completion_requests'
            AND column_name = 'api_key_id'
        ) THEN
            ALTER TABLE "public"."chat_completion_requests"
                ADD COLUMN "api_key_id" BIGINT;

            -- Add foreign key constraint to api_keys_new table
            ALTER TABLE "public"."chat_completion_requests"
                ADD CONSTRAINT "fk_chat_completion_requests_api_key_id"
                FOREIGN KEY ("api_key_id") REFERENCES "public"."api_keys_new"("id") ON DELETE SET NULL;

            -- Create index for better query performance
            CREATE INDEX "idx_chat_completion_requests_api_key_id"
                ON "public"."chat_completion_requests" ("api_key_id");

            -- Add comment to document the column
            COMMENT ON COLUMN "public"."chat_completion_requests"."api_key_id" IS 'Reference to api_keys_new.id - tracks which API key was used for this request';

            RAISE NOTICE 'Successfully added api_key_id column to chat_completion_requests table';
        ELSE
            RAISE NOTICE 'Column api_key_id already exists in chat_completion_requests table, skipping';
        END IF;
    ELSE
        RAISE NOTICE 'Table chat_completion_requests does not exist, skipping migration';
    END IF;
END $$;
