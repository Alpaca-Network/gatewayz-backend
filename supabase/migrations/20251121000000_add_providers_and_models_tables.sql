-- Migration: Add providers and models tables for comprehensive model catalog management
-- Created: 2025-11-21
-- Description: Creates tables to store AI providers and their models with health monitoring

-- ============================================================================
-- PROVIDERS TABLE
-- ============================================================================
-- Stores all AI model providers (OpenRouter, Portkey, Featherless, etc.)
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
COMMENT ON TABLE "public"."providers" IS 'AI model providers with health monitoring and capabilities';

-- Create indexes for providers
CREATE INDEX IF NOT EXISTS "idx_providers_slug" ON "public"."providers" ("slug");
CREATE INDEX IF NOT EXISTS "idx_providers_is_active" ON "public"."providers" ("is_active");
CREATE INDEX IF NOT EXISTS "idx_providers_health_status" ON "public"."providers" ("health_status");

-- ============================================================================
-- MODELS TABLE
-- ============================================================================
-- Stores all AI models with provider relationships and pricing
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

    -- Pricing information
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

    -- Ensure unique model per provider
    CONSTRAINT "unique_provider_model" UNIQUE ("provider_id", "provider_model_id")
);

-- Add comment to models table
COMMENT ON TABLE "public"."models" IS 'AI models with provider relationships, pricing, and health monitoring';

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
-- MODEL HEALTH HISTORY TABLE
-- ============================================================================
-- Stores historical health check data for models
CREATE TABLE IF NOT EXISTS "public"."model_health_history" (
    "id" SERIAL PRIMARY KEY,
    "model_id" INTEGER NOT NULL REFERENCES "public"."models"("id") ON DELETE CASCADE,
    "health_status" TEXT NOT NULL CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    "response_time_ms" INTEGER,
    "error_message" TEXT,
    "checked_at" TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Add comment to model_health_history table
COMMENT ON TABLE "public"."model_health_history" IS 'Historical health check data for models';

-- Create indexes for model_health_history
CREATE INDEX IF NOT EXISTS "idx_model_health_history_model_id" ON "public"."model_health_history" ("model_id");
CREATE INDEX IF NOT EXISTS "idx_model_health_history_checked_at" ON "public"."model_health_history" ("checked_at");

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
-- INITIAL DATA - Insert the 17 existing providers
-- ============================================================================

INSERT INTO "public"."providers" (
    "name",
    "slug",
    "description",
    "api_key_env_var",
    "supports_streaming",
    "is_active"
) VALUES
    ('OpenRouter', 'openrouter', 'Multi-provider AI model router', 'OPENROUTER_API_KEY', true, true),
    ('Portkey', 'portkey', 'AI gateway and model router', 'PORTKEY_API_KEY', true, true),
    ('Featherless', 'featherless', 'Featherless AI provider', 'FEATHERLESS_API_KEY', true, true),
    ('Chutes', 'chutes', 'Chutes AI infrastructure', 'CHUTES_API_KEY', true, true),
    ('DeepInfra', 'deepinfra', 'Deep learning infrastructure', 'DEEPINFRA_API_KEY', true, true),
    ('Fireworks AI', 'fireworks', 'Fast AI model inference', 'FIREWORKS_API_KEY', true, true),
    ('Together AI', 'together', 'Together AI platform', 'TOGETHER_API_KEY', true, true),
    ('HuggingFace', 'huggingface', 'HuggingFace inference API', 'HUGGINGFACE_API_KEY', true, true),
    ('XAI', 'xai', 'X.AI (Grok) provider', 'XAI_API_KEY', true, true),
    ('AIMO', 'aimo', 'AIMO AI provider', 'AIMO_API_KEY', true, true),
    ('Near AI', 'near', 'Near AI infrastructure', 'NEAR_API_KEY', true, true),
    ('Fal.ai', 'fal', 'Fal.ai image generation', 'FAL_API_KEY', false, true),
    ('Anannas', 'anannas', 'Anannas AI provider', 'ANANNAS_API_KEY', true, true),
    ('Google Vertex AI', 'google-vertex', 'Google Cloud Vertex AI', 'GOOGLE_APPLICATION_CREDENTIALS', true, true),
    ('Modelz', 'modelz', 'Modelz inference platform', 'MODELZ_API_KEY', true, true),
    ('AiHubMix', 'aihubmix', 'AiHubMix model provider', 'AIHUBMIX_API_KEY', true, true),
    ('Vercel AI Gateway', 'vercel-ai-gateway', 'Vercel AI Gateway', 'VERCEL_AI_GATEWAY_KEY', true, true)
ON CONFLICT (slug) DO NOTHING;

-- ============================================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE "public"."providers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."models" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "public"."model_health_history" ENABLE ROW LEVEL SECURITY;

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

-- Policies for model_health_history table
-- Allow authenticated users to read health history
CREATE POLICY "Allow authenticated read access to health history"
    ON "public"."model_health_history"
    FOR SELECT
    TO authenticated
    USING (true);

-- Allow service role full access to health history
CREATE POLICY "Allow service role full access to health history"
    ON "public"."model_health_history"
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
GRANT SELECT ON "public"."model_health_history" TO authenticated;

-- Grant all permissions to service role
GRANT ALL ON "public"."providers" TO service_role;
GRANT ALL ON "public"."models" TO service_role;
GRANT ALL ON "public"."model_health_history" TO service_role;

-- Grant sequence usage
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
