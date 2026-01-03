-- Add rate_limit_config column to api_keys_new table
-- and create rate_limit_alerts table for monitoring

-- ============================================
-- ADD rate_limit_config COLUMN TO api_keys_new
-- ============================================

-- Add the rate_limit_config column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'api_keys_new'
        AND column_name = 'rate_limit_config'
    ) THEN
        ALTER TABLE "public"."api_keys_new"
        ADD COLUMN "rate_limit_config" jsonb DEFAULT NULL;

        RAISE NOTICE 'Added rate_limit_config column to api_keys_new table';
    ELSE
        RAISE NOTICE 'rate_limit_config column already exists in api_keys_new table';
    END IF;
END $$;

-- Create index for faster lookups on rate_limit_config
CREATE INDEX IF NOT EXISTS "api_keys_new_rate_limit_config_idx"
ON "public"."api_keys_new" USING gin ("rate_limit_config")
WHERE "rate_limit_config" IS NOT NULL;

-- ============================================
-- CREATE rate_limit_alerts TABLE
-- ============================================

-- Create sequence first (must exist before table creation)
CREATE SEQUENCE IF NOT EXISTS "public"."rate_limit_alerts_id_seq";

-- Create rate_limit_alerts table
-- Stores rate limit alerts for monitoring and alerting
CREATE TABLE IF NOT EXISTS "public"."rate_limit_alerts" (
    "id" bigint NOT NULL DEFAULT nextval('rate_limit_alerts_id_seq'::regclass),
    "api_key" text NOT NULL,
    "alert_type" text NOT NULL,
    "details" jsonb DEFAULT '{}'::jsonb,
    "resolved" boolean DEFAULT false,
    "resolved_at" timestamp with time zone DEFAULT NULL,
    "created_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    CONSTRAINT "rate_limit_alerts_pkey" PRIMARY KEY ("id")
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_api_key_idx" ON "public"."rate_limit_alerts" USING btree ("api_key");
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_alert_type_idx" ON "public"."rate_limit_alerts" USING btree ("alert_type");
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_resolved_idx" ON "public"."rate_limit_alerts" USING btree ("resolved");
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_created_at_idx" ON "public"."rate_limit_alerts" USING btree ("created_at");

-- Set sequence ownership
ALTER SEQUENCE "public"."rate_limit_alerts_id_seq" OWNED BY "public"."rate_limit_alerts"."id";

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON "public"."rate_limit_alerts" TO "authenticated";
GRANT ALL ON "public"."rate_limit_alerts" TO "service_role";

-- Add RLS policies for security
ALTER TABLE "public"."rate_limit_alerts" ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS "Service role can manage rate limit alerts" ON "public"."rate_limit_alerts";

CREATE POLICY "Service role can manage rate limit alerts" ON "public"."rate_limit_alerts"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
