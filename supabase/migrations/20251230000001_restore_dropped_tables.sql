-- Migration: Restore tables dropped by 20251126165648_remote_schema.sql
-- Created: 2025-12-30
-- Description: Restores providers, models, stripe_webhook_events, and role_audit_log tables
-- These tables were dropped by the remote_schema migration but never restored

-- ============================================================================
-- PROVIDERS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."providers" (
    "id" SERIAL PRIMARY KEY,
    "name" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "description" TEXT,
    "base_url" TEXT,
    "api_key_env_var" TEXT,
    "logo_url" TEXT,
    "site_url" TEXT,
    "privacy_policy_url" TEXT,
    "terms_of_service_url" TEXT,
    "status_page_url" TEXT,
    "is_active" BOOLEAN DEFAULT true,
    "supports_streaming" BOOLEAN DEFAULT false,
    "supports_function_calling" BOOLEAN DEFAULT false,
    "supports_vision" BOOLEAN DEFAULT false,
    "supports_image_generation" BOOLEAN DEFAULT false,
    "average_response_time_ms" INTEGER,
    "health_status" TEXT DEFAULT 'unknown' CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    "last_health_check_at" TIMESTAMP WITH TIME ZONE,
    "metadata" JSONB DEFAULT '{}'::jsonb,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT now(),
    "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Add unique constraints if they don't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'providers_name_key') THEN
        ALTER TABLE "public"."providers" ADD CONSTRAINT "providers_name_key" UNIQUE ("name");
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'providers_slug_key') THEN
        ALTER TABLE "public"."providers" ADD CONSTRAINT "providers_slug_key" UNIQUE ("slug");
    END IF;
END $$;

COMMENT ON TABLE "public"."providers" IS 'AI model providers with health monitoring and capabilities';

CREATE INDEX IF NOT EXISTS "idx_providers_slug" ON "public"."providers" ("slug");
CREATE INDEX IF NOT EXISTS "idx_providers_is_active" ON "public"."providers" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_providers_health_status" ON "public"."providers" ("health_status");

