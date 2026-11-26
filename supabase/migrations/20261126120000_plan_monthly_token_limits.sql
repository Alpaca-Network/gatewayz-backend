-- Paid plans had monthly_token_limit equal to daily_token_limit (Starter 1M a
-- day and 1M a month, Business 25M and 25M, ...), so the monthly cap was the
-- one that bound, after a single full day of use. Set each paid plan's monthly
-- cap to 30 days of its daily cap, kept inside the integer column.
--
-- Only rows still in the broken state are touched, so re-running is a no-op
-- and a plan someone has since tuned by hand is left alone.
UPDATE plans
SET monthly_token_limit = LEAST(daily_token_limit::bigint * 30, 2000000000)::integer
WHERE price_per_month > 0
  AND monthly_token_limit = daily_token_limit;
