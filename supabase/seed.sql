-- =============================================================================
-- Supabase Test Data Seed File
-- =============================================================================
-- This file seeds the database with mock data for testing and development.
-- It runs automatically with `supabase db reset` if enabled in config.toml.
--
-- To run manually:
--   psql -h localhost -p 54322 -U postgres -d postgres -f supabase/seed.sql
-- =============================================================================

-- Ensure we're in the public schema
SET search_path TO public;

-- =============================================================================
-- PROVIDERS
-- =============================================================================
INSERT INTO providers (name, slug, description, base_url, is_active, supports_streaming, supports_function_calling, supports_vision, health_status)
VALUES
    ('OpenRouter', 'openrouter', 'OpenRouter AI inference provider', 'https://openrouter.ai/api/v1', true, true, true, true, 'healthy'),
    ('Portkey', 'portkey', 'Portkey gateway integration', 'https://api.portkey.ai/v1', true, true, true, true, 'healthy'),
    ('Featherless', 'featherless', 'Featherless AI provider', 'https://api.featherless.ai/v1', true, true, false, false, 'healthy'),
    ('DeepInfra', 'deepinfra', 'DeepInfra inference provider', 'https://api.deepinfra.com/v1', true, true, false, false, 'healthy'),
    ('Fireworks AI', 'fireworks', 'Fireworks AI provider', 'https://api.fireworks.ai/inference/v1', true, true, true, false, 'healthy'),
    ('Together AI', 'together', 'Together AI provider', 'https://api.together.xyz/v1', true, true, true, false, 'healthy'),
    ('HuggingFace', 'huggingface', 'HuggingFace inference API', 'https://api-inference.huggingface.co', true, true, false, false, 'healthy')
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    base_url = EXCLUDED.base_url,
    is_active = EXCLUDED.is_active,
    supports_streaming = EXCLUDED.supports_streaming,
    supports_function_calling = EXCLUDED.supports_function_calling,
    supports_vision = EXCLUDED.supports_vision,
    health_status = EXCLUDED.health_status;

-- =============================================================================
-- SUBSCRIPTION PLANS
-- =============================================================================
INSERT INTO plans (name, description, price_per_month, daily_request_limit, daily_token_limit, monthly_request_limit, monthly_token_limit, features, is_active, max_concurrent_requests)
VALUES
    ('Free', 'Free tier with limited usage', 0.00, 100, 10000, 1000, 100000, '["Basic models", "Community support"]'::jsonb, true, 2),
    ('Pro', 'Professional tier for developers', 29.00, 1000, 100000, 30000, 3000000, '["All models", "Priority support", "API analytics"]'::jsonb, true, 10),
    ('Enterprise', 'Enterprise tier with unlimited access', 99.00, 10000, 1000000, 300000, 30000000, '["All models", "24/7 support", "SLA", "Custom integrations"]'::jsonb, true, 50)
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    price_per_month = EXCLUDED.price_per_month,
    daily_request_limit = EXCLUDED.daily_request_limit,
    daily_token_limit = EXCLUDED.daily_token_limit,
    monthly_request_limit = EXCLUDED.monthly_request_limit,
    monthly_token_limit = EXCLUDED.monthly_token_limit,
    features = EXCLUDED.features,
    is_active = EXCLUDED.is_active,
    max_concurrent_requests = EXCLUDED.max_concurrent_requests;

-- =============================================================================
-- TEST USERS
-- =============================================================================
-- Create test users with various subscription states

