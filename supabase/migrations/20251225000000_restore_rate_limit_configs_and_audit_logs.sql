-- Restore rate_limit_configs and api_key_audit_logs tables
-- These were accidentally dropped by 20251126165648_remote_schema.sql
-- Required for API key rate limiting and audit trail functionality

-- ============================================
-- RATE LIMIT CONFIGS TABLE
-- ============================================

-- Create sequence first (must exist before table creation)
CREATE SEQUENCE IF NOT EXISTS "public"."rate_limit_configs_id_seq";

-- Create rate_limit_configs table
-- Stores per-API-key rate limit configurations
-- NOTE: Defaults match original migration 20251105000000_add_missing_rate_limit_tables.sql
CREATE TABLE IF NOT EXISTS "public"."rate_limit_configs" (
    "id" bigint NOT NULL DEFAULT nextval('rate_limit_configs_id_seq'::regclass),
    "api_key_id" bigint NOT NULL,
    "window_type" text DEFAULT 'sliding'::text,
    "window_size" integer DEFAULT 3600,
    "max_requests" integer DEFAULT 1000,
    "max_tokens" integer DEFAULT 1000000,
    "burst_limit" integer DEFAULT 100,
    "concurrency_limit" integer DEFAULT 10,
    "is_active" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    "updated_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    CONSTRAINT "rate_limit_configs_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "rate_limit_configs_api_key_id_fkey" FOREIGN KEY ("api_key_id") REFERENCES "public"."api_keys_new"("id") ON DELETE CASCADE
);

-- Create index on api_key_id for faster lookups
CREATE INDEX IF NOT EXISTS "rate_limit_configs_api_key_id_idx" ON "public"."rate_limit_configs" USING btree ("api_key_id");

-- Set sequence ownership
ALTER SEQUENCE "public"."rate_limit_configs_id_seq" OWNED BY "public"."rate_limit_configs"."id";

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON "public"."rate_limit_configs" TO "authenticated";
GRANT ALL ON "public"."rate_limit_configs" TO "service_role";

-- Add RLS policies for security
ALTER TABLE "public"."rate_limit_configs" ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS "Service role can manage rate limit configs" ON "public"."rate_limit_configs";
DROP POLICY IF EXISTS "Users can read their own rate limit configs" ON "public"."rate_limit_configs";

CREATE POLICY "Service role can manage rate limit configs" ON "public"."rate_limit_configs"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Users can read their own rate limit configs" ON "public"."rate_limit_configs"
    FOR SELECT
    TO authenticated
    USING (
        api_key_id IN (
            SELECT id FROM public.api_keys_new
            WHERE user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid())
        )
    );

-- ============================================
-- API KEY AUDIT LOGS TABLE
-- ============================================

-- Create sequence first (must exist before table creation)
CREATE SEQUENCE IF NOT EXISTS "public"."api_key_audit_logs_id_seq";

-- Create api_key_audit_logs table
-- Stores audit trail of API key operations
CREATE TABLE IF NOT EXISTS "public"."api_key_audit_logs" (
    "id" bigint NOT NULL DEFAULT nextval('api_key_audit_logs_id_seq'::regclass),
    "user_id" bigint NOT NULL,
    "api_key_id" bigint,
    "action" text NOT NULL,
    "details" jsonb DEFAULT '{}'::jsonb,
    "timestamp" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    CONSTRAINT "api_key_audit_logs_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "api_key_audit_logs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE,
    CONSTRAINT "api_key_audit_logs_api_key_id_fkey" FOREIGN KEY ("api_key_id") REFERENCES "public"."api_keys_new"("id") ON DELETE SET NULL
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS "api_key_audit_logs_user_id_idx" ON "public"."api_key_audit_logs" USING btree ("user_id");
CREATE INDEX IF NOT EXISTS "api_key_audit_logs_api_key_id_idx" ON "public"."api_key_audit_logs" USING btree ("api_key_id");
CREATE INDEX IF NOT EXISTS "api_key_audit_logs_action_idx" ON "public"."api_key_audit_logs" USING btree ("action");
CREATE INDEX IF NOT EXISTS "api_key_audit_logs_timestamp_idx" ON "public"."api_key_audit_logs" USING btree ("timestamp");

-- Set sequence ownership
ALTER SEQUENCE "public"."api_key_audit_logs_id_seq" OWNED BY "public"."api_key_audit_logs"."id";

-- Grant permissions
GRANT SELECT ON "public"."api_key_audit_logs" TO "authenticated";
GRANT ALL ON "public"."api_key_audit_logs" TO "service_role";

-- Add RLS policies for security
ALTER TABLE "public"."api_key_audit_logs" ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS "Service role can manage audit logs" ON "public"."api_key_audit_logs";
DROP POLICY IF EXISTS "Users can read their own audit logs" ON "public"."api_key_audit_logs";

CREATE POLICY "Service role can manage audit logs" ON "public"."api_key_audit_logs"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Users can read their own audit logs" ON "public"."api_key_audit_logs"
    FOR SELECT
    TO authenticated
    USING (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));
