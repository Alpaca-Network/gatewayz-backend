-- Migration: Create model_aliases table
-- Stores user-convenience aliases mapping simplified model IDs to canonical IDs.
-- Replaces the MODEL_ID_ALIASES dict in src/services/model_transformations.py.

CREATE TABLE IF NOT EXISTS model_aliases (
    id BIGSERIAL PRIMARY KEY,
    alias VARCHAR(255) NOT NULL,
    canonical_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_model_alias UNIQUE (alias)
);

CREATE INDEX IF NOT EXISTS idx_model_aliases_alias ON model_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_model_aliases_canonical_id ON model_aliases(canonical_id);

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_model_aliases_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_model_aliases_updated_at_trigger
    BEFORE UPDATE ON model_aliases
    FOR EACH ROW
    EXECUTE FUNCTION update_model_aliases_updated_at();
