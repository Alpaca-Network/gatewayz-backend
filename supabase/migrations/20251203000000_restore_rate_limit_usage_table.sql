-- Restore rate_limit_usage table that was accidentally dropped by 20251126165648_remote_schema.sql
-- This table is required for tracking rate limit usage per API key

-- Create sequence first (must exist before table creation)
CREATE SEQUENCE IF NOT EXISTS "public"."rate_limit_usage_id_seq";

-- Create rate_limit_usage table
-- Tracks rate limit window usage (minute, hour, day windows)
CREATE TABLE IF NOT EXISTS "public"."rate_limit_usage" (
    "id" bigint NOT NULL DEFAULT nextval('rate_limit_usage_id_seq'::regclass),
    "user_id" bigint NOT NULL,
    "api_key" text NOT NULL,
    "window_type" text NOT NULL,
    "window_start" timestamp with time zone NOT NULL,
    "requests_count" integer DEFAULT 0,
    "tokens_count" integer DEFAULT 0,
    "created_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    "updated_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    CONSTRAINT "rate_limit_usage_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "rate_limit_usage_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE,
    CONSTRAINT "rate_limit_usage_unique" UNIQUE ("api_key", "window_type", "window_start")
);

-- Create indexes on rate_limit_usage for performance
CREATE INDEX IF NOT EXISTS "rate_limit_usage_api_key_idx" ON "public"."rate_limit_usage" USING btree ("api_key");
CREATE INDEX IF NOT EXISTS "rate_limit_usage_user_id_idx" ON "public"."rate_limit_usage" USING btree ("user_id");
CREATE INDEX IF NOT EXISTS "rate_limit_usage_window_start_idx" ON "public"."rate_limit_usage" USING btree ("window_start");
CREATE INDEX IF NOT EXISTS "rate_limit_usage_window_type_idx" ON "public"."rate_limit_usage" USING btree ("window_type");

-- Set sequence ownership
ALTER SEQUENCE "public"."rate_limit_usage_id_seq" OWNED BY "public"."rate_limit_usage"."id";

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON "public"."rate_limit_usage" TO "authenticated";
GRANT ALL ON "public"."rate_limit_usage" TO "service_role";

-- Add RLS policies for security
ALTER TABLE "public"."rate_limit_usage" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role can manage rate limit usage" ON "public"."rate_limit_usage"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Users can read their own rate limit usage" ON "public"."rate_limit_usage"
    FOR SELECT
    TO authenticated
    USING (true);  -- Allow authenticated users to read rate limit usage
