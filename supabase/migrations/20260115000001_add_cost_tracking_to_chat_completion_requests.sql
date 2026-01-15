-- Migration: Add cost tracking to chat_completion_requests
-- Created: 2026-01-15
-- Description: Add cost fields to track actual per-request costs for accurate analytics

-- Add cost tracking columns to chat_completion_requests
ALTER TABLE "public"."chat_completion_requests"
ADD COLUMN IF NOT EXISTS "cost_usd" DECIMAL(12, 6) DEFAULT NULL,
ADD COLUMN IF NOT EXISTS "input_cost_usd" DECIMAL(12, 6) DEFAULT NULL,
ADD COLUMN IF NOT EXISTS "output_cost_usd" DECIMAL(12, 6) DEFAULT NULL,
ADD COLUMN IF NOT EXISTS "pricing_source" VARCHAR(50) DEFAULT 'calculated';

-- Add comments
COMMENT ON COLUMN "public"."chat_completion_requests"."cost_usd" IS
    'Total cost in USD for this request (input + output cost)';
COMMENT ON COLUMN "public"."chat_completion_requests"."input_cost_usd" IS
    'Cost in USD for input/prompt tokens';
COMMENT ON COLUMN "public"."chat_completion_requests"."output_cost_usd" IS
    'Cost in USD for output/completion tokens';
COMMENT ON COLUMN "public"."chat_completion_requests"."pricing_source" IS
    'Source of pricing data: calculated, model_pricing, manual_pricing, cross_reference';

-- Create index for cost-based queries
CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_cost"
    ON "public"."chat_completion_requests" ("cost_usd" DESC NULLS LAST)
    WHERE cost_usd IS NOT NULL;

CREATE INDEX IF NOT EXISTS "idx_chat_completion_requests_model_cost"
    ON "public"."chat_completion_requests" ("model_id", "cost_usd" DESC NULLS LAST)
    WHERE cost_usd IS NOT NULL;

-- Create function to calculate missing costs (backfill)
CREATE OR REPLACE FUNCTION calculate_missing_request_costs()
RETURNS TABLE (
    updated_count INTEGER,
    total_cost_calculated DECIMAL(12, 6)
) AS $$
DECLARE
    v_updated_count INTEGER := 0;
    v_total_cost DECIMAL(12, 6) := 0;
BEGIN
    -- Update requests that don't have cost calculated yet
    WITH updated AS (
        UPDATE "public"."chat_completion_requests" ccr
        SET
            input_cost_usd = ROUND(CAST(ccr.input_tokens * COALESCE(m.pricing_prompt, 0) AS NUMERIC), 6),
            output_cost_usd = ROUND(CAST(ccr.output_tokens * COALESCE(m.pricing_completion, 0) AS NUMERIC), 6),
            cost_usd = ROUND(
                CAST(
                    (ccr.input_tokens * COALESCE(m.pricing_prompt, 0)) +
                    (ccr.output_tokens * COALESCE(m.pricing_completion, 0))
                AS NUMERIC
            ), 6),
            pricing_source = 'backfilled'
        FROM "public"."models" m
        WHERE ccr.model_id = m.id
        AND ccr.cost_usd IS NULL
        AND ccr.status = 'completed'
        RETURNING ccr.cost_usd
    )
    SELECT COUNT(*)::INTEGER, COALESCE(SUM(cost_usd), 0)
    INTO v_updated_count, v_total_cost
    FROM updated;

    RETURN QUERY SELECT v_updated_count, v_total_cost;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION calculate_missing_request_costs() IS
    'Backfill cost calculations for requests that don''t have cost_usd set. '
    'Returns the number of updated rows and total cost calculated.';

-- Backfill existing records (run once)
DO $$
DECLARE
    backfill_result RECORD;
BEGIN
    SELECT * FROM calculate_missing_request_costs() INTO backfill_result;

    RAISE NOTICE 'Backfilled % requests with total cost of $%',
        backfill_result.updated_count,
        backfill_result.total_cost_calculated;
END $$;

-- Log success
DO $$
BEGIN
    RAISE NOTICE 'Successfully added cost tracking columns to chat_completion_requests table';
END $$;
