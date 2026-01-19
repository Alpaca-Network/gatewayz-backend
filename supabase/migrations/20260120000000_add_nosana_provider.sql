-- Migration: Add Nosana as a GPU computing provider
-- Created: 2026-01-20
-- Description: Adds Nosana to the providers table for distributed GPU computing
--
-- Nosana provides:
-- - Distributed GPU computing network
-- - AI model inference deployments
-- - OpenAI-compatible API endpoints
-- - Credit-based billing system
-- - GPU marketplace access

-- ============================================================================
-- INSERT NOSANA PROVIDER
-- ============================================================================

INSERT INTO "public"."providers" (
    "name",
    "slug",
    "description",
    "base_url",
    "api_key_env_var",
    "logo_url",
    "site_url",
    "privacy_policy_url",
    "terms_of_service_url",
    "supports_streaming",
    "supports_function_calling",
    "supports_vision",
    "supports_image_generation",
    "is_active",
    "metadata"
) VALUES
    (
        'Nosana',
        'nosana',
        'Nosana GPU Computing Network - Distributed GPU infrastructure for AI workloads with support for LLM inference (vLLM, Ollama, LMDeploy), image generation (Stable Diffusion), and audio transcription (Whisper)',
        'https://dashboard.k8s.prd.nos.ci/api',
        'NOSANA_API_KEY',
        'https://nosana.com/favicon.ico',
        'https://nosana.com',
        'https://nosana.com/privacy',
        'https://nosana.com/terms',
        true,
        true,
        false,
        true,
        true,
        jsonb_build_object(
            'provider_type', 'gpu_compute',
            'features', jsonb_build_array(
                'deployments',
                'jobs',
                'gpu_marketplace',
                'credit_billing',
                'vllm',
                'ollama',
                'lmdeploy',
                'stable_diffusion',
                'whisper'
            ),
            'supported_frameworks', jsonb_build_array(
                'vllm',
                'ollama',
                'lmdeploy',
                'stable-diffusion-webui',
                'whisper'
            ),
            'api_docs', 'https://learn.nosana.com/api',
            'sdk_docs', 'https://kit.nosana.com/',
            'swagger_ui', 'https://dashboard.k8s.prd.nos.ci/api/swagger',
            'deployment_strategies', jsonb_build_array(
                'SIMPLE',
                'SIMPLE-EXTEND',
                'INFINITE',
                'SCHEDULED'
            ),
            'market_types', jsonb_build_array(
                'PREMIUM',
                'COMMUNITY',
                'OTHER'
            )
        )
    )
ON CONFLICT (slug) DO UPDATE SET
    "name" = EXCLUDED."name",
    "description" = EXCLUDED."description",
    "base_url" = EXCLUDED."base_url",
    "api_key_env_var" = EXCLUDED."api_key_env_var",
    "logo_url" = EXCLUDED."logo_url",
    "site_url" = EXCLUDED."site_url",
    "privacy_policy_url" = EXCLUDED."privacy_policy_url",
    "terms_of_service_url" = EXCLUDED."terms_of_service_url",
    "supports_streaming" = EXCLUDED."supports_streaming",
    "supports_function_calling" = EXCLUDED."supports_function_calling",
    "supports_vision" = EXCLUDED."supports_vision",
    "supports_image_generation" = EXCLUDED."supports_image_generation",
    "is_active" = EXCLUDED."is_active",
    "metadata" = EXCLUDED."metadata",
    "updated_at" = now();

-- ============================================================================
-- INSERT NOSANA MODELS (Common deployable models)
-- ============================================================================

-- Insert common models that can be deployed on Nosana
INSERT INTO "public"."models" (
    "provider_id",
    "model_id",
    "model_name",
    "provider_model_id",
    "context_length",
    "modality",
    "supports_streaming",
    "supports_function_calling",
    "supports_vision",
    "is_active",
    "metadata"
)
SELECT
    p.id,
    m.model_id,
    m.model_name,
    m.provider_model_id,
    m.context_length,
    m.modality,
    m.supports_streaming,
    m.supports_function_calling,
    m.supports_vision,
    true,
    m.metadata
