-- Migration: Seed Tier-2 providers (DeepSeek, Moonshot/Kimi, MiniMax, Xiaomi MiMo)
-- Created: 2026-07-17
-- Description: Adds the four Tier-2 OpenAI-compatible providers wired up in
-- Task 18 (src/services/providers/adapter_configs.py + provider_registry.py)
-- to the providers table, following the existing provider-seed pattern (see
-- 20260104000001_add_openai_anthropic_providers.sql,
-- 20260120000000_add_nosana_provider.sql).
--
-- Controller decision (Task 18 amendment): this migration is committed but
-- NOT executed against any database from this refactor branch — no DB writes
-- are made as part of this task. Uses ON CONFLICT DO NOTHING (not DO UPDATE)
-- per that same decision, so applying it later is a pure no-op against any
-- row a human/ops process may have already seeded manually.
--
-- Base URLs verified live against each provider's OpenAI-compatible /models
-- endpoint at implementation time (see task-18-report.md for sources):
--   - DeepSeek:   https://api.deepseek.com/v1        (DEEPSEEK_API_KEY)
--   - Moonshot:   https://api.moonshot.ai/v1          (MOONSHOT_API_KEY)
--   - MiniMax:    https://api.minimax.io/v1           (MINIMAX_API_KEY)
--   - Xiaomi MiMo: https://api.xiaomimimo.com/v1      (XIAOMI_API_KEY)
--     (api.xiaomimimo.com resolves to Xiaomi-owned infra
--     mimo-pri-azams.alb.xiaomi.com; no XIAOMI_API_KEY is provisioned yet,
--     so this provider is code-only / untested live — see report for detail.)

-- ============================================================================
-- INSERT TIER-2 PROVIDERS
-- ============================================================================

INSERT INTO "public"."providers" (
    "name",
    "slug",
    "description",
    "base_url",
    "api_key_env_var",
    "site_url",
    "supports_streaming",
    "supports_function_calling",
    "supports_vision",
    "supports_image_generation",
    "is_active",
    "metadata"
) VALUES
    (
        'DeepSeek',
        'deepseek',
        'DeepSeek API - deepseek-v4-flash / deepseek-v4-pro models, OpenAI-compatible',
        'https://api.deepseek.com/v1',
        'DEEPSEEK_API_KEY',
        'https://www.deepseek.com',
        true,
        true,
        false,
        false,
        true,
        jsonb_build_object('provider_tier', 'tier2', 'added_in', 'task-18')
    ),
    (
        'Moonshot AI',
        'moonshot',
        'Moonshot AI (Kimi) API - kimi-k2.5 and moonshot-v1 model family, OpenAI-compatible',
        'https://api.moonshot.ai/v1',
        'MOONSHOT_API_KEY',
        'https://www.moonshot.ai',
        true,
        true,
        true,
        false,
        true,
        jsonb_build_object('provider_tier', 'tier2', 'added_in', 'task-18')
    ),
    (
        'MiniMax',
        'minimax',
        'MiniMax API - MiniMax-M series models, OpenAI-compatible',
        'https://api.minimax.io/v1',
        'MINIMAX_API_KEY',
        'https://www.minimax.io',
        true,
        true,
        true,
        false,
        true,
        jsonb_build_object('provider_tier', 'tier2', 'added_in', 'task-18')
    ),
    (
        'Xiaomi MiMo',
        'xiaomi',
        'Xiaomi MiMo API Open Platform - MiMo-V2/V2.5 model family, OpenAI-compatible. '
        'No XIAOMI_API_KEY provisioned yet (code-only as of task-18).',
        'https://api.xiaomimimo.com/v1',
        'XIAOMI_API_KEY',
        'https://mimo.mi.com',
        true,
        false,
        false,
        false,
        true,
        jsonb_build_object('provider_tier', 'tier2', 'added_in', 'task-18', 'live_smoke_tested', false)
    )
ON CONFLICT (slug) DO NOTHING;
