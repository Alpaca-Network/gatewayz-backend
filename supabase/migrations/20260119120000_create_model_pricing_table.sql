-- ============================================================================
-- Create Model Pricing Table
-- ============================================================================
-- Separate table for standardized per-token pricing
-- This makes it easier to update pricing without touching model data
-- ============================================================================

-- Create the model_pricing table
CREATE TABLE IF NOT EXISTS "public"."model_pricing" (
    "id" BIGSERIAL PRIMARY KEY,
    "model_id" BIGINT NOT NULL,
    "price_per_input_token" NUMERIC(20, 15) NOT NULL DEFAULT 0,
    "price_per_output_token" NUMERIC(20, 15) NOT NULL DEFAULT 0,
    "price_per_image_token" NUMERIC(20, 15),
    "price_per_request" NUMERIC(10, 6),
    "pricing_source" TEXT DEFAULT 'provider',
    "last_updated" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Ensure one pricing entry per model
    UNIQUE(model_id),

    -- Foreign key to models table
    FOREIGN KEY (model_id) REFERENCES "public"."models"(id) ON DELETE CASCADE
);

-- Add indexes for fast lookups
CREATE INDEX IF NOT EXISTS "idx_model_pricing_model_id" ON "public"."model_pricing"(model_id);
CREATE INDEX IF NOT EXISTS "idx_model_pricing_last_updated" ON "public"."model_pricing"(last_updated);

-- Add column comments
COMMENT ON TABLE "public"."model_pricing" IS
    'Standardized per-token pricing for all models. All prices stored in USD per single token.';

COMMENT ON COLUMN "public"."model_pricing"."price_per_input_token" IS
    'Cost in USD per single input/prompt token (e.g., 0.000000055 = $0.055 per 1M tokens)';

COMMENT ON COLUMN "public"."model_pricing"."price_per_output_token" IS
    'Cost in USD per single output/completion token (e.g., 0.000000055 = $0.055 per 1M tokens)';

COMMENT ON COLUMN "public"."model_pricing"."price_per_image_token" IS
    'Cost in USD per single image token (for vision models)';

COMMENT ON COLUMN "public"."model_pricing"."price_per_request" IS
    'Cost in USD per request (for per-request pricing models)';

COMMENT ON COLUMN "public"."model_pricing"."pricing_source" IS
    'Source of pricing: provider, manual, cross-reference, estimated';

-- Create updated_at trigger
CREATE OR REPLACE FUNCTION update_model_pricing_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_model_pricing_updated_at
    BEFORE UPDATE ON "public"."model_pricing"
    FOR EACH ROW
    EXECUTE FUNCTION update_model_pricing_updated_at();

-- Create view for easy joins
CREATE OR REPLACE VIEW "public"."models_with_pricing" AS
SELECT
    m.*,
    mp.price_per_input_token,
    mp.price_per_output_token,
    mp.price_per_image_token,
    mp.price_per_request,
    mp.pricing_source,
    mp.last_updated as pricing_last_updated
FROM "public"."models" m
LEFT JOIN "public"."model_pricing" mp ON m.id = mp.model_id;

COMMENT ON VIEW "public"."models_with_pricing" IS
    'Convenient view joining models with their standardized pricing';

-- Grant permissions
GRANT SELECT ON "public"."model_pricing" TO authenticated;
GRANT SELECT ON "public"."model_pricing" TO anon;
GRANT ALL ON "public"."model_pricing" TO service_role;

GRANT SELECT ON "public"."models_with_pricing" TO authenticated;
GRANT SELECT ON "public"."models_with_pricing" TO anon;
GRANT SELECT ON "public"."models_with_pricing" TO service_role;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE '✅ Model pricing table created successfully';
    RAISE NOTICE '   - All prices stored as per-token format';
    RAISE NOTICE '   - Separate from models table for easy updates';
    RAISE NOTICE '   - View created: models_with_pricing';
END$$;
