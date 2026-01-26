-- Update Pro tier pricing to $8/month
-- Previous: $10/month payment -> $15 monthly allowance (50% bonus)
-- New: $8/month payment -> $15 monthly allowance (87.5% bonus - "Save 20%")
-- Note: The new Stripe Price ID for Pro at $8/month must be created in Stripe Dashboard
-- and set via NEXT_PUBLIC_STRIPE_PRO_PRICE_ID environment variable on frontend

-- ==================== UP MIGRATION ====================

-- Update Pro tier description to reflect new pricing (idempotent)
UPDATE subscription_products
SET description = 'Professional tier - $8/month with $15 monthly allowance (Save 20%)'
WHERE tier = 'pro'
  AND is_active = TRUE
  AND description != 'Professional tier - $8/month with $15 monthly allowance (Save 20%)';

-- Log the update
DO $$
BEGIN
    RAISE NOTICE 'Updated PRO tier description to reflect $8/month pricing';
    RAISE NOTICE 'IMPORTANT: Create new Stripe Price at $8/month and update NEXT_PUBLIC_STRIPE_PRO_PRICE_ID';
END $$;

-- Add comment explaining the pricing tiers
COMMENT ON TABLE subscription_products IS 'Configuration for Stripe subscription products and tier mapping. Product IDs must match Stripe Dashboard: Pro=prod_TKOqQPhVRxNp4Q ($8/month), Max=prod_TKOraBpWMxMAIu ($75/month)';

-- ==================== DOWN MIGRATION ====================
-- To rollback, run the following SQL manually:
--
-- UPDATE subscription_products
-- SET description = 'Professional tier - $10/month with $15 monthly allowance'
-- WHERE tier = 'pro'
--   AND is_active = TRUE;
--
-- COMMENT ON TABLE subscription_products IS 'Configuration for Stripe subscription products and tier mapping. Product IDs must match Stripe Dashboard: Pro=prod_TKOqQPhVRxNp4Q ($10/month), Max=prod_TKOraBpWMxMAIu ($75/month)';
