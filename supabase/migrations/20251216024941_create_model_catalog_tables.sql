-- Migration: Create model catalog tables for automatic provider and model sync
-- Created: 2025-12-16
-- Description: Creates tables for AI providers and models WITHOUT hardcoded data.
--              Providers and models will be automatically discovered and added by the sync process.
--              This allows the sync script to be the single source of truth for provider metadata.

-- ============================================================================
-- PROVIDERS TABLE
-- ============================================================================
-- Stores all AI model providers (OpenRouter, Groq, DeepInfra, etc.)
-- Providers are automatically created by the sync process via ensure_provider_exists()
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

-- Add comment to providers table
COMMENT ON TABLE "public"."providers" IS 'AI model providers - automatically populated by sync process';

-- Create indexes for providers
CREATE INDEX IF NOT EXISTS "idx_providers_slug" ON "public"."providers" ("slug");
CREATE INDEX IF NOT EXISTS "idx_providers_is_active" ON "public"."providers" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_providers_health_status" ON "public"."providers" ("health_status");

-- ============================================================================
-- MODELS TABLE
-- ============================================================================
-- Stores all AI models with provider relationships and pricing
-- Models are automatically synced from provider APIs
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

    -- Pricing information (per token costs)
    "pricing_prompt" NUMERIC(20, 10),
    "pricing_completion" NUMERIC(20, 10),
    "pricing_image" NUMERIC(20, 10),
    "pricing_request" NUMERIC(20, 10),

    -- Capabilities
    "supports_streaming" BOOLEAN DEFAULT false,
    "supports_function_calling" BOOLEAN DEFAULT false,
    "supports_vision" BOOLEAN DEFAULT false,

    -- Performance monitoring
    "average_response_time_ms" INTEGER,
    "health_status" TEXT DEFAULT 'unknown' CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    "last_health_check_at" TIMESTAMP WITH TIME ZONE,
    "success_rate" NUMERIC(5, 2),

    -- Metadata
    "is_active" BOOLEAN DEFAULT true,
    "metadata" JSONB DEFAULT '{}'::jsonb,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT now(),
    "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT now(),

    -- Ensure unique model per provider (for upsert operations)
    CONSTRAINT "unique_provider_model" UNIQUE ("provider_id", "provider_model_id")
);

-- Add comment to models table
COMMENT ON TABLE "public"."models" IS 'AI models - automatically synced from provider APIs';

-- Create indexes for models
CREATE INDEX IF NOT EXISTS "idx_models_provider_id" ON "public"."models" ("provider_id");
CREATE INDEX IF NOT EXISTS "idx_models_model_id" ON "public"."models" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_models_provider_model_id" ON "public"."models" ("provider_model_id");
CREATE INDEX IF NOT EXISTS "idx_models_is_active" ON "public"."models" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_models_health_status" ON "public"."models" ("health_status");
CREATE INDEX IF NOT EXISTS "idx_models_modality" ON "public"."models" ("modality");

-- Create composite index for common queries
CREATE INDEX IF NOT EXISTS "idx_models_provider_active" ON "public"."models" ("provider_id", "is_active");

-- ============================================================================
-- MODEL CATALOG HEALTH HISTORY TABLE
-- ============================================================================
-- Stores historical health check data for catalog models
-- Note: This is separate from the runtime model_health_history table used for monitoring
CREATE TABLE IF NOT EXISTS "public"."model_catalog_health_history" (
    "id" SERIAL PRIMARY KEY,
    "model_id" INTEGER NOT NULL REFERENCES "public"."models"("id") ON DELETE CASCADE,
    "health_status" TEXT NOT NULL CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    "response_time_ms" INTEGER,
    "error_message" TEXT,
    "checked_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Add comment to model_catalog_health_history table
COMMENT ON TABLE "public"."model_catalog_health_history" IS 'Historical health check data for catalog models';

-- Create indexes for model_catalog_health_history
CREATE INDEX IF NOT EXISTS "idx_model_catalog_health_history_model_id" ON "public"."model_catalog_health_history" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_model_catalog_health_history_checked_at" ON "public"."model_catalog_health_history" ("checked_at");

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
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

-- Enable RLS on all tables
ALTER TABLE "public"."providers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."models" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."model_catalog_health_history" ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS "Allow public read access to providers" ON "public"."providers";
DROP POLICY IF EXISTS "Allow authenticated read access to providers" ON "public"."providers";
DROP POLICY IF EXISTS "Allow service role full access to providers" ON "public"."providers";
DROP POLICY IF EXISTS "Allow public read access to active models" ON "public"."models";
DROP POLICY IF EXISTS "Allow authenticated read access to models" ON "public"."models";
DROP POLICY IF EXISTS "Allow service role full access to models" ON "public"."models";
DROP POLICY IF EXISTS "Allow authenticated read access to catalog health history" ON "public"."model_catalog_health_history";
DROP POLICY IF EXISTS "Allow service role full access to catalog health history" ON "public"."model_catalog_health_history";

-- Policies for providers table
-- Allow public read access to providers
CREATE POLICY "Allow public read access to providers"
    ON "public"."providers"
    FOR SELECT
    USING (true);

-- Allow authenticated users to read providers
CREATE POLICY "Allow authenticated read access to providers"
    ON "public"."providers"
    FOR SELECT
    TO authenticated
    USING (true);

-- Allow service role full access to providers
CREATE POLICY "Allow service role full access to providers"
    ON "public"."providers"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policies for models table
-- Allow public read access to active models
CREATE POLICY "Allow public read access to active models"
    ON "public"."models"
    FOR SELECT
    USING (is_active = true);

-- Allow authenticated users to read all models
CREATE POLICY "Allow authenticated read access to models"
    ON "public"."models"
    FOR SELECT
    TO authenticated
    USING (true);

-- Allow service role full access to models
CREATE POLICY "Allow service role full access to models"
    ON "public"."models"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policies for model_catalog_health_history table
-- Allow authenticated users to read catalog health history
CREATE POLICY "Allow authenticated read access to catalog health history"
    ON "public"."model_catalog_health_history"
    FOR SELECT
    TO authenticated
    USING (true);

-- Allow service role full access to catalog health history
CREATE POLICY "Allow service role full access to catalog health history"
    ON "public"."model_catalog_health_history"
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- GRANTS
-- ============================================================================

-- Grant permissions to anon users (public read)
GRANT SELECT ON "public"."providers" TO anon;
GRANT SELECT ON "public"."models" TO anon;

-- Grant permissions to authenticated users
GRANT SELECT ON "public"."providers" TO authenticated;
GRANT SELECT ON "public"."models" TO authenticated;
GRANT SELECT ON "public"."model_catalog_health_history" TO authenticated;

-- Grant all permissions to service role
GRANT ALL ON "public"."providers" TO service_role;
GRANT ALL ON "public"."models" TO service_role;
GRANT ALL ON "public"."model_catalog_health_history" TO service_role;

-- Grant sequence usage
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- ============================================================================
-- NOTES
-- ============================================================================
-- This migration creates the table structure only.
-- NO providers or models are hardcoded.
--
-- Providers and models will be automatically added when you run:
--   python3 scripts/sync_models.py
--
-- The sync script (src/services/model_catalog_sync.py) will:
--   1. Call ensure_provider_exists() for each provider
--   2. Create provider records with metadata from the sync script
--   3. Fetch models from each provider's API
--   4. Transform and upsert models into the database
--
-- This approach provides a single source of truth for provider metadata
-- in the sync script, making it easier to add new providers without
-- requiring database migrations.