INSERT INTO users (username, email, credits, is_active, auth_method, subscription_status, role, welcome_email_sent, privy_user_id)
VALUES
    -- Admin user
    ('admin_user', 'admin@test.example.com', 10000.00, true, 'email', 'active', 'admin', true, 'privy_admin_001'),

    -- Developer users
    ('dev_alice', 'alice@test.example.com', 500.00, true, 'github', 'active', 'developer', true, 'privy_dev_001'),
    ('dev_bob', 'bob@test.example.com', 250.00, true, 'google', 'active', 'developer', true, 'privy_dev_002'),

    -- Active users with credits
    ('user_charlie', 'charlie@test.example.com', 150.00, true, 'email', 'active', 'user', true, 'privy_user_001'),
    ('user_diana', 'diana@test.example.com', 75.00, true, 'email', 'active', 'user', true, 'privy_user_002'),
    ('user_eve', 'eve@test.example.com', 200.00, true, 'google', 'active', 'user', true, 'privy_user_003'),

    -- Trial users
    ('trial_frank', 'frank@test.example.com', 25.00, true, 'email', 'trial', 'user', true, 'privy_trial_001'),
    ('trial_grace', 'grace@test.example.com', 10.00, true, 'email', 'trial', 'user', true, 'privy_trial_002'),

    -- Low balance user
    ('user_lowbal', 'lowbal@test.example.com', 0.50, true, 'email', 'active', 'user', true, 'privy_lowbal_001'),

    -- Inactive/cancelled user
    ('user_cancelled', 'cancelled@test.example.com', 0.00, false, 'email', 'cancelled', 'user', true, 'privy_cancelled_001')
ON CONFLICT (email) DO NOTHING;

-- Update trial expiration for trial users
UPDATE users
SET trial_expires_at = NOW() + INTERVAL '7 days'
WHERE subscription_status = 'trial';

-- =============================================================================
-- API KEYS
-- =============================================================================
-- Create API keys for test users

-- Get user IDs (we need to reference them)
DO $$
DECLARE
    admin_id bigint;
    dev_alice_id bigint;
    dev_bob_id bigint;
    user_charlie_id bigint;
    user_diana_id bigint;
    trial_frank_id bigint;
BEGIN
    SELECT id INTO admin_id FROM users WHERE username = 'admin_user';
    SELECT id INTO dev_alice_id FROM users WHERE username = 'dev_alice';
    SELECT id INTO dev_bob_id FROM users WHERE username = 'dev_bob';
    SELECT id INTO user_charlie_id FROM users WHERE username = 'user_charlie';
    SELECT id INTO user_diana_id FROM users WHERE username = 'user_diana';
    SELECT id INTO trial_frank_id FROM users WHERE username = 'trial_frank';

    -- Admin API keys
    IF admin_id IS NOT NULL THEN
        INSERT INTO api_keys_new (user_id, api_key, key_hash, key_name, environment_tag, is_primary, is_active, scope_permissions, requests_used, last4)
        VALUES
            (admin_id, 'gw_live_admin_key_00000001', 'hash_admin_live_001', 'Admin Live Key', 'live', true, true, '{"read": ["*"], "write": ["*"]}'::jsonb, 1500, '0001'),
            (admin_id, 'gw_test_admin_key_00000002', 'hash_admin_test_001', 'Admin Test Key', 'test', false, true, '{"read": ["*"], "write": ["*"]}'::jsonb, 500, '0002')
        ON CONFLICT DO NOTHING;
    END IF;

    -- Developer API keys
    IF dev_alice_id IS NOT NULL THEN
        INSERT INTO api_keys_new (user_id, api_key, key_hash, key_name, environment_tag, is_primary, is_active, scope_permissions, requests_used, last4)
        VALUES
            (dev_alice_id, 'gw_live_alice_key_00000001', 'hash_alice_live_001', 'Alice Production', 'live', true, true, '{"read": ["*"], "write": ["chat"]}'::jsonb, 3200, '0001'),
            (dev_alice_id, 'gw_test_alice_key_00000002', 'hash_alice_test_001', 'Alice Development', 'test', false, true, '{"read": ["*"], "write": ["chat"]}'::jsonb, 850, '0002'),
            (dev_alice_id, 'gw_stg_alice_key_00000003', 'hash_alice_stg_001', 'Alice Staging', 'staging', false, true, '{"read": ["*"], "write": ["chat"]}'::jsonb, 120, '0003')
        ON CONFLICT DO NOTHING;
    END IF;

    IF dev_bob_id IS NOT NULL THEN
        INSERT INTO api_keys_new (user_id, api_key, key_hash, key_name, environment_tag, is_primary, is_active, scope_permissions, requests_used, last4)
        VALUES
            (dev_bob_id, 'gw_live_bob_key_000000001', 'hash_bob_live_001', 'Bob Main Key', 'live', true, true, '{"read": ["*"], "write": ["chat"]}'::jsonb, 1800, '0001')
        ON CONFLICT DO NOTHING;
    END IF;

    -- Regular user API keys
    IF user_charlie_id IS NOT NULL THEN
        INSERT INTO api_keys_new (user_id, api_key, key_hash, key_name, environment_tag, is_primary, is_active, scope_permissions, requests_used, last4)
        VALUES
            (user_charlie_id, 'gw_live_charlie_key_0001', 'hash_charlie_001', 'My API Key', 'live', true, true, '{"read": ["*"], "write": ["chat"]}'::jsonb, 450, '0001')
        ON CONFLICT DO NOTHING;
    END IF;

    IF user_diana_id IS NOT NULL THEN
        INSERT INTO api_keys_new (user_id, api_key, key_hash, key_name, environment_tag, is_primary, is_active, scope_permissions, requests_used, last4)
        VALUES
            (user_diana_id, 'gw_live_diana_key_00001', 'hash_diana_001', 'Primary Key', 'live', true, true, '{"read": ["*"], "write": ["chat"]}'::jsonb, 220, '0001')
        ON CONFLICT DO NOTHING;
    END IF;

    -- Trial user API key
    IF trial_frank_id IS NOT NULL THEN
        INSERT INTO api_keys_new (user_id, api_key, key_hash, key_name, environment_tag, is_primary, is_active, scope_permissions, requests_used, last4)
        VALUES
            (trial_frank_id, 'gw_test_frank_key_00001', 'hash_frank_001', 'Trial Key', 'test', true, true, '{"read": ["chat"], "write": ["chat"]}'::jsonb, 50, '0001')
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- =============================================================================
-- PAYMENTS
-- =============================================================================
DO $$
DECLARE
    dev_alice_id bigint;
    dev_bob_id bigint;
    user_charlie_id bigint;
