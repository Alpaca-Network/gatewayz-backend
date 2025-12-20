-- Migration: Ensure model catalog tables exist (re-apply 20251216024941)
-- Created: 2025-12-18
-- Description: This migration ensures providers, models, and model_catalog_health_history tables exist.
--              This is a failsafe migration since 20251216024941 may not have applied correctly.
--              All statements use IF NOT EXISTS / OR REPLACE for idempotency.

-- ============================================================================
-- PROVIDERS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."providers" (
    "id" SERIAL PRIMARY KEY,
    "name" TEXT NOT NULL UNIQUE,
    "slug" TEXT NOT NULL UNIQUE,
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

COMMENT ON TABLE "public"."providers" IS 'AI model providers - automatically populated by sync process';

CREATE INDEX IF NOT EXISTS "idx_providers_slug" ON "public"."providers" ("slug");
CREATE INDEX IF NOT EXISTS "idx_providers_is_active" ON "public"."providers" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_providers_health_status" ON "public"."providers" ("health_status");

-- ============================================================================
-- MODELS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."models" (
    "id" SERIAL PRIMARY KEY,
    "provider_id" INTEGER NOT NULL REFERENCES "public"."providers"("id") ON DELETE CASCADE,
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
    "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT now(),
    CONSTRAINT "unique_provider_model" UNIQUE ("provider_id", "provider_model_id")
);

COMMENT ON TABLE "public"."models" IS 'AI models - automatically synced from provider APIs';

CREATE INDEX IF NOT EXISTS "idx_models_provider_id" ON "public"."models" ("provider_id");
CREATE INDEX IF NOT EXISTS "idx_models_model_id" ON "public"."models" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_models_provider_model_id" ON "public"."models" ("provider_model_id");
CREATE INDEX IF NOT EXISTS "idx_models_is_active" ON "public"."models" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_models_health_status" ON "public"."models" ("health_status");
CREATE INDEX IF NOT EXISTS "idx_models_modality" ON "public"."models" ("modality");
CREATE INDEX IF NOT EXISTS "idx_models_provider_active" ON "public"."models" ("provider_id", "is_active");

-- ============================================================================
-- MODEL CATALOG HEALTH HISTORY TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS "public"."model_catalog_health_history" (
    "id" SERIAL PRIMARY KEY,
    "model_id" INTEGER NOT NULL REFERENCES "public"."models"("id") ON DELETE CASCADE,
    "health_status" TEXT NOT NULL CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    "response_time_ms" INTEGER,
    "error_message" TEXT,
    "checked_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE "public"."model_catalog_health_history" IS 'Historical health check data for catalog models';

CREATE INDEX IF NOT EXISTS "idx_model_catalog_health_history_model_id" ON "public"."model_catalog_health_history" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_model_catalog_health_history_checked_at" ON "public"."model_catalog_health_history" ("checked_at");

-- ============================================================================
-- FUNCTIONS
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
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================
ALTER TABLE "public"."providers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."models" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."model_catalog_health_history" ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow public read access to providers" ON "public"."providers";
DROP POLICY IF EXISTS "Allow authenticated read access to providers" ON "public"."providers";
DROP POLICY IF EXISTS "Allow service role full access to providers" ON "public"."providers";
DROP POLICY IF EXISTS "Allow public read access to active models" ON "public"."models";
DROP POLICY IF EXISTS "Allow authenticated read access to models" ON "public"."models";
DROP POLICY IF EXISTS "Allow service role full access to models" ON "public"."models";
DROP POLICY IF EXISTS "Allow authenticated read access to catalog health history" ON "public"."model_catalog_health_history";
DROP POLICY IF EXISTS "Allow service role full access to catalog health history" ON "public"."model_catalog_health_history";

CREATE POLICY "Allow public read access to providers"
    ON "public"."providers"
    FOR SELECT
    USING (true);

CREATE POLICY "Allow authenticated read access to providers"
    ON "public"."providers"
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Allow service role full access to providers"
    ON "public"."providers"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Allow public read access to active models"
    ON "public"."models"
    FOR SELECT
    USING (is_active = true);

CREATE POLICY "Allow authenticated read access to models"
    ON "public"."models"
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Allow service role full access to models"
    ON "public"."models"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Allow authenticated read access to catalog health history"
    ON "public"."model_catalog_health_history"
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Allow service role full access to catalog health history"
    ON "public"."model_catalog_health_history"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- GRANTS
-- ============================================================================
GRANT SELECT ON "public"."providers" TO anon;
GRANT SELECT ON "public"."models" TO anon;
GRANT SELECT ON "public"."providers" TO authenticated;
GRANT SELECT ON "public"."models" TO authenticated;
GRANT SELECT ON "public"."model_catalog_health_history" TO authenticated;
GRANT ALL ON "public"."providers" TO service_role;
GRANT ALL ON "public"."models" TO service_role;
GRANT ALL ON "public"."model_catalog_health_history" TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
