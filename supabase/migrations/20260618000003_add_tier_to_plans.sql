-- Add a `tier` code column to the plans table and backfill it for paid plans.
--
-- Why: subscription_products.tier uses short codes ('basic', 'pro', 'max'),
-- but plans.name holds display names ('Starter', 'Professional', 'Business',
-- etc). get_plan_id_by_tier() previously fuzzy-matched the tier code against
-- plans.name via ILIKE '%tier%'. Only 'pro' matched by luck (substring of
-- 'Professional'); 'basic' and 'max' never matched, so paying subscribers on
-- those tiers never got a user_plans entitlement row and silently fell back to
-- default trial rate limits despite being billed correctly.
--
-- This adds an explicit tier code column so the lookup can match exactly
-- (.eq("tier", tier)) instead of relying on display-name substrings.
--
-- Free, Free Trial, Enterprise, and Admin have no subscription tier code and
-- are intentionally left with tier = NULL.

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS tier TEXT;

UPDATE public.plans SET tier = 'basic', price_per_month = 35  WHERE name = 'Starter';
UPDATE public.plans SET tier = 'pro',   price_per_month = 120 WHERE name = 'Professional';
UPDATE public.plans SET tier = 'max',   price_per_month = 350 WHERE name = 'Business';