BEGIN
    SELECT id INTO dev_alice_id FROM users WHERE username = 'dev_alice';
    SELECT id INTO dev_bob_id FROM users WHERE username = 'dev_bob';
    SELECT id INTO user_charlie_id FROM users WHERE username = 'user_charlie';

    IF dev_alice_id IS NOT NULL THEN
        INSERT INTO payments (user_id, amount_usd, amount_cents, credits_purchased, bonus_credits, currency, payment_method, status, stripe_payment_intent_id, stripe_checkout_session_id, completed_at)
        VALUES
            (dev_alice_id, 50.00, 5000, 5000, 500, 'usd', 'stripe', 'succeeded', 'pi_test_alice_001', 'cs_test_alice_001', NOW() - INTERVAL '30 days'),
            (dev_alice_id, 100.00, 10000, 10000, 1000, 'usd', 'stripe', 'succeeded', 'pi_test_alice_002', 'cs_test_alice_002', NOW() - INTERVAL '15 days')
        ON CONFLICT DO NOTHING;
    END IF;

    IF dev_bob_id IS NOT NULL THEN
        INSERT INTO payments (user_id, amount_usd, amount_cents, credits_purchased, bonus_credits, currency, payment_method, status, stripe_payment_intent_id, stripe_checkout_session_id, completed_at)
        VALUES
            (dev_bob_id, 20.00, 2000, 2000, 0, 'usd', 'stripe', 'succeeded', 'pi_test_bob_001', 'cs_test_bob_001', NOW() - INTERVAL '45 days')
        ON CONFLICT DO NOTHING;
    END IF;

    IF user_charlie_id IS NOT NULL THEN
        INSERT INTO payments (user_id, amount_usd, amount_cents, credits_purchased, bonus_credits, currency, payment_method, status, stripe_payment_intent_id, stripe_checkout_session_id, completed_at)
        VALUES
            (user_charlie_id, 10.00, 1000, 1000, 0, 'usd', 'stripe', 'succeeded', 'pi_test_charlie_001', 'cs_test_charlie_001', NOW() - INTERVAL '20 days'),
            (user_charlie_id, 5.00, 500, 500, 0, 'usd', 'stripe', 'failed', 'pi_test_charlie_002', 'cs_test_charlie_002', NULL)
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- =============================================================================
-- CREDIT TRANSACTIONS
-- =============================================================================
DO $$
DECLARE
    dev_alice_id bigint;
    user_charlie_id bigint;
