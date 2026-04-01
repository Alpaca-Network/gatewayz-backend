-- Migration: Create model_provider_mappings table
-- Stores provider-specific model ID transformations (simplified -> native format).
-- Replaces the _MODEL_ID_MAPPINGS nested dict in src/services/model_transformations.py.

CREATE TABLE IF NOT EXISTS model_provider_mappings (
    id BIGSERIAL PRIMARY KEY,
    model_id VARCHAR(255) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    provider_model_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_model_provider UNIQUE (model_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_model_provider_mappings_model_id ON model_provider_mappings(model_id);
CREATE INDEX IF NOT EXISTS idx_model_provider_mappings_provider ON model_provider_mappings(provider);
CREATE INDEX IF NOT EXISTS idx_model_provider_mappings_provider_model_id ON model_provider_mappings(provider, provider_model_id);

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_model_provider_mappings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_model_provider_mappings_updated_at_trigger
    BEFORE UPDATE ON model_provider_mappings
    FOR EACH ROW
    EXECUTE FUNCTION update_model_provider_mappings_updated_at();
