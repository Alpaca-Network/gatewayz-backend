-- Migration: Create model_routing_rules table
-- Stores explicit model-to-provider routing overrides.
-- Replaces the MODEL_PROVIDER_OVERRIDES dict in src/services/model_transformations.py.

CREATE TABLE IF NOT EXISTS model_routing_rules (
    id BIGSERIAL PRIMARY KEY,
    model_pattern VARCHAR(255) NOT NULL,
    force_provider VARCHAR(100) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_model_routing_pattern UNIQUE (model_pattern)
);

CREATE INDEX IF NOT EXISTS idx_model_routing_rules_pattern ON model_routing_rules(model_pattern);
CREATE INDEX IF NOT EXISTS idx_model_routing_rules_priority ON model_routing_rules(priority DESC);

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_model_routing_rules_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_model_routing_rules_updated_at_trigger
    BEFORE UPDATE ON model_routing_rules
    FOR EACH ROW
    EXECUTE FUNCTION update_model_routing_rules_updated_at();