FROM "public"."providers" p
CROSS JOIN (
    VALUES
        -- Llama 3.x models (vLLM/Ollama deployable)
        (
            'nosana/meta-llama/Llama-3.3-70B-Instruct',
            'Llama 3.3 70B Instruct',
            'meta-llama/Llama-3.3-70B-Instruct',
            131072,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "140GB", "recommended_gpus": ["A100", "H100"]}'::jsonb
        ),
        (
            'nosana/meta-llama/Llama-3.1-70B-Instruct',
            'Llama 3.1 70B Instruct',
            'meta-llama/Llama-3.1-70B-Instruct',
            131072,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "140GB", "recommended_gpus": ["A100", "H100"]}'::jsonb
        ),
        (
            'nosana/meta-llama/Llama-3.1-8B-Instruct',
            'Llama 3.1 8B Instruct',
            'meta-llama/Llama-3.1-8B-Instruct',
            131072,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "16GB", "recommended_gpus": ["A10", "RTX 4090"]}'::jsonb
        ),
        -- Qwen models
        (
            'nosana/Qwen/Qwen2.5-72B-Instruct',
            'Qwen 2.5 72B Instruct',
            'Qwen/Qwen2.5-72B-Instruct',
            131072,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "144GB", "recommended_gpus": ["A100", "H100"]}'::jsonb
        ),
        (
            'nosana/Qwen/Qwen2.5-7B-Instruct',
            'Qwen 2.5 7B Instruct',
            'Qwen/Qwen2.5-7B-Instruct',
            131072,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "14GB", "recommended_gpus": ["A10", "RTX 4090"]}'::jsonb
        ),
        -- DeepSeek models
        (
            'nosana/deepseek-ai/DeepSeek-R1',
            'DeepSeek R1',
            'deepseek-ai/DeepSeek-R1',
            65536,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "640GB", "recommended_gpus": ["H100"]}'::jsonb
        ),
        (
            'nosana/deepseek-ai/DeepSeek-V3',
            'DeepSeek V3',
            'deepseek-ai/DeepSeek-V3',
            131072,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "640GB", "recommended_gpus": ["H100"]}'::jsonb
        ),
        -- Mixtral models
        (
            'nosana/mistralai/Mixtral-8x22B-Instruct-v0.1',
            'Mixtral 8x22B Instruct',
            'mistralai/Mixtral-8x22B-Instruct-v0.1',
            65536,
            'text',
            true,
            true,
            false,
            '{"framework": "vllm", "gpu_memory_required": "176GB", "recommended_gpus": ["A100", "H100"]}'::jsonb
        ),
        -- Image generation
        (
            'nosana/stabilityai/stable-diffusion-xl-base-1.0',
            'Stable Diffusion XL Base 1.0',
            'stabilityai/stable-diffusion-xl-base-1.0',
            0,
            'image',
            false,
            false,
            false,
            '{"framework": "stable-diffusion-webui", "gpu_memory_required": "16GB", "recommended_gpus": ["A10", "RTX 4090"]}'::jsonb
        ),
        -- Audio transcription
        (
            'nosana/openai/whisper-large-v3',
            'Whisper Large V3',
            'large-v3',
            0,
            'audio',
            false,
            false,
            false,
            '{"framework": "whisper", "gpu_memory_required": "10GB", "recommended_gpus": ["A10", "RTX 4090"]}'::jsonb
        )
) AS m(model_id, model_name, provider_model_id, context_length, modality, supports_streaming, supports_function_calling, supports_vision, metadata)
WHERE p.slug = 'nosana'
ON CONFLICT (provider_id, provider_model_id) DO UPDATE SET
    "model_id" = EXCLUDED."model_id",
    "model_name" = EXCLUDED."model_name",
    "context_length" = EXCLUDED."context_length",
    "modality" = EXCLUDED."modality",
    "supports_streaming" = EXCLUDED."supports_streaming",
    "supports_function_calling" = EXCLUDED."supports_function_calling",
    "supports_vision" = EXCLUDED."supports_vision",
    "metadata" = EXCLUDED."metadata",
    "updated_at" = now();
