-- Migration: Add pricing for 8 models currently using default pricing
-- Generated: 2026-01-15
-- Purpose: Fix models showing "not found in catalog" warnings
-- Fixed: Changed table from models_catalog to models and column names to match actual schema

-- ============================================================================
-- Update pricing for models that exist in the models table
-- The models table uses:
--   - pricing_prompt (instead of input_cost_per_token)
--   - pricing_completion (instead of output_cost_per_token)
--   - model_id field for identification
-- ============================================================================

-- 1. DeepSeek Chat (DeepSeek V3)
UPDATE "public"."models" SET
    pricing_prompt = 0.0000003,      -- $0.30 per 1M input tokens
    pricing_completion = 0.0000012,  -- $1.20 per 1M output tokens
    context_length = 163840,
    updated_at = NOW()
WHERE model_id = 'deepseek/deepseek-chat'
   OR provider_model_id = 'deepseek/deepseek-chat'
   OR model_id = 'deepseek-chat';

-- 2. Google Gemini 2.0 Flash
UPDATE "public"."models" SET
    pricing_prompt = 0.0000001,      -- $0.10 per 1M input tokens
    pricing_completion = 0.0000004,  -- $0.40 per 1M output tokens
    context_length = 1048576,
    updated_at = NOW()
WHERE model_id = 'google/gemini-2.0-flash'
   OR provider_model_id = 'google/gemini-2.0-flash'
   OR model_id = 'gemini-2.0-flash';

-- 2b. Gemini 2.0 Flash Experimental (free during preview)
UPDATE "public"."models" SET
    pricing_prompt = 0,
    pricing_completion = 0,
    context_length = 1048576,
    updated_at = NOW()
WHERE model_id = 'google/gemini-2.0-flash-exp'
   OR provider_model_id = 'google/gemini-2.0-flash-exp'
   OR model_id = 'gemini-2.0-flash-exp';

-- 3. Mistral Large (latest version)
UPDATE "public"."models" SET
    pricing_prompt = 0.000002,       -- $2.00 per 1M input tokens
    pricing_completion = 0.000006,   -- $6.00 per 1M output tokens
    context_length = 128000,
    updated_at = NOW()
WHERE model_id = 'mistral/mistral-large'
   OR model_id = 'mistralai/mistral-large'
   OR provider_model_id LIKE '%mistral-large%'
   OR model_id = 'mistral-large';

-- 4. Meta Llama 3 8B Instruct
UPDATE "public"."models" SET
    pricing_prompt = 0.00000003,     -- $0.03 per 1M input tokens
    pricing_completion = 0.00000006, -- $0.06 per 1M output tokens
    context_length = 8192,
    updated_at = NOW()
WHERE model_id = 'meta-llama/llama-3-8b-instruct'
   OR model_id = 'meta/llama-3-8b-instruct'
   OR provider_model_id LIKE '%llama-3-8b-instruct%'
   OR model_id = 'llama-3-8b-instruct';

-- 5. Cohere Command R+
UPDATE "public"."models" SET
    pricing_prompt = 0.0000025,      -- $2.50 per 1M input tokens
    pricing_completion = 0.00001,    -- $10.00 per 1M output tokens
    context_length = 128000,
    updated_at = NOW()
WHERE model_id = 'cohere/command-r-plus'
   OR provider_model_id = 'cohere/command-r-plus'
   OR model_id = 'command-r-plus';

-- 6. Alibaba Qwen 3 14B (estimated pricing)
UPDATE "public"."models" SET
    pricing_prompt = 0.0000005,      -- $0.50 per 1M input tokens (estimated)
    pricing_completion = 0.0000015,  -- $1.50 per 1M output tokens (estimated)
    context_length = 32768,
    updated_at = NOW()
WHERE model_id = 'alibaba/qwen-3-14b'
   OR provider_model_id LIKE '%qwen-3-14b%'
   OR model_id = 'qwen-3-14b';

-- 7. Black Forest Labs FLUX 1.1 Pro (image generation)
UPDATE "public"."models" SET
    pricing_prompt = 0.00004,        -- $0.04 per image
    pricing_completion = 0,
    pricing_image = 0.00004,
    updated_at = NOW()
WHERE model_id = 'black-forest-labs/flux-1.1-pro'
   OR model_id = 'bfl/flux-1-1-pro'
   OR provider_model_id LIKE '%flux-1.1-pro%'
   OR provider_model_id LIKE '%flux-1-1-pro%';

-- 8. ByteDance SDXL Lightning (image generation)
UPDATE "public"."models" SET
    pricing_prompt = 0.000012,       -- $0.012 per image
    pricing_completion = 0,
    pricing_image = 0.000012,
    updated_at = NOW()
WHERE model_id = 'bytedance/sdxl-lightning-4step'
   OR provider_model_id LIKE '%sdxl-lightning%';

-- ============================================================================
-- Add comment explaining this migration
-- ============================================================================
COMMENT ON TABLE "public"."models" IS 'AI models with provider relationships, pricing, and health monitoring - Updated 2026-01-15 to fix 8 models using default pricing';