-- ============================================================================
-- MODELS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."models" (
    "id" SERIAL PRIMARY KEY,
    "provider_id" INTEGER REFERENCES "public"."providers"("id") ON DELETE CASCADE,
    "model_id" TEXT NOT NULL,
    "model_name" TEXT NOT NULL,
    "provider_model_id" TEXT NOT NULL,
    "description" TEXT,
    "context_length" INTEGER,
    "modality" TEXT DEFAULT 'text->text',
    "architecture" TEXT,
    "top_provider" TEXT,
    "per_request_limits" JSONB,
    "pricing_prompt" NUMERIC(20, 10),
    "pricing_completion" NUMERIC(20, 10),
    "pricing_image" NUMERIC(20, 10),
    "pricing_request" NUMERIC(20, 10),
    "supports_streaming" BOOLEAN DEFAULT false,
    "supports_function_calling" BOOLEAN DEFAULT false,
    "supports_vision" BOOLEAN DEFAULT false,
    "average_response_time_ms" INTEGER,
    "health_status" TEXT DEFAULT 'unknown' CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    "last_health_check_at" TIMESTAMP WITH TIME ZONE,
    "success_rate" NUMERIC(5, 2),
    "is_active" BOOLEAN DEFAULT true,
    "metadata" JSONB DEFAULT '{}'::jsonb,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT now(),
    "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'unique_provider_model') THEN
        ALTER TABLE "public"."models" ADD CONSTRAINT "unique_provider_model" UNIQUE ("provider_id", "provider_model_id");
    END IF;
END $$;

COMMENT ON TABLE "public"."models" IS 'AI models with provider relationships, pricing, and health monitoring';

CREATE INDEX IF NOT EXISTS "idx_models_provider_id" ON "public"."models" ("provider_id");
CREATE INDEX IF NOT EXISTS "idx_models_model_id" ON "public"."models" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_models_provider_model_id" ON "public"."models" ("provider_model_id");
CREATE INDEX IF NOT EXISTS "idx_models_is_active" ON "public"."models" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_models_health_status" ON "public"."models" ("health_status");
CREATE INDEX IF NOT EXISTS "idx_models_modality" ON "public"."models" ("modality");
CREATE INDEX IF NOT EXISTS "idx_models_provider_active" ON "public"."models" ("provider_id", "is_active");

-- ============================================================================
-- STRIPE_WEBHOOK_EVENTS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."stripe_webhook_events" (
    "id" SERIAL PRIMARY KEY,
    "event_id" TEXT NOT NULL UNIQUE,
    "event_type" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "processed" BOOLEAN DEFAULT false,
    "processed_at" TIMESTAMP WITH TIME ZONE,
    "error_message" TEXT,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE "public"."stripe_webhook_events" IS 'Track Stripe webhook events for idempotency';

CREATE INDEX IF NOT EXISTS "idx_stripe_webhook_events_event_id" ON "public"."stripe_webhook_events" ("event_id");
CREATE INDEX IF NOT EXISTS "idx_stripe_webhook_events_event_type" ON "public"."stripe_webhook_events" ("event_type");
CREATE INDEX IF NOT EXISTS "idx_stripe_webhook_events_processed" ON "public"."stripe_webhook_events" ("processed");
CREATE INDEX IF NOT EXISTS "idx_stripe_webhook_events_created_at" ON "public"."stripe_webhook_events" ("created_at");

-- ============================================================================
-- ROLE_AUDIT_LOG TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."role_audit_log" (
    "id" SERIAL PRIMARY KEY,
    "user_id" BIGINT REFERENCES "public"."users"("id") ON DELETE CASCADE,
    "changed_by" BIGINT REFERENCES "public"."users"("id") ON DELETE SET NULL,
    "old_role" TEXT,
    "new_role" TEXT,
    "reason" TEXT,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE "public"."role_audit_log" IS 'Audit log for user role changes';

CREATE INDEX IF NOT EXISTS "idx_role_audit_log_user_id" ON "public"."role_audit_log" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_role_audit_log_changed_by" ON "public"."role_audit_log" ("changed_by");
CREATE INDEX IF NOT EXISTS "idx_role_audit_log_created_at" ON "public"."role_audit_log" ("created_at");

-- ============================================================================
-- UPDATE TRIGGERS
-- ============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_providers_updated_at ON "public"."providers";
CREATE TRIGGER update_providers_updated_at
    BEFORE UPDATE ON "public"."providers"
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_models_updated_at ON "public"."models";
CREATE TRIGGER update_models_updated_at
    BEFORE UPDATE ON "public"."models"
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- RLS POLICIES
-- ============================================================================
ALTER TABLE "public"."providers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."models" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."stripe_webhook_events" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."role_audit_log" ENABLE ROW LEVEL SECURITY;

-- Providers policies
DROP POLICY IF EXISTS "Allow public read access to providers" ON "public"."providers";
CREATE POLICY "Allow public read access to providers"
    ON "public"."providers"
    FOR SELECT
    USING (true);

DROP POLICY IF EXISTS "Allow service role full access to providers" ON "public"."providers";
CREATE POLICY "Allow service role full access to providers"
    ON "public"."providers"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Models policies
DROP POLICY IF EXISTS "Allow public read access to active models" ON "public"."models";
CREATE POLICY "Allow public read access to active models"
    ON "public"."models"
    FOR SELECT
    USING (is_active = true);

DROP POLICY IF EXISTS "Allow service role full access to models" ON "public"."models";
CREATE POLICY "Allow service role full access to models"
    ON "public"."models"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Stripe webhook events policies
DROP POLICY IF EXISTS "Service role can manage stripe webhooks" ON "public"."stripe_webhook_events";
CREATE POLICY "Service role can manage stripe webhooks"
    ON "public"."stripe_webhook_events"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Role audit log policies
DROP POLICY IF EXISTS "Service role can manage role audit log" ON "public"."role_audit_log";
CREATE POLICY "Service role can manage role audit log"
    ON "public"."role_audit_log"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can read role audit log" ON "public"."role_audit_log";
CREATE POLICY "Authenticated users can read role audit log"
    ON "public"."role_audit_log"
    FOR SELECT
    TO authenticated
    USING (true);

-- ============================================================================
-- GRANTS
-- ============================================================================
GRANT SELECT ON "public"."providers" TO anon;
GRANT SELECT ON "public"."providers" TO authenticated;
GRANT ALL ON "public"."providers" TO service_role;

GRANT SELECT ON "public"."models" TO anon;
GRANT SELECT ON "public"."models" TO authenticated;
GRANT ALL ON "public"."models" TO service_role;

GRANT ALL ON "public"."stripe_webhook_events" TO service_role;

GRANT SELECT ON "public"."role_audit_log" TO authenticated;
GRANT ALL ON "public"."role_audit_log" TO service_role;

-- Grant sequence usage
GRANT USAGE, SELECT ON SEQUENCE "public"."providers_id_seq" TO anon, authenticated, service_role;
GRANT USAGE, SELECT ON SEQUENCE "public"."models_id_seq" TO anon, authenticated, service_role;
GRANT USAGE, SELECT ON SEQUENCE "public"."stripe_webhook_events_id_seq" TO service_role;
GRANT USAGE, SELECT ON SEQUENCE "public"."role_audit_log_id_seq" TO authenticated, service_role;

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

DO $$
BEGIN
    RAISE NOTICE 'Successfully restored providers, models, stripe_webhook_events, and role_audit_log tables';
END $$;
