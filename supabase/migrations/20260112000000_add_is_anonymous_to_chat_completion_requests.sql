-- Migration: Add is_anonymous column to chat_completion_requests table
-- Created: 2026-01-12
-- Description: Adds is_anonymous boolean column to distinguish intentional anonymous requests
-- from data quality issues where api_key_id is NULL due to lookup failures.

DO $$
BEGIN
    -- Only proceed if the table exists
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_name = 'chat_completion_requests'
    ) THEN
        -- Add is_anonymous column if it doesn't exist
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = 'chat_completion_requests'
            AND column_name = 'is_anonymous'
        ) THEN
            -- Add the column with a default value of FALSE
            ALTER TABLE "public"."chat_completion_requests"
                ADD COLUMN "is_anonymous" BOOLEAN NOT NULL DEFAULT FALSE;

            -- Create index for better query performance on anonymous filtering
            CREATE INDEX "idx_chat_completion_requests_is_anonymous"
                ON "public"."chat_completion_requests" ("is_anonymous");

            -- Add comment to document the column
            COMMENT ON COLUMN "public"."chat_completion_requests"."is_anonymous" IS
                'Indicates if this request was made anonymously (without authentication). '
                'TRUE = intentional anonymous request, FALSE = authenticated request. '
                'Helps distinguish anonymous requests from failed API key lookups.';

            RAISE NOTICE 'Successfully added is_anonymous column to chat_completion_requests table';
        ELSE
            RAISE NOTICE 'Column is_anonymous already exists in chat_completion_requests table, skipping';
        END IF;

        -- Backfill existing data: Set is_anonymous = TRUE where both user_id and api_key_id are NULL
        -- This assumes that records with both NULL likely represent anonymous requests
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = 'chat_completion_requests'
            AND column_name = 'is_anonymous'
        ) THEN
            UPDATE "public"."chat_completion_requests"
            SET "is_anonymous" = TRUE
            WHERE "user_id" IS NULL
              AND "api_key_id" IS NULL
              AND "is_anonymous" = FALSE;

            RAISE NOTICE 'Backfilled is_anonymous column for existing anonymous requests';
        END IF;

    ELSE
        RAISE NOTICE 'Table chat_completion_requests does not exist, skipping migration';
    END IF;
END $$;

-- Add a helpful view to analyze API key tracking quality
CREATE OR REPLACE VIEW "public"."api_key_tracking_quality" AS
SELECT
    DATE_TRUNC('hour', created_at) as hour,
    COUNT(*) as total_requests,
    COUNT(api_key_id) as requests_with_api_key,
    COUNT(*) FILTER (WHERE api_key_id IS NULL) as requests_without_api_key,
    COUNT(*) FILTER (WHERE is_anonymous = TRUE) as anonymous_requests,
    COUNT(*) FILTER (WHERE api_key_id IS NULL AND user_id IS NOT NULL) as potential_lookup_failures,
    ROUND(
        (COUNT(api_key_id)::NUMERIC / NULLIF(COUNT(*), 0)) * 100,
        2
    ) as tracking_rate_percent
FROM "public"."chat_completion_requests"
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY DATE_TRUNC('hour', created_at)
ORDER BY hour DESC;

COMMENT ON VIEW "public"."api_key_tracking_quality" IS
    'Hourly breakdown of API key tracking quality metrics for the last 7 days. '
    'Shows total requests, successful tracking rate, anonymous requests, and potential lookup failures.';