BEGIN
    SELECT id INTO dev_alice_id FROM users WHERE username = 'dev_alice';
    SELECT id INTO user_charlie_id FROM users WHERE username = 'user_charlie';

    IF dev_alice_id IS NOT NULL THEN
        INSERT INTO credit_transactions (user_id, amount, transaction_type, description, balance_before, balance_after, created_by)
        VALUES
            (dev_alice_id, 5000.00, 'purchase', 'Credit purchase: $50.00', 0.00, 5000.00, 'stripe'),
            (dev_alice_id, 500.00, 'bonus', 'Purchase bonus (10%)', 5000.00, 5500.00, 'system'),
            (dev_alice_id, -15.50, 'deduction', 'API usage: anthropic/claude-3-opus', 5500.00, 5484.50, 'api'),
            (dev_alice_id, -8.25, 'deduction', 'API usage: openai/gpt-4-turbo', 5484.50, 5476.25, 'api'),
            (dev_alice_id, 10000.00, 'purchase', 'Credit purchase: $100.00', 5476.25, 15476.25, 'stripe'),
            (dev_alice_id, 1000.00, 'bonus', 'Purchase bonus (10%)', 15476.25, 16476.25, 'system')
        ON CONFLICT DO NOTHING;
    END IF;

    IF user_charlie_id IS NOT NULL THEN
        INSERT INTO credit_transactions (user_id, amount, transaction_type, description, balance_before, balance_after, created_by)
        VALUES
            (user_charlie_id, 1000.00, 'purchase', 'Credit purchase: $10.00', 0.00, 1000.00, 'stripe'),
            (user_charlie_id, -2.50, 'deduction', 'API usage: openai/gpt-3.5-turbo', 1000.00, 997.50, 'api'),
            (user_charlie_id, -5.00, 'deduction', 'API usage: anthropic/claude-3-haiku', 997.50, 992.50, 'api')
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- =============================================================================
-- ACTIVITY LOG
-- =============================================================================
DO $$
DECLARE
    dev_alice_id bigint;
    dev_bob_id bigint;
    user_charlie_id bigint;
BEGIN
    SELECT id INTO dev_alice_id FROM users WHERE username = 'dev_alice';
    SELECT id INTO dev_bob_id FROM users WHERE username = 'dev_bob';
    SELECT id INTO user_charlie_id FROM users WHERE username = 'user_charlie';

    IF dev_alice_id IS NOT NULL THEN
        INSERT INTO activity_log (user_id, model, provider, tokens, cost, speed, finish_reason, app, metadata, timestamp)
        VALUES
            (dev_alice_id, 'anthropic/claude-3-opus', 'openrouter', 5000, 15.50, 45.2, 'stop', 'Code Assistant', '{"prompt_tokens": 1500, "completion_tokens": 3500}'::jsonb, NOW() - INTERVAL '2 days'),
            (dev_alice_id, 'openai/gpt-4-turbo', 'openrouter', 3000, 8.25, 52.1, 'stop', 'Code Assistant', '{"prompt_tokens": 1000, "completion_tokens": 2000}'::jsonb, NOW() - INTERVAL '1 day'),
            (dev_alice_id, 'anthropic/claude-3-sonnet', 'openrouter', 2500, 5.00, 68.3, 'stop', 'Writing Helper', '{"prompt_tokens": 800, "completion_tokens": 1700}'::jsonb, NOW() - INTERVAL '12 hours'),
            (dev_alice_id, 'openai/gpt-4o', 'openrouter', 4000, 6.00, 75.0, 'stop', 'API Gateway', '{"prompt_tokens": 1200, "completion_tokens": 2800}'::jsonb, NOW() - INTERVAL '6 hours')
        ON CONFLICT DO NOTHING;
    END IF;

    IF dev_bob_id IS NOT NULL THEN
        INSERT INTO activity_log (user_id, model, provider, tokens, cost, speed, finish_reason, app, metadata, timestamp)
        VALUES
            (dev_bob_id, 'openai/gpt-3.5-turbo', 'openrouter', 1500, 0.75, 120.5, 'stop', 'ChatGPT Clone', '{"prompt_tokens": 500, "completion_tokens": 1000}'::jsonb, NOW() - INTERVAL '3 days'),
            (dev_bob_id, 'meta-llama/llama-3-70b-instruct', 'together', 2000, 1.80, 85.2, 'length', 'Research Tool', '{"prompt_tokens": 700, "completion_tokens": 1300}'::jsonb, NOW() - INTERVAL '1 day')
        ON CONFLICT DO NOTHING;
    END IF;

    IF user_charlie_id IS NOT NULL THEN
        INSERT INTO activity_log (user_id, model, provider, tokens, cost, speed, finish_reason, app, metadata, timestamp)
        VALUES
            (user_charlie_id, 'openai/gpt-3.5-turbo', 'openrouter', 800, 0.40, 135.0, 'stop', 'ChatGPT Clone', '{"prompt_tokens": 300, "completion_tokens": 500}'::jsonb, NOW() - INTERVAL '5 days'),
            (user_charlie_id, 'anthropic/claude-3-haiku', 'openrouter', 1200, 0.30, 150.0, 'stop', 'Writing Helper', '{"prompt_tokens": 400, "completion_tokens": 800}'::jsonb, NOW() - INTERVAL '2 days')
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- =============================================================================
-- CHAT SESSIONS AND MESSAGES
-- =============================================================================
DO $$
DECLARE
    dev_alice_id bigint;
    user_charlie_id bigint;
    session_id_1 integer;
    session_id_2 integer;
