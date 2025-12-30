-- Clear trial status for users who are Pro/Max tier or have purchased credits
-- This migration ensures users who have paid are no longer marked as trial users

-- First, log the current state before migration
DO $$
DECLARE
    pro_max_count INTEGER;
    subscription_count INTEGER;
    credit_purchase_count INTEGER;
BEGIN
    -- Count Pro/Max users still marked as trial
    SELECT COUNT(DISTINCT ak.user_id) INTO pro_max_count
    FROM api_keys_new ak
    JOIN users u ON ak.user_id = u.id
    WHERE u.tier IN ('pro', 'max')
      AND ak.is_trial = TRUE;

    -- Count users with active subscriptions still marked as trial
    SELECT COUNT(DISTINCT ak.user_id) INTO subscription_count
    FROM api_keys_new ak
    JOIN users u ON ak.user_id = u.id
    WHERE u.stripe_subscription_id IS NOT NULL
      AND u.subscription_status = 'active'
      AND ak.is_trial = TRUE;

    -- Count users with credit purchases still marked as trial
    SELECT COUNT(DISTINCT ak.user_id) INTO credit_purchase_count
    FROM api_keys_new ak
    JOIN users u ON ak.user_id = u.id
    WHERE EXISTS (
        SELECT 1 FROM credit_transactions ct
        WHERE ct.user_id = u.id
        AND ct.transaction_type = 'purchase'
    )
    AND ak.is_trial = TRUE;

    RAISE NOTICE 'Pre-migration counts:';
    RAISE NOTICE '  Pro/Max tier users with trial API keys: %', pro_max_count;
    RAISE NOTICE '  Active subscription users with trial API keys: %', subscription_count;
    RAISE NOTICE '  Credit purchasers with trial API keys: %', credit_purchase_count;
END $$;

-- 1. Clear trial status for Pro/Max tier users
UPDATE api_keys_new
SET
    is_trial = FALSE,
    trial_converted = TRUE,
    subscription_status = 'active',
    subscription_plan = u.tier,
    updated_at = NOW()
FROM users u
WHERE api_keys_new.user_id = u.id
    AND u.tier IN ('pro', 'max')
    AND api_keys_new.is_trial = TRUE;

DO $$
DECLARE
    affected_count INTEGER;
BEGIN
    GET DIAGNOSTICS affected_count = ROW_COUNT;
    RAISE NOTICE 'Cleared trial status for % API keys belonging to Pro/Max tier users', affected_count;
END $$;

-- 2. Clear trial status for users with active Stripe subscriptions
UPDATE api_keys_new
SET
    is_trial = FALSE,
    trial_converted = TRUE,
    subscription_status = 'active',
    subscription_plan = COALESCE(u.tier, 'pro'),
    updated_at = NOW()
FROM users u
WHERE api_keys_new.user_id = u.id
    AND u.stripe_subscription_id IS NOT NULL
    AND u.subscription_status = 'active'
    AND api_keys_new.is_trial = TRUE;

DO $$
DECLARE
    affected_count INTEGER;
BEGIN
    GET DIAGNOSTICS affected_count = ROW_COUNT;
    RAISE NOTICE 'Cleared trial status for % API keys belonging to users with active subscriptions', affected_count;
END $$;

-- 3. Clear trial status for users who have purchased credits
-- This catches users who bought credits but didn't subscribe
UPDATE api_keys_new
SET
    is_trial = FALSE,
    trial_converted = TRUE,
    subscription_status = 'active',
    subscription_plan = COALESCE(u.tier, 'basic'),
    updated_at = NOW()
FROM users u
WHERE api_keys_new.user_id = u.id
    AND api_keys_new.is_trial = TRUE
    AND EXISTS (
        SELECT 1 FROM credit_transactions ct
        WHERE ct.user_id = u.id
        AND ct.transaction_type = 'purchase'
    );

DO $$
DECLARE
    affected_count INTEGER;
BEGIN
    GET DIAGNOSTICS affected_count = ROW_COUNT;
    RAISE NOTICE 'Cleared trial status for % API keys belonging to users who purchased credits', affected_count;
END $$;

-- 4. Also update the users table subscription_status for consistency
UPDATE users
SET
    subscription_status = 'active',
    updated_at = NOW()
WHERE subscription_status = 'trial'
    AND (
        tier IN ('pro', 'max')
        OR stripe_subscription_id IS NOT NULL
        OR EXISTS (
            SELECT 1 FROM credit_transactions ct
            WHERE ct.user_id = users.id
            AND ct.transaction_type = 'purchase'
        )
    );

DO $$
DECLARE
    affected_count INTEGER;
BEGIN
    GET DIAGNOSTICS affected_count = ROW_COUNT;
    RAISE NOTICE 'Updated subscription_status for % users in users table', affected_count;
END $$;

-- Final verification
DO $$
DECLARE
    remaining_trial_paid INTEGER;
BEGIN
    SELECT COUNT(DISTINCT ak.user_id) INTO remaining_trial_paid
    FROM api_keys_new ak
    JOIN users u ON ak.user_id = u.id
    WHERE ak.is_trial = TRUE
      AND (
          u.tier IN ('pro', 'max')
          OR u.stripe_subscription_id IS NOT NULL
          OR EXISTS (
              SELECT 1 FROM credit_transactions ct
              WHERE ct.user_id = u.id
              AND ct.transaction_type = 'purchase'
          )
      );

    IF remaining_trial_paid > 0 THEN
        RAISE WARNING 'There are still % paid users with trial API keys - manual review recommended', remaining_trial_paid;
    ELSE
        RAISE NOTICE 'Migration complete: All paid users have had their trial status cleared';
    END IF;
END $$;
