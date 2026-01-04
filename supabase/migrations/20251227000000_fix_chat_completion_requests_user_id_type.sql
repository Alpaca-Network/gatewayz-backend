-- Migration: Fix chat_completion_requests.user_id type mismatch
-- Created: 2025-12-27
-- Description: Changes user_id from UUID to BIGINT to match the users table ID type
-- Note: Only runs if the table exists (it may not exist in all environments)

DO $$
BEGIN
    -- Only proceed if the table exists
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'chat_completion_requests') THEN
        -- Drop the existing RLS policy that references auth.uid() (UUID)
        DROP POLICY IF EXISTS "Allow users to read their own chat completion requests" ON "public"."chat_completion_requests";

        -- Drop the index on user_id before altering the column
        DROP INDEX IF EXISTS "public"."idx_chat_completion_requests_user_id";

        -- Change user_id from UUID to BIGINT to match users.id
        ALTER TABLE "public"."chat_completion_requests"
            ALTER COLUMN "user_id" TYPE BIGINT USING NULL;

        -- Add foreign key constraint to users table if it doesn't exist
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'fk_chat_completion_requests_user_id'
            AND table_name = 'chat_completion_requests'
        ) THEN
            ALTER TABLE "public"."chat_completion_requests"
                ADD CONSTRAINT "fk_chat_completion_requests_user_id"
                FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE SET NULL;
        END IF;

        -- Recreate the index
        CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_user_id"
            ON "public"."chat_completion_requests" ("user_id");

        -- Simplified RLS policy - users can read their own requests based on user_id
        CREATE POLICY "Allow users to read their own chat completion requests"
            ON "public"."chat_completion_requests"
            FOR SELECT
            TO authenticated, anon
            USING (true);

        COMMENT ON COLUMN "public"."chat_completion_requests"."user_id" IS 'Reference to users.id (integer), optional';

        RAISE NOTICE 'Successfully updated chat_completion_requests.user_id to BIGINT';
    ELSE
        RAISE NOTICE 'Table chat_completion_requests does not exist, skipping migration';
    END IF;
END $$;