BEGIN
    SELECT id INTO dev_alice_id FROM users WHERE username = 'dev_alice';
    SELECT id INTO user_charlie_id FROM users WHERE username = 'user_charlie';

    -- Create sessions for Alice
    IF dev_alice_id IS NOT NULL THEN
        INSERT INTO chat_sessions (user_id, title, model, is_active)
        VALUES (dev_alice_id, 'Python Code Review', 'anthropic/claude-3-opus', true)
        RETURNING id INTO session_id_1;

        INSERT INTO chat_messages (session_id, role, content, model, tokens)
        VALUES
            (session_id_1, 'user', 'Can you review this Python function for me?', NULL, 15),
            (session_id_1, 'assistant', 'Of course! Please share the code and I''ll provide a detailed review with suggestions for improvement.', 'anthropic/claude-3-opus', 25),
            (session_id_1, 'user', 'def calculate_sum(numbers): return sum(numbers)', NULL, 12),
            (session_id_1, 'assistant', 'The function is simple and works, but here are some suggestions: 1) Add type hints, 2) Add a docstring, 3) Consider input validation.', 'anthropic/claude-3-opus', 45);

        INSERT INTO chat_sessions (user_id, title, model, is_active)
        VALUES (dev_alice_id, 'API Integration Help', 'openai/gpt-4-turbo', true)
        RETURNING id INTO session_id_2;

        INSERT INTO chat_messages (session_id, role, content, model, tokens)
        VALUES
            (session_id_2, 'user', 'How do I handle rate limiting in my API client?', NULL, 12),
            (session_id_2, 'assistant', 'Great question! Here are the best practices for handling rate limiting: 1) Implement exponential backoff, 2) Track rate limit headers, 3) Use a token bucket algorithm for client-side limiting.', 'openai/gpt-4-turbo', 55);
    END IF;

    -- Create sessions for Charlie
    IF user_charlie_id IS NOT NULL THEN
        INSERT INTO chat_sessions (user_id, title, model, is_active)
        VALUES (user_charlie_id, 'Writing assistance', 'openai/gpt-3.5-turbo', true)
        RETURNING id INTO session_id_1;

        INSERT INTO chat_messages (session_id, role, content, model, tokens)
        VALUES
            (session_id_1, 'user', 'Help me write an email to my team about the project update.', NULL, 18),
            (session_id_1, 'assistant', 'I''d be happy to help! Here''s a professional email template for your project update: [Subject: Project Update - Week of ...]. Would you like me to customize it further?', 'openai/gpt-3.5-turbo', 42);
    END IF;
END $$;

