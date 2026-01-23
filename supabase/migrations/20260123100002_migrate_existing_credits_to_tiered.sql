-- Migrate existing credits to the tiered system
--
-- Strategy:
-- 1. All existing credits become purchased_credits (they were earned/purchased before this system)
-- 2. Active PRO/MAX subscribers get their initial subscription_allowance
-- 3. Log all migrations in credit_transactions for audit trail

-- Step 1: Copy existing credits to purchased_credits for all users who have credits
-- Only update users where purchased_credits hasn't been set yet (idempotent)
UPDATE users
SET
    purchased_credits = COALESCE(credits, 0),
    updated_at = NOW()
WHERE
    (purchased_credits IS NULL OR purchased_credits = 0)
    AND COALESCE(credits, 0) > 0;

-- Step 2: Set initial subscription allowance for active subscribers based on tier
-- PRO tier gets $15, MAX tier gets $150
UPDATE users
SET
    subscription_allowance = CASE
        WHEN tier = 'pro' THEN 15.00
        WHEN tier = 'max' THEN 150.00
        ELSE 0
    END,
    allowance_reset_date = NOW(),
    updated_at = NOW()
WHERE
    subscription_status = 'active'
    AND tier IN ('pro', 'max')
    AND (subscription_allowance IS NULL OR subscription_allowance = 0);

-- Step 3: Log the migration in credit_transactions for audit trail
-- This creates a record showing each user's migration to the tiered system
-- Only insert if user doesn't already have a tiered_credits_v1 migration record (idempotent)
INSERT INTO credit_transactions (
    user_id,
    amount,
    transaction_type,
    description,
    balance_before,
    balance_after,
    metadata,
    created_at
)
SELECT
    id as user_id,
    0 as amount,  -- No actual credit change, just a system record
    'system_migration' as transaction_type,
    'Credits migrated to tiered subscription system' as description,
    COALESCE(credits, 0) as balance_before,
    COALESCE(purchased_credits, 0) + COALESCE(subscription_allowance, 0) as balance_after,
    jsonb_build_object(
        'migration_name', 'tiered_credits_v1',
        'migration_date', NOW(),
        'original_credits', COALESCE(credits, 0),
        'new_purchased_credits', COALESCE(purchased_credits, 0),
        'new_subscription_allowance', COALESCE(subscription_allowance, 0),
        'tier', tier,
        'subscription_status', subscription_status
    ) as metadata,
    NOW() as created_at
FROM users u
WHERE
    (COALESCE(credits, 0) > 0
    OR (subscription_status = 'active' AND tier IN ('pro', 'max')))
    -- Idempotency: only insert if no tiered_credits_v1 migration record exists
    AND NOT EXISTS (
        SELECT 1 FROM credit_transactions ct
        WHERE ct.user_id = u.id
        AND ct.transaction_type = 'system_migration'
        AND ct.metadata->>'migration_name' = 'tiered_credits_v1'
    );

-- Output migration summary
DO $$
DECLARE
    users_with_purchased_credits INTEGER;
    pro_subscribers INTEGER;
    max_subscribers INTEGER;
BEGIN
    SELECT COUNT(*) INTO users_with_purchased_credits
    FROM users WHERE purchased_credits > 0;

    SELECT COUNT(*) INTO pro_subscribers
    FROM users WHERE tier = 'pro' AND subscription_status = 'active' AND subscription_allowance > 0;

    SELECT COUNT(*) INTO max_subscribers
    FROM users WHERE tier = 'max' AND subscription_status = 'active' AND subscription_allowance > 0;

    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  Users with purchased_credits: %', users_with_purchased_credits;
    RAISE NOTICE '  PRO subscribers with allowance: %', pro_subscribers;
    RAISE NOTICE '  MAX subscribers with allowance: %', max_subscribers;
END $$;
