-- Provider Pricing Standards Database Schema
-- Optional: Store pricing standards in database instead of JSON file

-- Main providers table with pricing format information
CREATE TABLE provider_pricing_standards (
    id SERIAL PRIMARY KEY,
    provider_slug VARCHAR(50) UNIQUE NOT NULL,
    provider_name VARCHAR(100) NOT NULL,
    pricing_unit VARCHAR(50) NOT NULL,  -- 'per_token', 'per_1K_tokens', 'per_1M_tokens', 'scientific_notation', 'subscription'
    api_format VARCHAR(50) NOT NULL,    -- Format returned by provider API
    conversion_factor DECIMAL(20, 10),  -- Multiplier to convert to per-token (NULL for dynamic)
    pricing_model VARCHAR(50),          -- 'usage_based', 'subscription', 'flat_rate'
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Supported modalities per provider
CREATE TABLE provider_modalities (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER REFERENCES provider_pricing_standards(id) ON DELETE CASCADE,
    modality VARCHAR(50) NOT NULL,      -- 'text->text', 'text->image', 'audio->text', etc.
    pricing_fields JSONB NOT NULL,      -- ["prompt", "completion"] or ["image"] or ["request"]
    unit_description TEXT,              -- Human-readable unit description
    calculation_formula TEXT,           -- Formula for calculating cost
    field_mapping JSONB,                -- Field name mappings (e.g., {"input": "prompt", "output": "completion"})
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(provider_id, modality)
);

-- Special features per provider
CREATE TABLE provider_special_features (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER REFERENCES provider_pricing_standards(id) ON DELETE CASCADE,
    feature_key VARCHAR(100) NOT NULL,  -- 'free_models', 'dynamic_pricing', 'tiered_pricing'
    feature_value JSONB NOT NULL,       -- Feature configuration as JSON
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(provider_id, feature_key)
);

-- Pricing history for audit and analysis
CREATE TABLE provider_pricing_history (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER REFERENCES provider_pricing_standards(id) ON DELETE CASCADE,
    model_id VARCHAR(200),              -- Optional: specific model if price change is model-specific
    change_type VARCHAR(50) NOT NULL,   -- 'format_change', 'price_increase', 'price_decrease', 'new_modality'
    old_value JSONB,                    -- Previous pricing format/value
    new_value JSONB,                    -- New pricing format/value
    changed_by VARCHAR(100),            -- Who made the change (system, admin name, etc.)
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for better query performance
CREATE INDEX idx_provider_slug ON provider_pricing_standards(provider_slug);
CREATE INDEX idx_provider_active ON provider_pricing_standards(is_active);
CREATE INDEX idx_provider_modalities_lookup ON provider_modalities(provider_id, modality);
CREATE INDEX idx_pricing_history_provider ON provider_pricing_history(provider_id);
CREATE INDEX idx_pricing_history_created ON provider_pricing_history(created_at DESC);

-- Insert example data for OpenRouter
INSERT INTO provider_pricing_standards (provider_slug, provider_name, pricing_unit, api_format, conversion_factor, pricing_model)
VALUES ('openrouter', 'OpenRouter', 'per_token', 'per_token', 1.0, 'usage_based');

INSERT INTO provider_modalities (provider_id, modality, pricing_fields, unit_description, calculation_formula)
VALUES (
    (SELECT id FROM provider_pricing_standards WHERE provider_slug = 'openrouter'),
    'text->text',
    '["prompt", "completion"]'::jsonb,
    'USD per token',
    'prompt_tokens * prompt_price + completion_tokens * completion_price'
);

INSERT INTO provider_special_features (provider_id, feature_key, feature_value, description)
VALUES (
    (SELECT id FROM provider_pricing_standards WHERE provider_slug = 'openrouter'),
    'free_models',
    '{"enabled": true, "suffix": ":free"}'::jsonb,
    'OpenRouter offers free models with :free suffix'
);

-- Insert example data for DeepInfra
INSERT INTO provider_pricing_standards (provider_slug, provider_name, pricing_unit, api_format, conversion_factor, pricing_model)
VALUES ('deepinfra', 'DeepInfra', 'per_1M_tokens', 'per_1M_tokens', 0.000001, 'usage_based');

INSERT INTO provider_modalities (provider_id, modality, pricing_fields, unit_description, calculation_formula)
VALUES (
    (SELECT id FROM provider_pricing_standards WHERE provider_slug = 'deepinfra'),
    'text->text',
    '["prompt", "completion"]'::jsonb,
    'USD per 1M tokens',
    '(prompt_tokens * prompt_price + completion_tokens * completion_price) / 1000000'
);

-- Insert example data for AiHubMix
INSERT INTO provider_pricing_standards (provider_slug, provider_name, pricing_unit, api_format, conversion_factor, pricing_model)
VALUES ('aihubmix', 'AiHubMix', 'per_1K_tokens', 'per_1K_tokens', 0.001, 'usage_based');

INSERT INTO provider_modalities (provider_id, modality, pricing_fields, unit_description, calculation_formula, field_mapping)
VALUES (
    (SELECT id FROM provider_pricing_standards WHERE provider_slug = 'aihubmix'),
    'text->text',
    '["input", "output"]'::jsonb,
    'USD per 1K tokens',
    '(prompt_tokens * input_price + completion_tokens * output_price) / 1000',
    '{"input": "prompt", "output": "completion"}'::jsonb
);

-- View to easily query provider information
CREATE VIEW v_provider_pricing_info AS
SELECT
    p.provider_slug,
    p.provider_name,
    p.pricing_unit,
    p.api_format,
    p.conversion_factor,
    p.pricing_model,
    p.is_active,
    json_agg(
        json_build_object(
            'modality', m.modality,
            'pricing_fields', m.pricing_fields,
            'unit_description', m.unit_description,
            'calculation_formula', m.calculation_formula,
            'field_mapping', m.field_mapping
        )
    ) AS modalities,
    p.updated_at
FROM provider_pricing_standards p
LEFT JOIN provider_modalities m ON m.provider_id = p.id
WHERE p.is_active = true
GROUP BY p.id, p.provider_slug, p.provider_name, p.pricing_unit, p.api_format,
         p.conversion_factor, p.pricing_model, p.is_active, p.updated_at;

-- Function to get provider standard by slug
CREATE OR REPLACE FUNCTION get_provider_standard(p_provider_slug VARCHAR)
RETURNS JSON AS $$
DECLARE
    result JSON;
BEGIN
    SELECT row_to_json(v.*) INTO result
    FROM v_provider_pricing_info v
    WHERE v.provider_slug = p_provider_slug;

    RETURN result;
END;
$$ LANGUAGE plpgsql;

-- Function to log pricing changes
CREATE OR REPLACE FUNCTION log_pricing_change()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO provider_pricing_history (
        provider_id,
        change_type,
        old_value,
        new_value,
        changed_by,
        notes
    ) VALUES (
        NEW.id,
        'format_change',
        row_to_json(OLD),
        row_to_json(NEW),
        current_user,
        'Automated change log'
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically log changes
CREATE TRIGGER provider_pricing_change_trigger
AFTER UPDATE ON provider_pricing_standards
FOR EACH ROW
WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE FUNCTION log_pricing_change();

-- Query examples:

-- Get all active providers
-- SELECT * FROM v_provider_pricing_info;

-- Get specific provider standard
-- SELECT get_provider_standard('openrouter');

-- Get providers by pricing unit
-- SELECT provider_slug, provider_name
-- FROM provider_pricing_standards
-- WHERE pricing_unit = 'per_token';

-- Get pricing history for a provider
-- SELECT * FROM provider_pricing_history
-- WHERE provider_id = (SELECT id FROM provider_pricing_standards WHERE provider_slug = 'openrouter')
-- ORDER BY created_at DESC;

-- Find providers supporting specific modality
-- SELECT DISTINCT p.provider_slug, p.provider_name
-- FROM provider_pricing_standards p
-- JOIN provider_modalities m ON m.provider_id = p.id
-- WHERE m.modality = 'text->image';
