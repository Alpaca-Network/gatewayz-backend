-- Migration: Fix max tier product ID in subscription_products table
--
-- Issue: The frontend uses product ID 'prod_TMHUXL8p0onwwO' for max tier,
-- but the database has 'prod_TKOqRE2L6qXu7s'. This causes tier resolution
-- to fail when processing Stripe webhooks.
--
-- Fix: Add the correct product ID 'prod_TMHUXL8p0onwwO' to subscription_products
-- and update any users who subscribed with this product ID to max tier.

BEGIN;

-- Step 1: Insert the correct max tier product ID if it doesn't exist
INSERT INTO subscription_products (product_id, tier, display_name, credits_per_month, description, is_active)
VALUES ('prod_TMHUXL8p0onwwO', 'max', 'MAX', 150.00, 'Maximum tier with $150 monthly credits', TRUE)
ON CONFLICT (product_id) DO UPDATE SET
    tier = 'max',
    display_name = 'MAX',
    credits_per_month = 150.00,
    is_active = TRUE;

-- Step 2: Log current state before fix
DO $$
DECLARE
    affected_count INTEGER;
BEGIN
    -- Count users with mismatched tier (have max product ID but not max tier)
    SELECT COUNT(*) INTO affected_count
    FROM users
    WHERE stripe_product_id = 'prod_TMHUXL8p0onwwO'
      AND (tier IS NULL OR tier != 'max');

    RAISE NOTICE 'Users with max product ID but incorrect tier: %', affected_count;
END $$;

-- Step 3: Fix users who subscribed with the max product ID but have wrong tier
UPDATE users
SET
    tier = 'max',
    subscription_status = 'active',
    updated_at = NOW()
WHERE stripe_product_id = 'prod_TMHUXL8p0onwwO'
  AND (tier IS NULL OR tier != 'max');

-- Step 4: Also clear trial status for these users' API keys
UPDATE api_keys_new
SET
    is_trial = FALSE,
    trial_converted = TRUE,
    subscription_status = 'active',
    subscription_plan = 'max'
WHERE user_id IN (
    SELECT id FROM users WHERE stripe_product_id = 'prod_TMHUXL8p0onwwO'
)
AND (is_trial = TRUE OR subscription_status != 'active');

-- Step 5: Log the fix result
DO $$
DECLARE
    fixed_users INTEGER;
    fixed_keys INTEGER;
BEGIN
    -- Count fixed users
    SELECT COUNT(*) INTO fixed_users
    FROM users
    WHERE stripe_product_id = 'prod_TMHUXL8p0onwwO'
      AND tier = 'max';

    RAISE NOTICE 'Users now correctly set to max tier: %', fixed_users;

    -- Count fixed API keys
    SELECT COUNT(*) INTO fixed_keys
    FROM api_keys_new ak
    JOIN users u ON ak.user_id = u.id
    WHERE u.stripe_product_id = 'prod_TMHUXL8p0onwwO'
      AND ak.subscription_plan = 'max';

    RAISE NOTICE 'API keys now correctly set to max plan: %', fixed_keys;
END $$;

-- Verify both product IDs exist for max tier (old and new)
DO $$
DECLARE
    product_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO product_count
    FROM subscription_products
    WHERE tier = 'max';

    RAISE NOTICE 'Total max tier products in subscription_products: %', product_count;
END $$;

COMMIT;
