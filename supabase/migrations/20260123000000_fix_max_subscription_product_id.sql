-- Fix MAX subscription product ID mismatch
-- The original migration used prod_TKOqRE2L6qXu7s for MAX tier,
-- but the frontend/Stripe uses prod_TKOraBpWMxMAIu
-- This migration adds the correct product ID mapping

-- Ensure subscription_products table exists (may have been dropped by remote_schema migration)
CREATE TABLE IF NOT EXISTS subscription_products (
    product_id VARCHAR(255) PRIMARY KEY,
    tier VARCHAR(50) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    credits_per_month DECIMAL(10,2) NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes if they don't exist
CREATE INDEX IF NOT EXISTS idx_subscription_products_tier ON subscription_products(tier);
CREATE INDEX IF NOT EXISTS idx_subscription_products_is_active ON subscription_products(is_active);

-- Create or replace updated_at trigger function
CREATE OR REPLACE FUNCTION update_subscription_products_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop trigger if exists and recreate
DROP TRIGGER IF EXISTS trigger_update_subscription_products_updated_at ON subscription_products;
CREATE TRIGGER trigger_update_subscription_products_updated_at
    BEFORE UPDATE ON subscription_products
    FOR EACH ROW
    EXECUTE FUNCTION update_subscription_products_updated_at();

-- Ensure PRO tier product exists (may have been lost)
INSERT INTO subscription_products (product_id, tier, display_name, credits_per_month, description, is_active)
VALUES
    ('prod_TKOqQPhVRxNp4Q', 'pro', 'Pro', 20.00, 'Professional tier with $20 monthly credits', TRUE)
ON CONFLICT (product_id) DO NOTHING;

-- Add the correct MAX product ID that's actually used in production
-- The $75/month subscription gives $150 worth of credits (50% discount)
INSERT INTO subscription_products (product_id, tier, display_name, credits_per_month, description, is_active)
VALUES
    ('prod_TKOraBpWMxMAIu', 'max', 'MAX', 150.00, 'Maximum tier with $150 monthly credits (50% discount on $75/month subscription)', TRUE)
ON CONFLICT (product_id) DO UPDATE SET
    tier = EXCLUDED.tier,
    display_name = EXCLUDED.display_name,
    credits_per_month = EXCLUDED.credits_per_month,
    description = EXCLUDED.description,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- Keep the old product ID mapping for backward compatibility, but mark it as inactive
-- in case any legacy users have it
UPDATE subscription_products
SET is_active = FALSE,
    description = 'DEPRECATED: Old MAX product ID - use prod_TKOraBpWMxMAIu instead',
    updated_at = NOW()
WHERE product_id = 'prod_TKOqRE2L6qXu7s'
  AND tier = 'max';

-- Verify the changes
DO $$
DECLARE
    correct_product_count INTEGER;
    max_tier_credits DECIMAL(10,2);
BEGIN
    -- Check that the correct product ID exists
    SELECT COUNT(*) INTO correct_product_count
    FROM subscription_products
    WHERE product_id = 'prod_TKOraBpWMxMAIu'
      AND tier = 'max'
      AND is_active = TRUE;

    IF correct_product_count != 1 THEN
        RAISE EXCEPTION 'Migration failed: prod_TKOraBpWMxMAIu not properly configured for MAX tier';
    END IF;

    -- Check that MAX tier gives 150 credits
    -- Use ORDER BY created_at DESC to get the most recently created active record
    SELECT credits_per_month INTO max_tier_credits
    FROM subscription_products
    WHERE tier = 'max' AND is_active = TRUE
    ORDER BY created_at DESC
    LIMIT 1;

    IF max_tier_credits != 150.00 THEN
        RAISE EXCEPTION 'Migration failed: MAX tier should give 150 credits, got %', max_tier_credits;
    END IF;

    RAISE NOTICE 'Migration successful: MAX product ID prod_TKOraBpWMxMAIu now configured with 150 credits/month';
END $$;

-- Add comment explaining the product IDs
COMMENT ON TABLE subscription_products IS 'Configuration for Stripe subscription products and tier mapping. Product IDs must match Stripe Dashboard: Pro=prod_TKOqQPhVRxNp4Q, Max=prod_TKOraBpWMxMAIu';