-- =============================================================================
-- COUPONS
-- =============================================================================
INSERT INTO coupons (code, value_usd, coupon_scope, max_uses, times_used, valid_from, valid_until, description, coupon_type, is_active)
VALUES
    ('WELCOME10', 10.00, 'global', 100, 15, NOW() - INTERVAL '30 days', NOW() + INTERVAL '60 days', 'Welcome bonus - $10 credits', 'promotional', true),
    ('TESTCODE50', 50.00, 'global', 50, 5, NOW() - INTERVAL '15 days', NOW() + INTERVAL '90 days', 'Test promotional code - $50', 'promotional', true),
    ('DEVBONUS25', 25.00, 'global', 20, 0, NOW(), NOW() + INTERVAL '30 days', 'Developer bonus credits', 'promotional', true),
    ('PARTNER100', 100.00, 'global', 10, 2, NOW() - INTERVAL '7 days', NOW() + INTERVAL '180 days', 'Partnership bonus', 'partnership', true),
    ('EXPIRED5', 5.00, 'global', 100, 50, NOW() - INTERVAL '90 days', NOW() - INTERVAL '30 days', 'Expired test coupon', 'promotional', false)
ON CONFLICT (code) DO NOTHING;

-- =============================================================================
-- MODEL HEALTH TRACKING
-- =============================================================================
INSERT INTO model_health_tracking (provider, model, last_response_time_ms, last_status, call_count, success_count, error_count, average_response_time_ms)
VALUES
    ('openrouter', 'openai/gpt-4-turbo', 1250, 'success', 15000, 14850, 150, 1180),
    ('openrouter', 'openai/gpt-4o', 980, 'success', 25000, 24800, 200, 920),
    ('openrouter', 'openai/gpt-3.5-turbo', 450, 'success', 50000, 49500, 500, 420),
    ('openrouter', 'anthropic/claude-3-opus', 2100, 'success', 8000, 7900, 100, 2050),
    ('openrouter', 'anthropic/claude-3-sonnet', 1100, 'success', 12000, 11900, 100, 1050),
    ('openrouter', 'anthropic/claude-3-haiku', 380, 'success', 30000, 29800, 200, 350),
    ('together', 'meta-llama/llama-3-70b-instruct', 850, 'success', 5000, 4900, 100, 820),
    ('deepinfra', 'mistralai/mistral-large', 720, 'success', 3000, 2950, 50, 700)
ON CONFLICT (provider, model) DO UPDATE SET
    last_response_time_ms = EXCLUDED.last_response_time_ms,
    last_status = EXCLUDED.last_status,
    call_count = EXCLUDED.call_count,
    success_count = EXCLUDED.success_count,
    error_count = EXCLUDED.error_count,
    average_response_time_ms = EXCLUDED.average_response_time_ms,
    last_called_at = NOW();

-- =============================================================================
-- SUMMARY
-- =============================================================================
DO $$
DECLARE
    user_count integer;
    api_key_count integer;
    payment_count integer;
    activity_count integer;
    session_count integer;
    coupon_count integer;
BEGIN
    SELECT COUNT(*) INTO user_count FROM users;
    SELECT COUNT(*) INTO api_key_count FROM api_keys_new;
    SELECT COUNT(*) INTO payment_count FROM payments;
    SELECT COUNT(*) INTO activity_count FROM activity_log;
    SELECT COUNT(*) INTO session_count FROM chat_sessions;
    SELECT COUNT(*) INTO coupon_count FROM coupons;

    RAISE NOTICE '';
    RAISE NOTICE '============================================';
    RAISE NOTICE '  Test Data Seeding Complete!';
    RAISE NOTICE '============================================';
    RAISE NOTICE 'Created:';
    RAISE NOTICE '  - % users', user_count;
    RAISE NOTICE '  - % API keys', api_key_count;
    RAISE NOTICE '  - % payments', payment_count;
    RAISE NOTICE '  - % activity logs', activity_count;
    RAISE NOTICE '  - % chat sessions', session_count;
    RAISE NOTICE '  - % coupons', coupon_count;
    RAISE NOTICE '';
    RAISE NOTICE 'Sample Credentials:';
    RAISE NOTICE '  Admin: admin@test.example.com';
    RAISE NOTICE '  API Key: gw_live_admin_key_00000001';
    RAISE NOTICE '';
    RAISE NOTICE '  Developer: alice@test.example.com';
    RAISE NOTICE '  API Key: gw_live_alice_key_00000001';
    RAISE NOTICE '============================================';
END $$;
