-- Add allowance_per_month column to subscription_products
-- This tracks the monthly usage allowance for each subscription tier

ALTER TABLE subscription_products
ADD COLUMN IF NOT EXISTS allowance_per_month DECIMAL(10,2) DEFAULT 0;

COMMENT ON COLUMN subscription_products.allowance_per_month IS 'Monthly subscription allowance in USD - usage allowance granted to subscribers';

-- Update existing products with new allowance values
-- PRO: $10/month payment -> $15 monthly allowance (50% bonus)
-- MAX: $75/month payment -> $150 monthly allowance (100% bonus)

UPDATE subscription_products
SET allowance_per_month = 15.00
WHERE tier = 'pro' AND is_active = TRUE;

UPDATE subscription_products
SET allowance_per_month = 150.00
WHERE tier = 'max' AND is_active = TRUE;

-- Log the updates
DO $$
BEGIN
    RAISE NOTICE 'Updated PRO tier allowance to $15/month';
    RAISE NOTICE 'Updated MAX tier allowance to $150/month';
END $$;
