-- Create rate_limit_alerts table to back admin alerts UI
CREATE TABLE IF NOT EXISTS "public"."rate_limit_alerts" (
    "id" bigint NOT NULL DEFAULT nextval('rate_limit_alerts_id_seq'::regclass),
    "api_key" text NOT NULL,
    "alert_type" text NOT NULL,
    "severity" text DEFAULT 'medium'::text,
    "details" jsonb DEFAULT '{}'::jsonb,
    "resolved" boolean DEFAULT false,
    "resolved_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    "updated_at" timestamp with time zone DEFAULT (now() AT TIME ZONE 'UTC'::text),
    CONSTRAINT "rate_limit_alerts_pkey" PRIMARY KEY ("id")
);

-- Sequences
CREATE SEQUENCE IF NOT EXISTS "public"."rate_limit_alerts_id_seq" OWNED BY "public"."rate_limit_alerts"."id";

-- Helpful indexes
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_api_key_idx" ON "public"."rate_limit_alerts" USING btree ("api_key");
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_resolved_idx" ON "public"."rate_limit_alerts" USING btree ("resolved");
CREATE INDEX IF NOT EXISTS "rate_limit_alerts_created_at_idx" ON "public"."rate_limit_alerts" USING btree ("created_at");

-- Row Level Security & policies
ALTER TABLE "public"."rate_limit_alerts" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role can manage rate limit alerts"
    ON "public"."rate_limit_alerts"
    FOR ALL
    USING (auth.role() = 'service_role');

-- Permissions
GRANT ALL ON "public"."rate_limit_alerts" TO "service_role";
