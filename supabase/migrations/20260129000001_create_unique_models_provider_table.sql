-- Migration: Create unique_models_provider mapping table
-- Description: Links unique models to providers with reference to actual model records
-- Auto-updates via triggers on INSERT/UPDATE/DELETE in models table

-- ============================================================================
-- Step 1: Create the unique_models_provider mapping table
-- ============================================================================

CREATE TABLE IF NOT EXISTS unique_models_provider (
    id BIGSERIAL PRIMARY KEY,
    unique_model_id BIGINT NOT NULL,
    provider_id BIGINT NOT NULL,
    model_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Foreign key constraints
    CONSTRAINT fk_unique_model
        FOREIGN KEY (unique_model_id)
        REFERENCES unique_models(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_model
        FOREIGN KEY (model_id)
        REFERENCES models(id)
        ON DELETE CASCADE,

    -- Ensure one entry per provider per unique model per actual model
    CONSTRAINT unique_model_provider_mapping
        UNIQUE (unique_model_id, provider_id, model_id)
);

-- Add indexes for performance
CREATE INDEX idx_unique_models_provider_unique_model
    ON unique_models_provider(unique_model_id);

CREATE INDEX idx_unique_models_provider_provider
    ON unique_models_provider(provider_id);

CREATE INDEX idx_unique_models_provider_model
    ON unique_models_provider(model_id);

CREATE INDEX idx_unique_models_provider_lookup
    ON unique_models_provider(unique_model_id, provider_id);

-- Add comments for documentation
COMMENT ON TABLE unique_models_provider IS 'Maps unique models to providers with references to actual model records from models table';
COMMENT ON COLUMN unique_models_provider.unique_model_id IS 'Reference to unique_models.id';
COMMENT ON COLUMN unique_models_provider.provider_id IS 'Provider ID offering this model';
COMMENT ON COLUMN unique_models_provider.model_id IS 'Reference to actual models.id record';

-- ============================================================================
-- Step 2: Create trigger function to maintain unique_models_provider
-- ============================================================================

CREATE OR REPLACE FUNCTION sync_unique_models_provider()
RETURNS TRIGGER AS $$
DECLARE
    v_unique_model_id BIGINT;
BEGIN
    -- Handle INSERT
    IF (TG_OP = 'INSERT') THEN
        -- Find the unique_model_id for this model_name
        SELECT id INTO v_unique_model_id
        FROM unique_models
        WHERE model_name = NEW.model_name;

        -- If unique model exists, create the mapping
        IF v_unique_model_id IS NOT NULL THEN
            INSERT INTO unique_models_provider (
                unique_model_id,
                provider_id,
                model_id
            )
            VALUES (
                v_unique_model_id,
                NEW.provider_id,
                NEW.id
            )
            ON CONFLICT (unique_model_id, provider_id, model_id) DO NOTHING;
        END IF;
    END IF;

    -- Handle UPDATE (when model_name or provider_id changes)
    IF (TG_OP = 'UPDATE') THEN
        -- Delete old mapping if model_name or provider_id changed
        IF (OLD.model_name IS DISTINCT FROM NEW.model_name OR
            OLD.provider_id IS DISTINCT FROM NEW.provider_id) THEN

            DELETE FROM unique_models_provider
            WHERE model_id = OLD.id;
        END IF;

        -- Add new mapping
        SELECT id INTO v_unique_model_id
        FROM unique_models
        WHERE model_name = NEW.model_name;

        IF v_unique_model_id IS NOT NULL THEN
            INSERT INTO unique_models_provider (
                unique_model_id,
                provider_id,
                model_id
            )
            VALUES (
                v_unique_model_id,
                NEW.provider_id,
                NEW.id
            )
            ON CONFLICT (unique_model_id, provider_id, model_id)
            DO UPDATE SET updated_at = NOW();
        END IF;
    END IF;

    -- Handle DELETE
    IF (TG_OP = 'DELETE') THEN
        -- Remove the mapping (CASCADE will handle this, but being explicit)
        DELETE FROM unique_models_provider
        WHERE model_id = OLD.id;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Step 3: Create triggers on models table
-- ============================================================================

-- Drop existing triggers if they exist (for re-running migration)
DROP TRIGGER IF EXISTS trg_sync_unique_models_provider_insert ON models;
DROP TRIGGER IF EXISTS trg_sync_unique_models_provider_update ON models;
DROP TRIGGER IF EXISTS trg_sync_unique_models_provider_delete ON models;

-- Create INSERT trigger
CREATE TRIGGER trg_sync_unique_models_provider_insert
    AFTER INSERT ON models
    FOR EACH ROW
    EXECUTE FUNCTION sync_unique_models_provider();

-- Create UPDATE trigger (fires when model_name or provider_id changes)
CREATE TRIGGER trg_sync_unique_models_provider_update
    AFTER UPDATE ON models
    FOR EACH ROW
    WHEN (OLD.model_name IS DISTINCT FROM NEW.model_name OR
          OLD.provider_id IS DISTINCT FROM NEW.provider_id)
    EXECUTE FUNCTION sync_unique_models_provider();

-- Create DELETE trigger
CREATE TRIGGER trg_sync_unique_models_provider_delete
    AFTER DELETE ON models
    FOR EACH ROW
    EXECUTE FUNCTION sync_unique_models_provider();

-- ============================================================================
-- Step 4: Initial population from existing data
-- ============================================================================

INSERT INTO unique_models_provider (unique_model_id, provider_id, model_id)
SELECT
    um.id as unique_model_id,
    m.provider_id,
    m.id as model_id
FROM models m
INNER JOIN unique_models um ON um.model_name = m.model_name
WHERE m.model_name IS NOT NULL AND m.model_name != ''
ON CONFLICT (unique_model_id, provider_id, model_id) DO NOTHING;

-- ============================================================================
-- Step 5: Create helper views and functions
-- ============================================================================

-- View to see unique models with their providers
CREATE OR REPLACE VIEW unique_models_provider_summary AS
SELECT
    um.id as unique_model_id,
    um.model_name,
    ump.provider_id,
    ump.model_id,
    m.model_id as full_model_id,
    m.provider_model_id,
    COUNT(*) OVER (PARTITION BY um.id) as provider_count_for_model
FROM unique_models um
INNER JOIN unique_models_provider ump ON ump.unique_model_id = um.id
INNER JOIN models m ON m.id = ump.model_id
ORDER BY um.model_name, ump.provider_id;

COMMENT ON VIEW unique_models_provider_summary IS 'Shows unique models with all their provider mappings';

-- View to count providers per unique model
CREATE OR REPLACE VIEW unique_models_provider_count AS
SELECT
    um.id as unique_model_id,
    um.model_name,
    COUNT(DISTINCT ump.provider_id) as provider_count,
    ARRAY_AGG(DISTINCT ump.provider_id ORDER BY ump.provider_id) as provider_ids,
    COUNT(*) as total_model_entries
FROM unique_models um
LEFT JOIN unique_models_provider ump ON ump.unique_model_id = um.id
GROUP BY um.id, um.model_name
ORDER BY provider_count DESC, um.model_name;

COMMENT ON VIEW unique_models_provider_count IS 'Counts how many providers offer each unique model';

-- Function to get all models for a specific unique model
CREATE OR REPLACE FUNCTION get_models_for_unique_model(p_unique_model_id BIGINT)
RETURNS TABLE (
    provider_id BIGINT,
    model_id BIGINT,
    full_model_id TEXT,
    provider_model_id TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ump.provider_id,
        ump.model_id,
        m.model_id as full_model_id,
        m.provider_model_id
    FROM unique_models_provider ump
    INNER JOIN models m ON m.id = ump.model_id
    WHERE ump.unique_model_id = p_unique_model_id
    ORDER BY ump.provider_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_models_for_unique_model IS 'Get all provider models for a specific unique model';

-- Function to get all models for a unique model by name
CREATE OR REPLACE FUNCTION get_models_for_unique_model_name(p_model_name TEXT)
RETURNS TABLE (
    provider_id BIGINT,
    model_id BIGINT,
    full_model_id TEXT,
    provider_model_id TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ump.provider_id,
        ump.model_id,
        m.model_id as full_model_id,
        m.provider_model_id
    FROM unique_models um
    INNER JOIN unique_models_provider ump ON ump.unique_model_id = um.id
    INNER JOIN models m ON m.id = ump.model_id
    WHERE um.model_name = p_model_name
    ORDER BY ump.provider_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_models_for_unique_model_name IS 'Get all provider models for a unique model by name';

-- ============================================================================
-- Verification queries (commented out for production)
-- ============================================================================

-- SELECT COUNT(*) as total_mappings FROM unique_models_provider;
-- SELECT * FROM unique_models_provider_count LIMIT 10;
-- SELECT * FROM unique_models_provider_summary LIMIT 20;
-- SELECT * FROM get_models_for_unique_model_name('GPT-4');
