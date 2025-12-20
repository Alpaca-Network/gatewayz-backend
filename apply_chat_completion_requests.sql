-- Standalone script to create chat_completion_requests table
-- Run this in Supabase SQL Editor if migration didn't apply

-- ============================================================================
-- CHAT COMPLETION REQUESTS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."chat_completion_requests" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "request_id" TEXT NOT NULL UNIQUE,
    "model_id" INTEGER NOT NULL REFERENCES "public"."models"("id") ON DELETE CASCADE,
    "input_tokens" INTEGER NOT NULL DEFAULT 0,
    "output_tokens" INTEGER NOT NULL DEFAULT 0,
    "total_tokens" INTEGER GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    "processing_time_ms" INTEGER NOT NULL,
    "status" TEXT DEFAULT 'completed' CHECK (status IN ('completed', 'failed', 'partial')),
    "error_message" TEXT,
    "user_id" UUID,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE "public"."chat_completion_requests" IS 'Tracks all chat completion requests with token usage and processing metrics';
COMMENT ON COLUMN "public"."chat_completion_requests"."request_id" IS 'Unique identifier for the request';
COMMENT ON COLUMN "public"."chat_completion_requests"."model_id" IS 'Reference to the model used for this request';
COMMENT ON COLUMN "public"."chat_completion_requests"."input_tokens" IS 'Number of tokens in the input/prompt';
COMMENT ON COLUMN "public"."chat_completion_requests"."output_tokens" IS 'Number of tokens in the completion/response';
COMMENT ON COLUMN "public"."chat_completion_requests"."total_tokens" IS 'Total tokens (input + output), computed column';
COMMENT ON COLUMN "public"."chat_completion_requests"."processing_time_ms" IS 'Total time to process the request in milliseconds';
COMMENT ON COLUMN "public"."chat_completion_requests"."status" IS 'Status of the request (completed, failed, partial)';
COMMENT ON COLUMN "public"."chat_completion_requests"."error_message" IS 'Error message if the request failed';
COMMENT ON COLUMN "public"."chat_completion_requests"."user_id" IS 'Optional user identifier for the request';

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_request_id" ON "public"."chat_completion_requests" ("request_id");
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_id" ON "public"."chat_completion_requests" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_created_at" ON "public"."chat_completion_requests" ("created_at" DESC);
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_user_id" ON "public"."chat_completion_requests" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_status" ON "public"."chat_completion_requests" ("status");
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_created" ON "public"."chat_completion_requests" ("model_id", "created_at" DESC);

-- ============================================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================
ALTER TABLE "public"."chat_completion_requests" ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Allow users to read their own chat completion requests" ON "public"."chat_completion_requests";
DROP POLICY IF EXISTS "Allow service role full access to chat completion requests" ON "public"."chat_completion_requests";

-- Allow authenticated users to read their own requests
CREATE POLICY "Allow users to read their own chat completion requests"
    ON "public"."chat_completion_requests"
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- Allow service role full access
CREATE POLICY "Allow service role full access to chat completion requests"
    ON "public"."chat_completion_requests"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- GRANTS
-- ============================================================================
GRANT SELECT ON "public"."chat_completion_requests" TO authenticated;
GRANT ALL ON "public"."chat_completion_requests" TO service_role;

-- Verify table was created
SELECT 'Table chat_completion_requests created successfully!' as status,
       COUNT(*) as column_count
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'chat_completion_requests';
