-- MVP refactor Task 5: drop coupons + referrals subsystem tables.
-- STAGING ONLY. Do NOT apply to production. See docs/NORTH_STAR.md §5 Non-Goals.

DROP TABLE IF EXISTS coupons CASCADE;
DROP TABLE IF EXISTS coupon_redemptions CASCADE;
DROP TABLE IF EXISTS referrals CASCADE;
