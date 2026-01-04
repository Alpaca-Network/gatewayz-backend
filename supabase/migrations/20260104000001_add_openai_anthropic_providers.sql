-- Migration: Add OpenAI and Anthropic as direct AI model providers
-- Created: 2026-01-04
-- Description: Adds OpenAI and Anthropic to the providers table for direct API access

-- ============================================================================
-- INSERT OPENAI AND ANTHROPIC PROVIDERS
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
    "is_active"
) VALUES
    (
        'OpenAI',
        'openai',
        'OpenAI API - GPT-4, GPT-3.5, DALL-E, and Whisper models',
        'https://api.openai.com/v1',
        'OPENAI_API_KEY',
        'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/openai.svg',
        'https://openai.com',
        'https://openai.com/policies/privacy-policy',
        'https://openai.com/policies/terms-of-use',
        true,
        true,
        true,
        true,
        true
    ),
    (
        'Anthropic',
        'anthropic',
        'Anthropic API - Claude 3.5, Claude 3, and Claude 2 models',
        'https://api.anthropic.com',
        'ANTHROPIC_API_KEY',
        'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/anthropic.svg',
        'https://anthropic.com',
        'https://www.anthropic.com/privacy',
        'https://www.anthropic.com/terms',
        true,
        true,
        true,
        false,
        true
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
    "updated_at" = now();
