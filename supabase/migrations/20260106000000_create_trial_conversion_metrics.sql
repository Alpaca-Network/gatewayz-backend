-- Create trial_conversion_metrics table to track conversion data
CREATE TABLE IF NOT EXISTS "public"."trial_conversion_metrics" (
    "id" bigserial PRIMARY KEY,
    "user_id" bigint NOT NULL REFERENCES "public"."users"("id") ON DELETE CASCADE,
    "api_key_id" bigint NOT NULL REFERENCES "public"."api_keys_new"("id") ON DELETE CASCADE,
    "requests_at_conversion" integer NOT NULL DEFAULT 0,
    "tokens_at_conversion" bigint NOT NULL DEFAULT 0,
    "credits_used_at_conversion" numeric(10,2) NOT NULL DEFAULT 0.00,
    "trial_days_used" integer NOT NULL DEFAULT 0,
    "converted_plan" character varying(100),
    "conversion_trigger" character varying(50) DEFAULT 'manual_upgrade',
    "conversion_date" timestamp with time zone DEFAULT NOW(),
    "created_at" timestamp with time zone DEFAULT NOW(),
    "updated_at" timestamp with time zone DEFAULT NOW()
);

-- Add comments
COMMENT ON TABLE "public"."trial_conversion_metrics" IS 'Stores metrics at the moment of trial-to-paid conversion';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."user_id" IS 'User who converted';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."api_key_id" IS 'API key that was converted';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."requests_at_conversion" IS 'Number of requests made at time of conversion';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."tokens_at_conversion" IS 'Number of tokens used at time of conversion';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."credits_used_at_conversion" IS 'Credits used at time of conversion';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."trial_days_used" IS 'Number of days into trial when converted';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."converted_plan" IS 'Plan name user converted to';
COMMENT ON COLUMN "public"."trial_conversion_metrics"."conversion_trigger" IS 'What triggered the conversion (manual_upgrade, auto_upgrade, etc)';

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS "idx_trial_conversion_metrics_user_id"
    ON "public"."trial_conversion_metrics"("user_id");

CREATE INDEX IF NOT EXISTS "idx_trial_conversion_metrics_api_key_id"
    ON "public"."trial_conversion_metrics"("api_key_id");

CREATE INDEX IF NOT EXISTS "idx_trial_conversion_metrics_conversion_date"
    ON "public"."trial_conversion_metrics"("conversion_date" DESC);

CREATE INDEX IF NOT EXISTS "idx_trial_conversion_metrics_converted_plan"
    ON "public"."trial_conversion_metrics"("converted_plan");

-- Enable RLS (Row Level Security)
ALTER TABLE "public"."trial_conversion_metrics" ENABLE ROW LEVEL SECURITY;

-- Grant permissions
GRANT ALL ON TABLE "public"."trial_conversion_metrics" TO "service_role";
GRANT SELECT, INSERT ON TABLE "public"."trial_conversion_metrics" TO "authenticated";

-- Grant sequence permissions
GRANT USAGE, SELECT ON SEQUENCE "public"."trial_conversion_metrics_id_seq" TO "service_role";
GRANT USAGE, SELECT ON SEQUENCE "public"."trial_conversion_metrics_id_seq" TO "authenticated";
