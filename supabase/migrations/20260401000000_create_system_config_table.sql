-- System Configuration Table
-- Stores system-wide configuration values (default models, fallback chains, feature flags)
-- that were previously hardcoded in Python source files.

CREATE TABLE IF NOT EXISTS "public"."system_config" (
    "id" BIGSERIAL PRIMARY KEY,
    "key" VARCHAR(255) UNIQUE NOT NULL,
    "value" JSONB NOT NULL,
    "description" TEXT,
    "is_active" BOOLEAN DEFAULT true,
    "created_at" TIMESTAMPTZ DEFAULT NOW(),
    "updated_at" TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "idx_system_config_key" ON "public"."system_config" ("key");
CREATE INDEX IF NOT EXISTS "idx_system_config_is_active" ON "public"."system_config" ("is_active");

COMMENT ON TABLE "public"."system_config" IS 'System-wide configuration values (default models, fallback chains, feature flags). Replaces hardcoded constants.';

-- Auto-update updated_at on row modification
CREATE OR REPLACE FUNCTION update_system_config_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_system_config_updated_at
    BEFORE UPDATE ON "public"."system_config"
    FOR EACH ROW
    EXECUTE FUNCTION update_system_config_updated_at();

-- Seed initial config values (current hardcoded defaults)
INSERT INTO "public"."system_config" ("key", "value", "description") VALUES
    ('auto_route_default_model', '"openai/gpt-4o-mini"', 'Default model for auto-routing when no preference specified'),
    ('code_router_default_model', '"zai/glm-4.7"', 'Default model for code router fallback'),
    ('general_router_fallback_quality', '"openai/gpt-4o"', 'Fallback model for quality-optimized routing'),
    ('general_router_fallback_cost', '"openai/gpt-4o-mini"', 'Fallback model for cost-optimized routing'),
    ('general_router_fallback_latency', '"groq/llama-3.3-70b-versatile"', 'Fallback model for latency-optimized routing'),
    ('general_router_fallback_balanced', '"anthropic/claude-sonnet-4"', 'Fallback model for balanced routing'),
    ('default_fallback_model', '"anthropic/claude-sonnet-4"', 'System-wide default fallback model'),
    ('code_router_fallback_model', '"anthropic/claude-sonnet-4"', 'Fallback model when code router is disabled'),
    ('model_fallback_mappings', '{"gpt-4":["gpt-4-turbo","gpt-3.5-turbo","claude-3-opus","claude-3-sonnet"],"gpt-4-turbo":["gpt-4","gpt-3.5-turbo","claude-3-opus"],"gpt-3.5-turbo":["gpt-4","gpt-4-turbo","claude-3-sonnet"],"claude-3-opus":["gpt-4","claude-3-sonnet","gpt-4-turbo"],"claude-3-sonnet":["claude-3-opus","gpt-3.5-turbo","gpt-4"],"llama-3-70b":["llama-3-8b","claude-3-sonnet","gpt-3.5-turbo"],"llama-3-8b":["llama-3-70b","gpt-3.5-turbo","claude-3-sonnet"]}', 'Model fallback chains for availability failover')
ON CONFLICT ("key") DO NOTHING;
