-- Migration: Add pricing for 8 models currently using default pricing
-- Generated: 2026-01-15
-- Purpose: Fix models showing "not found in catalog" warnings

-- ============================================================================
-- 1. DeepSeek Chat (DeepSeek V3)
-- ============================================================================
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'deepseek/deepseek-chat',
    'DeepSeek V3',
    'DeepSeek',
    'openrouter',
    'deepseek',
    0.0000003,    -- $0.30 per 1M input tokens
    0.0000012,    -- $1.20 per 1M output tokens
    'USD',
    163840,
    8192,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    context_length = EXCLUDED.context_length,
    updated_at = NOW();

-- ============================================================================
-- 2. Google Gemini 2.0 Flash
-- ============================================================================
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    supports_vision,
    created_at,
    updated_at
) VALUES (
    'google/gemini-2.0-flash',
    'Gemini 2.0 Flash',
    'Google',
    'openrouter',
    'google-vertex',
    0.0000001,    -- $0.10 per 1M input tokens
    0.0000004,    -- $0.40 per 1M output tokens
    'USD',
    1048576,      -- 1M context
    8192,
    true,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    context_length = EXCLUDED.context_length,
    updated_at = NOW();

-- Also add the -exp variant
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    supports_vision,
    created_at,
    updated_at
) VALUES (
    'google/gemini-2.0-flash-exp',
    'Gemini 2.0 Flash Experimental',
    'Google',
    'openrouter',
    'google-vertex',
    0,            -- Free during preview
    0,
    'USD',
    1048576,
    8192,
    true,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    updated_at = NOW();

-- ============================================================================
-- 3. Mistral Large (latest version)
-- ============================================================================
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'mistral/mistral-large',
    'Mistral Large',
    'Mistral AI',
    'openrouter',
    'fireworks',
    0.000002,     -- $2.00 per 1M input tokens
    0.000006,     -- $6.00 per 1M output tokens
    'USD',
    128000,
    4096,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    context_length = EXCLUDED.context_length,
    updated_at = NOW();

-- Also add mistralai prefix version
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'mistralai/mistral-large',
    'Mistral Large',
    'Mistral AI',
    'openrouter',
    'fireworks',
    0.000002,
    0.000006,
    'USD',
    128000,
    4096,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    updated_at = NOW();

-- ============================================================================
-- 4. Meta Llama 3 8B Instruct
-- ============================================================================
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'meta-llama/llama-3-8b-instruct',
    'Llama 3 8B Instruct',
    'Meta',
    'openrouter',
    'together',
    0.00000003,   -- $0.03 per 1M input tokens
    0.00000006,   -- $0.06 per 1M output tokens
    'USD',
    8192,
    2048,
    true,
    false,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    context_length = EXCLUDED.context_length,
    updated_at = NOW();

-- Also add meta/ prefix version for compatibility
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'meta/llama-3-8b-instruct',
    'Llama 3 8B Instruct',
    'Meta',
    'openrouter',
    'together',
    0.00000003,
    0.00000006,
    'USD',
    8192,
    2048,
    true,
    false,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    updated_at = NOW();

-- ============================================================================
-- 5. Cohere Command R+
-- ============================================================================
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'cohere/command-r-plus',
    'Command R+',
    'Cohere',
    'openrouter',
    'cohere',
    0.0000025,    -- $2.50 per 1M input tokens
    0.00001,      -- $10.00 per 1M output tokens
    'USD',
    128000,
    4096,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    context_length = EXCLUDED.context_length,
    updated_at = NOW();

-- ============================================================================
-- 6. Alibaba Qwen 3 14B
-- ============================================================================
-- Note: Exact Qwen-3-14B not found in OpenRouter, using Qwen3 VL 32B as reference
-- TODO: Update with actual Alibaba Cloud pricing when available
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    supports_function_calling,
    created_at,
    updated_at
) VALUES (
    'alibaba/qwen-3-14b',
    'Qwen 3 14B',
    'Alibaba Cloud',
    'alibaba',
    'alibaba',
    0.0000005,    -- $0.50 per 1M input tokens (estimated)
    0.0000015,    -- $1.50 per 1M output tokens (estimated)
    'USD',
    32768,
    8192,
    true,
    true,
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    context_length = EXCLUDED.context_length,
    updated_at = NOW();

-- ============================================================================
-- 7. Black Forest Labs FLUX 1.1 Pro
-- ============================================================================
-- Note: BFL FLUX is an image generation model, not in OpenRouter text API
-- Using typical Flux Pro pricing
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    model_type,
    created_at,
    updated_at
) VALUES (
    'black-forest-labs/flux-1.1-pro',
    'FLUX 1.1 Pro',
    'Black Forest Labs',
    'fal',
    'fal',
    0.00004,      -- $0.04 per image (converted to token equivalent)
    0,
    'USD',
    77,           -- Typical prompt length in tokens
    0,
    false,
    'image',
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    updated_at = NOW();

-- Also add bfl/ prefix version
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    model_type,
    created_at,
    updated_at
) VALUES (
    'bfl/flux-1-1-pro',
    'FLUX 1.1 Pro',
    'Black Forest Labs',
    'fal',
    'fal',
    0.00004,
    0,
    'USD',
    77,
    0,
    false,
    'image',
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    updated_at = NOW();

-- ============================================================================
-- 8. ByteDance SDXL Lightning
-- ============================================================================
-- Note: ByteDance SDXL is an image generation model
-- Using typical SDXL Lightning pricing (very fast, cheaper)
INSERT INTO models_catalog (
    id,
    name,
    provider,
    source_gateway,
    provider_slug,
    input_cost_per_token,
    output_cost_per_token,
    pricing_currency,
    context_length,
    max_output_tokens,
    supports_streaming,
    model_type,
    created_at,
    updated_at
) VALUES (
    'bytedance/sdxl-lightning-4step',
    'SDXL Lightning 4-Step',
    'ByteDance',
    'fal',
    'fal',
    0.000012,     -- $0.012 per image (converted to token equivalent)
    0,
    'USD',
    77,
    0,
    false,
    'image',
    NOW(),
    NOW()
) ON CONFLICT (id) DO UPDATE SET
    input_cost_per_token = EXCLUDED.input_cost_per_token,
    output_cost_per_token = EXCLUDED.output_cost_per_token,
    updated_at = NOW();

-- ============================================================================
-- Create indexes for faster lookups
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_models_catalog_provider_slug
ON models_catalog(provider_slug);

CREATE INDEX IF NOT EXISTS idx_models_catalog_source_gateway
ON models_catalog(source_gateway);

CREATE INDEX IF NOT EXISTS idx_models_catalog_model_type
ON models_catalog(model_type);

-- ============================================================================
-- Add comment explaining this migration
-- ============================================================================
COMMENT ON TABLE models_catalog IS 'Model catalog with pricing - Updated 2026-01-15 to fix 8 models using default pricing';
