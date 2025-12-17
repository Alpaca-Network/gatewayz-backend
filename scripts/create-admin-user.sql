-- ============================================================================
-- Create Admin User with 1000 Credits
-- ============================================================================
-- This script creates a complete admin user setup with:
-- 1. User account with 1000 credits
-- 2. Admin privileges
-- 3. API key
-- 4. Returns all user details
-- ============================================================================

DO $$
DECLARE
    v_user_id BIGINT;
    v_admin_id UUID;
    v_api_key_id BIGINT;
    v_api_key TEXT;
    v_email TEXT := 'admin@gatewayz.local';  -- Change this!
    v_username TEXT := 'admin';               -- Change this!
    v_password TEXT := 'admin123';            -- Change this!
    v_privy_user_id TEXT := 'admin_' || (EXTRACT(EPOCH FROM NOW())::BIGINT);
BEGIN
    -- ========================================================================
    -- Step 1: Create User with 1000 Credits
    -- ========================================================================
    INSERT INTO public.users (
        username,
        email,
        credits,
        is_active,
        auth_method,
        subscription_status,
        privy_user_id,
        created_at,
        updated_at
    ) VALUES (
        v_username,
        v_email,
        1000.00,  -- 1000 credits
        true,
        'email',
        'active',
        v_privy_user_id,
        NOW(),
        NOW()
    )
    RETURNING id INTO v_user_id;

    RAISE NOTICE '✅ User created with ID: %', v_user_id;

    -- ========================================================================
    -- Step 2: Create Admin Entry
    -- ========================================================================
    INSERT INTO public.admin_users (
        email,
        password,
        role,
        status,
        created_at,
        updated_at
    ) VALUES (
        v_email,
        v_password,  -- In production, this should be hashed!
        'superadmin',
        'active',
        NOW(),
        NOW()
    )
    RETURNING id INTO v_admin_id;

    RAISE NOTICE '✅ Admin user created with ID: %', v_admin_id;

    -- ========================================================================
    -- Step 3: Generate API Key
    -- ========================================================================
    -- Generate a secure API key (format: gw_live_<random>)
    v_api_key := 'gw_live_' || encode(gen_random_bytes(24), 'base64');
    v_api_key := replace(v_api_key, '/', '_');
    v_api_key := replace(v_api_key, '+', '-');

    INSERT INTO public.api_keys_new (
        user_id,
        api_key,
        key_name,
        environment_tag,
        is_primary,
        is_active,
        scope_permissions,
        created_at,
        updated_at
    ) VALUES (
        v_user_id,
        v_api_key,
        'Primary Admin Key',
        'live',
        true,
        true,
        '{
            "chat": ["*"],
            "models": ["*"],
            "images": ["*"],
            "admin": ["*"]
        }'::jsonb,
        NOW(),
        NOW()
    )
    RETURNING id INTO v_api_key_id;

    RAISE NOTICE '✅ API key created with ID: %', v_api_key_id;

    -- ========================================================================
    -- Step 4: Display Results
    -- ========================================================================
    RAISE NOTICE '';
    RAISE NOTICE '════════════════════════════════════════════════════════════════';
    RAISE NOTICE '🎉 ADMIN USER CREATED SUCCESSFULLY!';
    RAISE NOTICE '════════════════════════════════════════════════════════════════';
    RAISE NOTICE '';
    RAISE NOTICE '👤 USER DETAILS:';
    RAISE NOTICE '   User ID:       %', v_user_id;
    RAISE NOTICE '   Username:      %', v_username;
    RAISE NOTICE '   Email:         %', v_email;
    RAISE NOTICE '   Credits:       1000.00';
    RAISE NOTICE '   Status:        active';
    RAISE NOTICE '';
    RAISE NOTICE '🔑 ADMIN CREDENTIALS:';
    RAISE NOTICE '   Admin ID:      %', v_admin_id;
    RAISE NOTICE '   Role:          superadmin';
    RAISE NOTICE '   Password:      %', v_password;
    RAISE NOTICE '';
    RAISE NOTICE '🔐 API KEY:';
    RAISE NOTICE '   API Key ID:    %', v_api_key_id;
    RAISE NOTICE '   API Key:       %', v_api_key;
    RAISE NOTICE '   Environment:   live';
    RAISE NOTICE '   Permissions:   Full Access (*)';
    RAISE NOTICE '';
    RAISE NOTICE '════════════════════════════════════════════════════════════════';
    RAISE NOTICE '';
    RAISE NOTICE '📋 USAGE:';
    RAISE NOTICE '';
    RAISE NOTICE 'API Request Example:';
    RAISE NOTICE '  curl -X POST http://localhost:8000/v1/chat/completions \';
    RAISE NOTICE '    -H "Authorization: Bearer %" \', v_api_key;
    RAISE NOTICE '    -H "Content-Type: application/json" \';
    RAISE NOTICE '    -d ''{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello!"}]}''';
    RAISE NOTICE '';
    RAISE NOTICE '════════════════════════════════════════════════════════════════';
    RAISE NOTICE '';

    -- Also return as a result set for programmatic access
    RETURN QUERY
    SELECT
        v_user_id AS user_id,
        v_username AS username,
        v_email AS email,
        1000.00 AS credits,
        v_admin_id::TEXT AS admin_id,
        'superadmin'::TEXT AS role,
        v_api_key AS api_key,
        'live'::TEXT AS environment;

EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'User with email "%" already exists!', v_email;
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Error creating admin user: %', SQLERRM;
END $$;
